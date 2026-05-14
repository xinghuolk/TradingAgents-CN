# -*- coding: utf-8 -*-
"""
价值投资分析工具

提供给 LangChain Agent 使用的价值投资分析工具
包含穿透回报率、现金健康度、5维评分等功能

修复说明:
- C1: 使用 AKShare 直接获取结构化数据，避免字符串解析
- C2: 移除 _run_async()，使用同步调用避免事件循环冲突
- C3: 添加股票代码输入验证
- W1: 使用 AKShare 动态获取行业信息
"""

import os
import re
import time
from typing import Dict, Any, Optional, List
from langchain_core.tools import tool

from tradingagents.utils.logging_init import get_logger
from tradingagents.services.report_collector_config import (
    get_report_collector_config,
)
from tradingagents.dataflows.financial_reports import (
    apply_financial_report_client_data,
    format_value_report_source_note,
)
from tradingagents.dataflows.value_investment import (
    DividendFetcher,
    BuybackFetcher,
    PenetratingYieldCalculator,
    CashHealthCalculator,
    HealthScoreCalculator,
    generate_health_flags,
    get_conservative_factor,
    map_extracted_reports_to_financial_data,
    merge_financial_data,
)

logger = get_logger("value_investment")


# ==================== C3: 输入验证 ====================

def validate_stock_code(ticker: str, market: str = "A") -> tuple[bool, str, str]:
    """
    验证股票代码格式

    Args:
        ticker: 股票代码
        market: 市场类型（A=A股, HK=港股）

    Returns:
        (是否有效, 清理后的代码, 错误信息)
    """
    if not ticker:
        return False, "", "股票代码不能为空"

    # 去掉空格和特殊字符
    clean_code = ticker.strip().upper()

    # 移除可能的后缀
    clean_code = clean_code.split('.')[0]

    if market == "A":
        # A股代码验证：6位数字
        if not re.match(r'^[0-9]{6}$', clean_code):
            return False, clean_code, f"A股代码格式错误，应为6位数字，如：600519、000001。输入: {ticker}"

        # 验证交易所前缀
        prefix = clean_code[:3]
        valid_prefixes = ['600', '601', '603', '605', '688',  # 上海
                         '000', '001', '002', '003', '300', '301']  # 深圳
        if prefix not in valid_prefixes:
            return False, clean_code, f"A股代码前缀无效: {prefix}。有效前缀: {valid_prefixes}"

    elif market == "HK":
        # 港股代码验证：1-5位数字
        if not re.match(r'^[0-9]{1,5}$', clean_code):
            return False, clean_code, f"港股代码格式错误，应为1-5位数字，如：00700、01810。输入: {ticker}"

    else:
        return False, clean_code, f"不支持的市场类型: {market}。支持: A, HK"

    return True, clean_code, ""


# ==================== Report-Collector 数据补充 ====================

def _get_report_collector_config() -> Dict[str, Any]:
    """从环境变量获取 report-collector 配置"""
    return get_report_collector_config()


def _get_mongo_client():
    """获取 pymongo 同步客户端（延迟创建）"""
    try:
        import pymongo
        mongo_host = os.getenv("MONGODB_HOST", "localhost")
        mongo_port = int(os.getenv("MONGODB_PORT", "27017"))
        mongo_user = os.getenv("MONGODB_USERNAME", "")
        mongo_pass = os.getenv("MONGODB_PASSWORD", "")
        mongo_db = os.getenv("MONGODB_DATABASE", "tradingagents")
        mongo_auth = os.getenv("MONGODB_AUTH_SOURCE", "admin")

        if mongo_user and mongo_pass:
            uri = f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}/{mongo_db}?authSource={mongo_auth}"
        else:
            uri = f"mongodb://{mongo_host}:{mongo_port}/{mongo_db}"

        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=3000)
        # 快速验证连接
        client.admin.command('ping')
        return client[mongo_db]
    except Exception as e:
        logger.debug(f"MongoDB 连接失败（report-collector 缓存不可用）: {e}")
        return None


def _get_cached_report_data(ticker: str, market: str) -> Optional[Dict[str, Any]]:
    """从 MongoDB 读取 report-collector 缓存数据（TTL 7天）"""
    try:
        db = _get_mongo_client()
        if db is None:
            return None

        collection = db["report_collector_cache"]
        doc = collection.find_one({"ticker": ticker, "market": market})
        if doc is None:
            return None

        # 检查 TTL（7天）
        cached_at = doc.get("cached_at", 0)
        if time.time() - cached_at > 7 * 24 * 3600:
            logger.info(f"[report-collector 缓存] {ticker} 缓存已过期")
            return None

        logger.info(f"[report-collector 缓存] {ticker} 命中缓存")
        return doc.get("data")
    except Exception as e:
        logger.debug(f"读取 report-collector 缓存失败: {e}")
        return None


def _save_report_data_to_cache(ticker: str, market: str, data: Dict[str, Any]) -> None:
    """将 report-collector 数据保存到 MongoDB 缓存"""
    try:
        db = _get_mongo_client()
        if db is None:
            return

        collection = db["report_collector_cache"]
        collection.update_one(
            {"ticker": ticker, "market": market},
            {"$set": {
                "ticker": ticker,
                "market": market,
                "data": data,
                "cached_at": time.time(),
            }},
            upsert=True,
        )
        logger.info(f"[report-collector 缓存] {ticker} 已缓存")
    except Exception as e:
        logger.debug(f"保存 report-collector 缓存失败: {e}")


def _fetch_from_report_collector(ticker: str, market: str) -> Optional[Dict[str, Any]]:
    """从 report-collector 获取并映射财务数据"""
    from tradingagents.services.report_collector_client import ReportCollectorClient

    rc_config = _get_report_collector_config()
    client = ReportCollectorClient(
        base_url=rc_config["url"],
        port=rc_config["port"],
        timeout=rc_config["timeout"],
    )

    if not client.is_available():
        logger.warning("[report-collector] 服务不可用")
        return None

    # 转换市场代码
    rc_market = "CN" if market == "A" else market

    extracted_reports = client.fetch_financial_data(
        stock_code=ticker,
        market=rc_market,
        max_reports=rc_config["max_reports"],
    )

    if not extracted_reports:
        return None

    # 映射为 financial_data 格式
    rc_data = map_extracted_reports_to_financial_data(extracted_reports)

    # 附加原始提取数据供报告生成使用
    rc_data['_extracted_reports'] = extracted_reports

    return rc_data


def _supplement_with_report_collector(financial_data: Dict[str, Any], ticker: str, market: str) -> Dict[str, Any]:
    """
    检查 financial_data 缺失字段，用 report-collector 补充

    Args:
        financial_data: AKShare 获取的财务数据
        ticker: 股票代码
        market: 市场类型 (A/HK)

    Returns:
        补充后的财务数据
    """
    # 1. 检查是否有缺失
    key_fields = [
        'net_profits', 'operating_cash_flow', 'free_cash_flow',
        'debt_ratio', 'total_equity', 'cash_and_equivalents', 'total_assets',
    ]
    missing_fields = []
    for k in key_fields:
        v = financial_data.get(k)
        if v is None or (isinstance(v, list) and len(v) == 0):
            missing_fields.append(k)

    if not missing_fields:
        logger.info(f"[report-collector] AKShare 数据完整，无需补充")
        return financial_data

    logger.info(f"[report-collector] 检测到 {len(missing_fields)} 个缺失字段: {missing_fields}")

    # 2. 检查 MongoDB 缓存
    cached = _get_cached_report_data(ticker, market)
    if cached:
        return merge_financial_data(financial_data, cached)

    # 3. 从 report-collector 获取
    rc_data = _fetch_from_report_collector(ticker, market)
    if rc_data:
        _save_report_data_to_cache(ticker, market, rc_data)
        return merge_financial_data(financial_data, rc_data)

    logger.warning(f"[report-collector] 补充失败，使用 AKShare 原始数据")
    return financial_data


# ==================== C1: 结构化数据获取 ====================

def _fetch_financial_data_structured(ticker: str, market: str = "A") -> Dict[str, Any]:
    """
    使用 AKShare 直接获取结构化财务数据（修复 C1）

    Args:
        ticker: 股票代码
        market: 市场类型

    Returns:
        结构化的财务数据字典
    """
    import akshare as ak

    data = {
        'net_profits': [],
        'free_cash_flow': None,
        'operating_cash_flow': None,
        'roe_avg_3y': None,
        'profit_quality': None,
        'fcf_yield': None,
        'revenue_growth_3y': None,
        'profit_growth_3y': None,
        'debt_ratio': None,
        'financials_list': [],
        'total_equity': None,
        'cash_and_equivalents': None,
        # 问题1修复: 添加现金健康度计算所需字段
        'interest_expense': None,          # 财务费用
        'short_term_debt': None,           # 短期借款
        'long_term_debt': None,            # 长期借款
        'interest_bearing_debt': None,     # 有息负债合计
        'current_ratio': None,             # 流动比率
        'current_assets': None,            # 流动资产
        'current_liabilities': None,       # 流动负债
        'capex': None,                     # 资本支出
        'total_assets': None,              # 总资产
    }

    pure_code = ticker.split('.')[0]

    if market != "A":
        logger.warning(f"目前仅支持 A 股财务数据，收到市场类型: {market}")
        return data

    try:
        # 1. 获取财务摘要（主要指标）
        logger.info(f"📊 获取 {pure_code} 财务摘要...")
        df_abstract = ak.stock_financial_abstract(symbol=pure_code)

        if df_abstract is not None and not df_abstract.empty:
            # 转换为字典列表
            raw_financials = df_abstract.to_dict('records')

            # 问题2修复: 添加统一的英文字段名映射，同时保留中文字段名
            financials_list = []
            for record in raw_financials[:8]:  # 最近8期
                normalized = dict(record)  # 保留原始中文字段

                # 添加英文字段名映射
                normalized['net_profit'] = record.get('归属于母公司所有者的净利润')
                normalized['revenue'] = record.get('营业总收入') or record.get('营业收入')
                normalized['total_assets'] = record.get('资产总计')

                financials_list.append(normalized)

            data['financials_list'] = financials_list

            # 提取净利润列表（保守净利润计算需要）
            for record in financials_list[:5]:  # 最近5期
                net_profit = record.get('归属于母公司所有者的净利润')
                if net_profit is not None:
                    try:
                        data['net_profits'].append(float(net_profit))
                    except (ValueError, TypeError):
                        pass

            # 提取 ROE
            if financials_list:
                latest = financials_list[0]
                roe = latest.get('加权净资产收益率')
                if roe is not None:
                    try:
                        roe_val = float(str(roe).replace('%', ''))
                        data['roe_avg_3y'] = roe_val
                    except (ValueError, TypeError):
                        pass

            logger.info(f"✅ 财务摘要获取成功，获取 {len(financials_list)} 期数据")

    except Exception as e:
        logger.warning(f"获取财务摘要失败: {e}")

    try:
        # 2. 获取资产负债表数据（使用新浪接口，更稳定）
        logger.info(f"📊 获取 {pure_code} 资产负债表...")
        df_balance = ak.stock_financial_report_sina(stock=pure_code, symbol="资产负债表")

        if df_balance is not None and not df_balance.empty:
            import pandas as pd
            latest_row = df_balance.iloc[0] if len(df_balance) > 0 else None

            if latest_row is not None:
                # 辅助函数：安全获取数值（处理 nan、None、'--' 等）
                def safe_float(val, default=None):
                    if val is None or val == '--' or val == '':
                        return default
                    try:
                        f_val = float(val)
                        # 检查是否为 nan
                        if pd.isna(f_val):
                            return default
                        return f_val
                    except (ValueError, TypeError):
                        return default

                # 资产负债率
                total_assets = safe_float(latest_row.get('资产总计'))
                total_liabilities = safe_float(latest_row.get('负债合计'))
                if total_assets and total_liabilities:
                    data['debt_ratio'] = total_liabilities / total_assets
                    data['total_assets'] = total_assets

                # 股东权益
                equity = safe_float(latest_row.get('所有者权益合计')) or safe_float(latest_row.get('归属于母公司股东权益合计'))
                if equity:
                    data['total_equity'] = equity

                # 货币资金
                cash = safe_float(latest_row.get('货币资金'))
                if cash:
                    data['cash_and_equivalents'] = cash

                # ========== 问题1修复: 添加缺失的有息负债和流动比率数据 ==========

                # 短期借款（可能为空，如茅台）
                data['short_term_debt'] = safe_float(latest_row.get('短期借款'), 0)

                # 长期借款（可能为空）
                data['long_term_debt'] = safe_float(latest_row.get('长期借款'), 0)

                # 应付债券
                bonds_payable = safe_float(latest_row.get('应付债券'), 0)

                # 一年内到期的非流动负债
                one_year_debt = safe_float(latest_row.get('一年内到期的非流动负债'), 0)

                # 计算有息负债合计
                data['interest_bearing_debt'] = (
                    data['short_term_debt'] +
                    data['long_term_debt'] +
                    bonds_payable +
                    one_year_debt
                )

                # 流动资产和流动负债
                data['current_assets'] = safe_float(latest_row.get('流动资产合计'))
                data['current_liabilities'] = safe_float(latest_row.get('流动负债合计'))

                # 计算流动比率
                if data['current_assets'] and data['current_liabilities'] and data['current_liabilities'] > 0:
                    data['current_ratio'] = data['current_assets'] / data['current_liabilities']

                logger.info(f"✅ 资产负债表获取成功: 有息负债={data.get('interest_bearing_debt', 0)/1e8:.2f}亿, 流动比率={data.get('current_ratio', 'N/A'):.2f}")

    except Exception as e:
        logger.warning(f"获取资产负债表失败: {e}")

    try:
        # 3. 获取利润表数据（使用新浪接口，获取财务费用和净利润）
        logger.info(f"📊 获取 {pure_code} 利润表...")
        df_income = ak.stock_financial_report_sina(stock=pure_code, symbol="利润表")

        if df_income is not None and not df_income.empty:
            # 辅助函数
            def safe_float(val, default=None):
                if val is None or val == '--' or val == '':
                    return default
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return default

            latest_row = df_income.iloc[0] if len(df_income) > 0 else None

            if latest_row is not None:
                # 财务费用（利息支出等，负数表示利息收入大于支出）
                interest_expense = safe_float(latest_row.get('财务费用'), 0)
                data['interest_expense'] = interest_expense
                logger.info(f"✅ 财务费用获取成功: {interest_expense/1e8:.2f}亿")

            # 从利润表提取净利润列表（比财务摘要更可靠）
            if not data['net_profits']:
                for i in range(min(5, len(df_income))):
                    row = df_income.iloc[i]
                    np_val = safe_float(row.get('归属于母公司所有者的净利润'))
                    if np_val is not None:
                        data['net_profits'].append(np_val)
                logger.info(f"✅ 净利润列表获取成功: {len(data['net_profits'])}期")

            logger.info(f"✅ 利润表获取成功")

    except Exception as e:
        logger.warning(f"获取利润表失败: {e}")
        data['interest_expense'] = 0

    try:
        # 4. 获取现金流量表数据（使用新浪接口）
        logger.info(f"📊 获取 {pure_code} 现金流量表...")
        df_cashflow = ak.stock_financial_report_sina(stock=pure_code, symbol="现金流量表")

        if df_cashflow is not None and not df_cashflow.empty:
            latest_row = df_cashflow.iloc[0] if len(df_cashflow) > 0 else None

            if latest_row is not None:
                # 辅助函数
                def safe_float(val, default=None):
                    if val is None or val == '--' or val == '':
                        return default
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        return default

                # 经营活动现金流
                ocf = safe_float(latest_row.get('经营活动产生的现金流量净额'))
                if ocf:
                    data['operating_cash_flow'] = ocf

                # 资本支出（注意新浪接口字段名是"所支付的现金"而非"支付的现金"）
                capex = safe_float(latest_row.get('购建固定资产、无形资产和其他长期资产所支付的现金'))
                if capex:
                    data['capex'] = abs(capex)
                else:
                    data['capex'] = 0

                # 计算自由现金流 = 经营现金流 - 资本支出
                if ocf and data['capex']:
                    data['free_cash_flow'] = ocf - data['capex']

            logger.info(f"✅ 现金流量表获取成功: CFO={data.get('operating_cash_flow', 0)/1e8:.2f}亿, CapEx={data.get('capex', 0)/1e8:.2f}亿")

    except Exception as e:
        logger.warning(f"获取现金流量表失败: {e}")

    # 计算利润质量 (经营现金流 / 净利润)
    if data['operating_cash_flow'] and data['net_profits']:
        try:
            latest_profit = data['net_profits'][0]
            if latest_profit > 0:
                data['profit_quality'] = data['operating_cash_flow'] / latest_profit
        except (ZeroDivisionError, IndexError):
            pass

    # 5. 补充同花顺数据（流动比率、资产负债率备选）
    try:
        if data.get('current_ratio') is None or data.get('debt_ratio') is None:
            logger.info(f"📊 获取 {pure_code} 同花顺财务概况（补充数据）...")
            df_ths = ak.stock_financial_abstract_ths(symbol=pure_code)

            if df_ths is not None and not df_ths.empty:
                latest = df_ths.iloc[0]

                # 流动比率（如果之前没获取到）
                if data.get('current_ratio') is None:
                    cr = latest.get('流动比率')
                    if cr is not None:
                        try:
                            data['current_ratio'] = float(cr)
                            logger.info(f"✅ 同花顺流动比率: {data['current_ratio']:.2f}")
                        except (ValueError, TypeError):
                            pass

                # 资产负债率（如果之前没获取到）
                if data.get('debt_ratio') is None:
                    dr = latest.get('资产负债率')
                    if dr is not None:
                        try:
                            dr_str = str(dr).replace('%', '')
                            data['debt_ratio'] = float(dr_str) / 100 if float(dr_str) > 1 else float(dr_str)
                            logger.info(f"✅ 同花顺资产负债率: {data['debt_ratio']:.2%}")
                        except (ValueError, TypeError):
                            pass

    except Exception as e:
        logger.debug(f"获取同花顺补充数据失败: {e}")

    return data


def _fetch_market_data_structured(ticker: str, market: str = "A") -> Dict[str, Any]:
    """
    使用 AKShare 直接获取结构化行情数据（修复 C1）

    Args:
        ticker: 股票代码
        market: 市场类型

    Returns:
        结构化的行情数据字典
    """
    import akshare as ak

    data = {
        'market_cap': None,
        'close_price': None,
        'total_shares': None,
    }

    pure_code = ticker.split('.')[0]

    if market != "A":
        logger.warning(f"目前仅支持 A 股行情数据，收到市场类型: {market}")
        return data

    try:
        # 获取实时行情
        logger.info(f"📊 获取 {pure_code} 实时行情...")
        df = ak.stock_individual_info_em(symbol=pure_code)

        if df is not None and not df.empty:
            # 转换为字典方便查找
            info_dict = dict(zip(df['item'], df['value']))

            # 总市值
            market_cap = info_dict.get('总市值')
            if market_cap:
                try:
                    data['market_cap'] = float(market_cap)
                except (ValueError, TypeError):
                    pass

            # 最新价
            close_price = info_dict.get('最新价') or info_dict.get('收盘价')
            if close_price:
                try:
                    data['close_price'] = float(close_price)
                except (ValueError, TypeError):
                    pass

            # 总股本
            total_shares = info_dict.get('总股本')
            if total_shares:
                try:
                    data['total_shares'] = float(total_shares)
                except (ValueError, TypeError):
                    pass

            logger.info(f"✅ 行情数据获取成功: 市值={data['market_cap']}, 股价={data['close_price']}")

    except Exception as e:
        logger.warning(f"获取行情数据失败: {e}")

    return data


# ==================== W1: 动态行业获取 ====================

def _get_industry_dynamic(ticker: str, market: str = "A") -> str:
    """
    使用 AKShare 动态获取股票行业（修复 W1）

    Args:
        ticker: 股票代码
        market: 市场类型

    Returns:
        行业名称
    """
    import akshare as ak

    pure_code = ticker.split('.')[0]

    if market != "A":
        return "default"

    try:
        # 方法1: 从个股信息获取
        df = ak.stock_individual_info_em(symbol=pure_code)
        if df is not None and not df.empty:
            info_dict = dict(zip(df['item'], df['value']))
            industry = info_dict.get('行业')
            if industry:
                logger.info(f"✅ 获取到行业: {industry}")
                return industry

    except Exception as e:
        logger.debug(f"从个股信息获取行业失败: {e}")

    try:
        # 方法2: 从股票基本信息获取
        df = ak.stock_info_a_code_name()
        if df is not None and not df.empty:
            stock_row = df[df['code'] == pure_code]
            if not stock_row.empty:
                industry = stock_row.iloc[0].get('industry', 'default')
                if industry:
                    return industry

    except Exception as e:
        logger.debug(f"从基本信息获取行业失败: {e}")

    # 回退到硬编码映射（保留作为最后备选）
    industry_map = {
        '601398': '银行', '601939': '银行', '600036': '银行',
        '600519': '食品饮料', '000858': '食品饮料',
        '600900': '公用事业', '601088': '煤炭',
    }

    return industry_map.get(pure_code, 'default')


# ==================== C2: 同步调用分红/回购数据 ====================

def _fetch_dividend_data_sync(ticker: str, market: str = "A") -> Optional[Dict]:
    """
    同步获取分红数据（修复 C2 - 避免事件循环冲突）

    Args:
        ticker: 股票代码
        market: 市场类型

    Returns:
        分红数据字典
    """
    import akshare as ak

    records = []
    payout_ratios = []
    pure_code = ticker.split('.')[0]

    if market != "A":
        return {
            'records': [],
            'consecutive_years': 0,
            'avg_payout_ratio_3y': 0,
            'total_dividend_years': 0
        }

    try:
        # 使用同花顺分红明细
        logger.info(f"📊 获取 {pure_code} 分红数据...")
        df = ak.stock_fhps_detail_ths(symbol=pure_code)

        if df is not None and len(df) > 0:
            # 筛选年报数据
            annual_df = df[df['报告期'].str.contains('年报', na=False)].copy()

            for _, row in annual_df.iterrows():
                try:
                    # 提取年份
                    report_period = str(row.get('报告期', ''))
                    year_match = None
                    if '年报' in report_period:
                        import re
                        match = re.search(r'(\d{4})', report_period)
                        if match:
                            year_match = int(match.group(1))

                    if not year_match:
                        continue

                    # 提取每股分红
                    cash_dividend = row.get('派息', 0)
                    if cash_dividend and cash_dividend != '--':
                        try:
                            if isinstance(cash_dividend, str) and '派' in cash_dividend:
                                parts = cash_dividend.split('派')
                                cash_dividend = float(parts[-1]) / 10
                            else:
                                cash_dividend = float(cash_dividend)
                        except:
                            cash_dividend = 0

                    # 提取支付率
                    payout_ratio = row.get('股利支付率', None)
                    if payout_ratio and payout_ratio != '--':
                        try:
                            if isinstance(payout_ratio, str):
                                payout_ratio = float(payout_ratio.replace('%', '')) / 100
                            else:
                                payout_ratio = float(payout_ratio)
                                if payout_ratio > 1:
                                    payout_ratio = payout_ratio / 100
                            payout_ratios.append(payout_ratio)
                        except:
                            payout_ratio = None

                    records.append({
                        'year': year_match,
                        'cash_dividend': cash_dividend,
                        'payout_ratio': payout_ratio,
                        'source': '同花顺'
                    })

                except Exception as e:
                    logger.debug(f"解析分红记录失败: {e}")
                    continue

            logger.info(f"✅ 获取到 {len(records)} 条分红记录")

    except Exception as e:
        logger.warning(f"获取分红数据失败: {e}")

    if not records:
        return {
            'records': [],
            'consecutive_years': 0,
            'avg_payout_ratio_3y': 0,
            'total_dividend_years': 0
        }

    # 按年份排序（降序）
    records.sort(key=lambda x: x['year'], reverse=True)

    # 计算连续分红年数
    consecutive_years = _calculate_consecutive_years(records)

    # 计算近3年平均支付率
    avg_payout_ratio_3y = 0
    if payout_ratios:
        recent_ratios = payout_ratios[:3]
        avg_payout_ratio_3y = sum(recent_ratios) / len(recent_ratios)

    return {
        'records': records,
        'consecutive_years': consecutive_years,
        'avg_payout_ratio_3y': avg_payout_ratio_3y,
        'total_dividend_years': len([r for r in records if r.get('cash_dividend', 0) > 0])
    }


def _fetch_buyback_data_sync(ticker: str, market: str = "A") -> Optional[Dict]:
    """
    同步获取回购数据（修复 C2 - 避免事件循环冲突）

    Args:
        ticker: 股票代码
        market: 市场类型

    Returns:
        回购数据字典
    """
    import akshare as ak
    from datetime import datetime

    records = []
    total_cancelled_amount = 0
    latest_year_amount = 0

    pure_code = ticker.split('.')[0]
    current_year = datetime.now().year

    if market != "A":
        return {
            'records': [],
            'total_cancelled_amount': 0,
            'latest_year_amount': 0,
            'has_active_buyback': False
        }

    try:
        # 获取回购实施情况
        logger.info(f"📊 获取 {pure_code} 回购数据...")
        df = ak.stock_repurchase_em()

        if df is not None and len(df) > 0:
            # 筛选当前股票的回购记录
            stock_df = df[df['代码'].str.contains(pure_code, na=False)]

            for _, row in stock_df.iterrows():
                try:
                    # 解析回购金额
                    amount_str = str(row.get('已回购金额', '0'))
                    amount = _parse_amount(amount_str)

                    # 解析回购进度/状态
                    status = str(row.get('实施进度', ''))
                    is_cancelled = '注销' in status or '已完成' in status

                    # 解析公告日期
                    announce_date = row.get('首次公告日', '')
                    year = None
                    if announce_date:
                        try:
                            if isinstance(announce_date, str):
                                year = int(announce_date[:4])
                            else:
                                year = announce_date.year
                        except:
                            year = current_year

                    record = {
                        'year': year,
                        'amount': amount,
                        'status': status,
                        'is_cancelled': is_cancelled,
                        'source': '东方财富'
                    }
                    records.append(record)

                    # 统计已注销金额
                    if is_cancelled and amount:
                        total_cancelled_amount += amount

                    # 统计最近一年金额
                    if year and year >= current_year - 1 and amount:
                        latest_year_amount += amount

                except Exception as e:
                    logger.debug(f"解析回购记录失败: {e}")
                    continue

            logger.info(f"✅ 获取到 {len(records)} 条回购记录，已注销金额: {total_cancelled_amount/1e8:.2f}亿")

    except Exception as e:
        logger.warning(f"获取回购数据失败: {e}")

    return {
        'records': records,
        'total_cancelled_amount': total_cancelled_amount,
        'latest_year_amount': latest_year_amount,
        'has_active_buyback': any(not r.get('is_cancelled', True) for r in records)
    }


def _calculate_consecutive_years(records: list) -> int:
    """计算连续分红年数"""
    if not records:
        return 0

    dividend_years = sorted(
        [r['year'] for r in records if r.get('cash_dividend', 0) > 0],
        reverse=True
    )

    if not dividend_years:
        return 0

    consecutive = 1
    for i in range(1, len(dividend_years)):
        if dividend_years[i-1] - dividend_years[i] == 1:
            consecutive += 1
        else:
            break

    return consecutive


def _parse_amount(amount_str: str) -> float:
    """解析金额字符串"""
    if not amount_str or amount_str == '--':
        return 0

    try:
        amount_str = str(amount_str).strip()
        if '亿' in amount_str:
            return float(amount_str.replace('亿', '').strip()) * 1e8
        elif '万' in amount_str:
            return float(amount_str.replace('万', '').strip()) * 1e4
        else:
            return float(amount_str)
    except:
        return 0


# ==================== 主工具函数 ====================

@tool
def get_value_investment_analysis(
    ticker: str,
    market: str = "A"
) -> str:
    """
    获取股票的价值投资分析报告

    基于「龟龟投资」穿透回报率模型，提供：
    - 穿透回报率（分红收益率 + 回购收益率）
    - 现金健康度评估
    - 5维健康评分（盈利、偿债、成长、运营、现金流）
    - 健康标签（high_roe, stable_dividend 等）

    Args:
        ticker: 股票代码（如 600519、00700）
        market: 市场类型（A=A股, HK=港股）

    Returns:
        价值投资分析报告（字符串格式）
    """
    logger.info(f"📊 [价值投资工具] 开始分析 {ticker} ({market}股)")

    # C3: 输入验证
    is_valid, clean_code, error_msg = validate_stock_code(ticker, market)
    if not is_valid:
        logger.error(f"❌ [价值投资工具] 股票代码验证失败: {error_msg}")
        return f"❌ 股票代码验证失败: {error_msg}"

    ticker = clean_code
    logger.info(f"✅ 股票代码验证通过: {ticker}")

    try:
        # 1. C1: 获取结构化财务数据（直接使用 AKShare）
        financial_data = _fetch_financial_data_structured(ticker, market)

        # 1.5: Report-Collector 补充缺失字段
        rc_config = _get_report_collector_config()
        if rc_config["enabled"] and rc_config["analysis_enabled"]:
            financial_data = _supplement_with_report_collector(financial_data, ticker, market)

        # 1.6: FinancialReportClient authoritative annual-report fields
        financial_data = apply_financial_report_client_data(
            financial_data=financial_data,
            ticker=ticker,
            market=market,
            period_end=None,
        )

        # 2. C1: 获取结构化行情数据（直接使用 AKShare）
        market_data = _fetch_market_data_structured(ticker, market)

        if not market_data.get('market_cap') or market_data['market_cap'] <= 0:
            return f"❌ 无法获取 {ticker} 的市值数据，无法计算穿透回报率"

        # 3. C2: 同步获取分红和回购数据（避免事件循环冲突）
        dividend_data = _fetch_dividend_data_sync(ticker, market)
        buyback_data = _fetch_buyback_data_sync(ticker, market)

        if not dividend_data:
            dividend_data = {
                'records': [],
                'consecutive_years': 0,
                'avg_payout_ratio_3y': 0,
                'total_dividend_years': 0
            }

        if not buyback_data:
            buyback_data = {
                'records': [],
                'total_cancelled_amount': 0,
                'latest_year_amount': 0,
                'has_active_buyback': False
            }

        # 4. W1: 动态获取行业信息
        industry = _get_industry_dynamic(ticker, market)

        # 5. 计算穿透回报率
        py_calculator = PenetratingYieldCalculator()
        py_result = py_calculator.calculate_full_analysis(
            financial_data=financial_data,
            dividend_data=dividend_data,
            buyback_data=buyback_data,
            market_data=market_data,
            industry=industry
        )

        if not py_result:
            return f"❌ 无法计算 {ticker} 的穿透回报率，数据不足"

        # 6. 计算现金健康度
        cash_calculator = CashHealthCalculator()
        expected_dividend = (
            py_result['penetrating_yield']['conservative_profit'] *
            py_result['penetrating_yield']['payout_ratio']
        )
        cash_health = cash_calculator.calculate_from_financial_data(
            financial_data=financial_data,
            expected_dividend=expected_dividend,
            industry=industry
        )

        # 7. 计算健康评分
        indicators = {
            'roe_avg_3y': financial_data.get('roe_avg_3y'),
            'profit_quality': financial_data.get('profit_quality'),
            'penetrating_yield': py_result['penetrating_yield']['total'],
            'fcf_yield': financial_data.get('fcf_yield'),
            'revenue_growth_3y': financial_data.get('revenue_growth_3y'),
            'profit_growth_3y': financial_data.get('profit_growth_3y'),
            'debt_ratio': financial_data.get('debt_ratio'),
            'consecutive_dividend_years': dividend_data.get('consecutive_years', 0),
            'dividend_yield': py_result['penetrating_yield']['dividend_yield'],
        }

        score_calculator = HealthScoreCalculator()
        financials_list = financial_data.get('financials_list', [])

        # 问题2修复: 将计算好的资产负债率和流动比率注入到 financials_list
        if financials_list:
            financials_list[0]['asset_liability_ratio'] = financial_data.get('debt_ratio')
            financials_list[0]['current_ratio'] = financial_data.get('current_ratio')
            financials_list[0]['total_assets'] = financial_data.get('total_assets')
        else:
            # 如果 financials_list 为空，创建一个基本记录
            financials_list = [{
                'asset_liability_ratio': financial_data.get('debt_ratio'),
                'current_ratio': financial_data.get('current_ratio'),
                'total_assets': financial_data.get('total_assets'),
            }]

        health_scores = score_calculator.calculate(indicators, financials_list)

        # 8. 生成健康标签
        cash_health_dict = cash_calculator.to_dict(cash_health)
        health_flags = generate_health_flags(indicators, cash_health_dict)

        # 9. 生成报告
        report = _generate_report(
            ticker=ticker,
            market=market,
            py_result=py_result,
            cash_health=cash_health,
            health_scores=health_scores,
            health_flags=health_flags,
            dividend_data=dividend_data,
            market_data=market_data,
            industry=industry,
            financial_data=financial_data,
        )

        logger.info(f"✅ [价值投资工具] {ticker} 分析完成")
        return report

    except Exception as e:
        logger.error(f"❌ [价值投资工具] 分析 {ticker} 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"❌ 价值投资分析失败: {str(e)}"


def _generate_report(
    ticker: str,
    market: str,
    py_result: Dict,
    cash_health,
    health_scores,
    health_flags: list,
    dividend_data: Dict,
    market_data: Dict,
    industry: str,
    financial_data: Optional[Dict] = None,
) -> str:
    """生成价值投资分析报告"""

    py = py_result['penetrating_yield']
    scores = health_scores

    report = f"""
═══════════════════════════════════════════════════════════════
📊 价值投资分析报告 - {ticker} ({market}股)
═══════════════════════════════════════════════════════════════

▶ 一、穿透回报率分析
───────────────────────────────────────────────────────────────
  穿透回报率: {py['total']:.2f}%
    ├─ 分红收益率: {py['dividend_yield']:.2f}%
    └─ 回购收益率: {py['buyback_yield']:.2f}%

  有效支付率: {py['payout_ratio']*100:.1f}% (来源: {py['payout_source']})
  保守净利润: {py['conservative_profit']/1e8:.2f}亿元 ({py['profit_method']})
  行业: {industry} (保守系数: {get_conservative_factor(industry)})

▶ 二、现金健康度评估
───────────────────────────────────────────────────────────────
  健康度状态: {cash_health.health_flag}
  真实可支配现金流(RCF): {cash_health.rcf/1e8:.2f}亿元 (如有数据)
  现金储备: {cash_health.cash_reserve/1e8:.2f}亿元 (如有数据)
  分红覆盖率: {cash_health.cov_div:.2f}x (如有数据)
  现金缓冲: {cash_health.buffer_months:.1f}个月 (如有数据)

▶ 三、5维健康评分 (满分100)
───────────────────────────────────────────────────────────────
  盈利能力: {scores.profitability}分
  偿债能力: {scores.solvency}分
  成长能力: {scores.growth}分
  运营能力: {scores.operation}分
  现金流能力: {scores.cashflow}分
  ─────────────────
  综合得分: {scores.total}分

▶ 四、健康标签
───────────────────────────────────────────────────────────────
  {', '.join(health_flags) if health_flags else '暂无标签'}

  标签说明:
  • high_roe: ROE ≥ 15%
  • stable_dividend: 连续分红 ≥ 5年
  • low_debt: 资产负债率 < 50%
  • positive_fcf: 自由现金流 > 0
  • profit_quality: 利润质量(CFO/NP) ≥ 0.8
  • high_penetrating_yield: 穿透率 ≥ 5%
  • high_dividend_yield: 股息率 ≥ 3%
  • cash_healthy: 现金健康

▶ 五、分红历史
───────────────────────────────────────────────────────────────
  连续分红年数: {dividend_data.get('consecutive_years', 0)}年
  累计分红年数: {dividend_data.get('total_dividend_years', 0)}年
  近3年平均支付率: {dividend_data.get('avg_payout_ratio_3y', 0)*100:.1f}%

▶ 六、投资建议
───────────────────────────────────────────────────────────────
"""

    # 根据穿透回报率和健康评分给出建议
    if py['total'] >= 7 and scores.total >= 80:
        report += "  🟢 优质价值投资标的 - 穿透回报率高，财务健康，适合长期持有\n"
    elif py['total'] >= 5 and scores.total >= 70:
        report += "  🟡 良好价值投资标的 - 回报率适中，财务状况良好\n"
    elif py['total'] >= 3 and scores.total >= 60:
        report += "  🟠 一般价值投资标的 - 回报率一般，需关注风险因素\n"
    else:
        report += "  🔴 谨慎 - 穿透回报率或财务健康度较低，建议深入研究\n"

    if 'cash_healthy' in health_flags:
        report += "  ✅ 现金流健康，分红可持续性较强\n"
    else:
        report += "  ⚠️ 需关注现金流状况，分红可持续性存疑\n"

    if 'stable_dividend' in health_flags:
        report += "  ✅ 分红历史稳定，股东回报政策可靠\n"

    # 数据来源说明（当使用了 report-collector 补充时）
    if financial_data and financial_data.get('_data_source'):
        data_source = financial_data['_data_source']
        rc_fields = [k for k, v in data_source.items() if v == 'report-collector']
        if rc_fields:
            report += f"""
▶ 七、数据来源说明
───────────────────────────────────────────────────────────────
  AKShare 数据不完整，已通过 Report-Collector（年报PDF提取）补充以下字段:
  {', '.join(rc_fields)}
"""
            # 附加 PDF 年报元数据
            extracted = financial_data.get('_extracted_reports', [])
            if extracted:
                report += "  参考年报:\n"
                for er in extracted[:5]:
                    pi = er.get('_pdf_info', {})
                    report += f"    - {pi.get('file_name', '未知')} (报告年份: {pi.get('report_year', 'N/A')})\n"

    financial_report_note = format_value_report_source_note(financial_data or {})
    if financial_report_note:
        report += financial_report_note

    report += """
═══════════════════════════════════════════════════════════════
⚠️ 免责声明：本报告基于历史数据计算，仅供参考，不构成投资建议
═══════════════════════════════════════════════════════════════
"""

    return report


# 导出工具
__all__ = ['get_value_investment_analysis', 'validate_stock_code']
