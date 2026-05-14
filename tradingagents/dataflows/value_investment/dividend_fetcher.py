# -*- coding: utf-8 -*-
"""
分红数据获取模块

支持：
- AKShare 免费接口获取 A 股分红数据
- MongoDB 缓存（7天过期）
- 多数据源降级
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from statistics import median

from tradingagents.utils.logging_init import get_logger

logger = get_logger("value_investment")

# 缓存过期时间（天）
CACHE_EXPIRE_DAYS = 7

# 全局单例
_dividend_fetcher_instance = None


class DividendFetcher:
    """分红数据获取器"""

    def __init__(self, db=None):
        """
        初始化分红数据获取器

        Args:
            db: MongoDB 数据库实例（可选，用于缓存）
        """
        self.db = db
        self._cache_collection = "dividend_cache"

    async def _get_from_cache(self, stock_code: str) -> Optional[Dict]:
        """从缓存获取分红数据"""
        if not self.db:
            return None

        try:
            collection = self.db[self._cache_collection]
            cache_doc = await collection.find_one({"stock_code": stock_code})

            if cache_doc:
                # 检查是否过期
                cached_at = cache_doc.get("cached_at")
                if cached_at:
                    expire_time = cached_at + timedelta(days=CACHE_EXPIRE_DAYS)
                    if datetime.now() < expire_time:
                        logger.debug(f"分红数据缓存命中: {stock_code}")
                        return cache_doc.get("data")

            return None
        except Exception as e:
            logger.warning(f"读取分红缓存失败: {e}")
            return None

    async def _save_to_cache(self, stock_code: str, data: Dict) -> None:
        """保存分红数据到缓存"""
        if not self.db:
            return

        try:
            collection = self.db[self._cache_collection]
            await collection.update_one(
                {"stock_code": stock_code},
                {
                    "$set": {
                        "stock_code": stock_code,
                        "data": data,
                        "cached_at": datetime.now(),
                        "updated_at": datetime.now()
                    }
                },
                upsert=True
            )
            logger.debug(f"分红数据已缓存: {stock_code}")
        except Exception as e:
            logger.warning(f"保存分红缓存失败: {e}")

    async def fetch_dividend_data(self, stock_code: str, market: str = "A") -> Optional[Dict]:
        """
        获取股票分红数据

        Args:
            stock_code: 股票代码（如 600519）
            market: 市场类型（A=A股, HK=港股）

        Returns:
            分红数据字典，包含：
            - records: 分红记录列表
            - consecutive_years: 连续分红年数
            - avg_payout_ratio_3y: 近3年平均支付率
            - total_dividend_years: 总分红年数
        """
        # 1. 先尝试从缓存获取
        cached_data = await self._get_from_cache(stock_code)
        if cached_data:
            return cached_data

        # 2. 从数据源获取
        if market == "A":
            data = await self._fetch_a_stock_dividend(stock_code)
        elif market == "HK":
            data = await self._fetch_hk_stock_dividend(stock_code)
        else:
            logger.warning(f"不支持的市场类型: {market}")
            return None

        # 3. 保存到缓存
        if data:
            await self._save_to_cache(stock_code, data)

        return data

    async def _fetch_a_stock_dividend(self, stock_code: str) -> Optional[Dict]:
        """
        获取 A 股分红数据（使用 AKShare）

        优先使用同花顺分红明细，回退到巨潮资讯
        """
        import akshare as ak

        records = []
        payout_ratios = []

        # 去掉可能的后缀
        pure_code = stock_code.split('.')[0]

        try:
            # 方案1：使用同花顺分红明细
            df = await asyncio.to_thread(
                ak.stock_fhps_detail_ths,
                symbol=pure_code
            )

            if df is not None and len(df) > 0:
                # 筛选年报数据
                annual_df = df[df['报告期'].str.contains('年报', na=False)].copy()

                for _, row in annual_df.iterrows():
                    try:
                        # 提取年份
                        report_period = str(row.get('报告期', ''))
                        year_match = None
                        if '年报' in report_period:
                            # 格式可能是 "2023年报" 或 "2023年年报"
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
                                # 格式可能是 "10派5" 或直接数字
                                if isinstance(cash_dividend, str) and '派' in cash_dividend:
                                    parts = cash_dividend.split('派')
                                    cash_dividend = float(parts[-1]) / 10  # 转为每股
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
                                    if payout_ratio > 1:  # 如果是百分比形式
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

                logger.info(f"{stock_code} 从同花顺获取到 {len(records)} 条分红记录")

        except Exception as e:
            logger.debug(f"同花顺分红数据获取失败: {e}")

        # 方案2：如果同花顺失败，尝试巨潮资讯
        if not records:
            try:
                df = await asyncio.to_thread(
                    ak.stock_dividend_cninfo,
                    symbol=pure_code
                )

                if df is not None and len(df) > 0:
                    for _, row in df.iterrows():
                        try:
                            year = int(row.get('分红年度', 0))
                            if year < 2000:
                                continue

                            cash_dividend = float(row.get('派息', 0) or 0)
                            # 巨潮的派息通常是每10股派息金额
                            cash_dividend = cash_dividend / 10 if cash_dividend > 0 else 0

                            records.append({
                                'year': year,
                                'cash_dividend': cash_dividend,
                                'payout_ratio': None,
                                'source': '巨潮资讯'
                            })

                        except Exception as e:
                            continue

                    logger.info(f"{stock_code} 从巨潮资讯获取到 {len(records)} 条分红记录")

            except Exception as e:
                logger.debug(f"巨潮资讯分红数据获取失败: {e}")

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
        consecutive_years = self._calculate_consecutive_years(records)

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

    async def _fetch_hk_stock_dividend(self, stock_code: str) -> Optional[Dict]:
        """获取港股分红数据"""
        import akshare as ak

        records = []

        # 港股代码格式：可能是 00001 或 01810
        pure_code = stock_code.lstrip('0') or '0'

        try:
            # 使用 AKShare 港股分红数据
            df = await asyncio.to_thread(
                ak.stock_hk_dividend,
                symbol=pure_code
            )

            if df is not None and len(df) > 0:
                for _, row in df.iterrows():
                    try:
                        # 解析年份和分红金额
                        year = int(row.get('年份', 0))
                        cash_dividend = float(row.get('每股派息', 0) or 0)

                        records.append({
                            'year': year,
                            'cash_dividend': cash_dividend,
                            'payout_ratio': None,
                            'source': 'AKShare港股'
                        })

                    except Exception as e:
                        continue

                logger.info(f"{stock_code} 获取到 {len(records)} 条港股分红记录")

        except Exception as e:
            logger.debug(f"港股分红数据获取失败: {e}")

        if not records:
            return {
                'records': [],
                'consecutive_years': 0,
                'avg_payout_ratio_3y': 0,
                'total_dividend_years': 0
            }

        records.sort(key=lambda x: x['year'], reverse=True)
        consecutive_years = self._calculate_consecutive_years(records)

        return {
            'records': records,
            'consecutive_years': consecutive_years,
            'avg_payout_ratio_3y': 0,  # 港股通常没有支付率数据
            'total_dividend_years': len([r for r in records if r.get('cash_dividend', 0) > 0])
        }

    def _calculate_consecutive_years(self, records: List[Dict]) -> int:
        """计算连续分红年数"""
        if not records:
            return 0

        # 获取有分红的年份列表
        dividend_years = sorted(
            [r['year'] for r in records if r.get('cash_dividend', 0) > 0],
            reverse=True
        )

        if not dividend_years:
            return 0

        # 从最近一年开始计算连续年数
        consecutive = 1
        for i in range(1, len(dividend_years)):
            if dividend_years[i-1] - dividend_years[i] == 1:
                consecutive += 1
            else:
                break

        return consecutive

    def get_effective_payout_ratio(
        self,
        dividend_data: Dict,
        net_profit: float,
        fcf: Optional[float] = None,
        official_ratio: Optional[float] = None
    ) -> Dict:
        """
        计算有效支付率

        算法优先级：
        1. 公告的官方支付率（最准确）
        2. 历史中位数 × 0.8
        3. 已有数据平均 × 0.8
        4. 无分红历史返回 0

        Args:
            dividend_data: 分红数据
            net_profit: 保守净利润
            fcf: 自由现金流（用于资金约束）
            official_ratio: 官方公告的支付率

        Returns:
            {
                'r_eff': 有效支付率,
                'source': 来源说明,
                'r_fcf_limit': FCF约束上限
            }
        """
        if not net_profit or net_profit <= 0:
            return {'r_eff': 0, 'source': '净利润无效', 'r_fcf_limit': None}

        r_policy = None
        source = None

        # 优先级1：官方数据
        if official_ratio is not None and official_ratio > 0:
            r_policy = official_ratio
            source = '官方公告'
        else:
            records = dividend_data.get('records', [])
            payout_ratios = [
                r['payout_ratio'] for r in records
                if r.get('payout_ratio') and r['payout_ratio'] > 0
            ]

            # 优先级2：历史中位数 × 0.8
            if len(payout_ratios) >= 2:
                r_policy = median(payout_ratios) * 0.8
                source = '历史中位数×0.8'
            # 优先级3：已有数据平均 × 0.8
            elif payout_ratios:
                r_policy = sum(payout_ratios) / len(payout_ratios) * 0.8
                source = f'历史平均({len(payout_ratios)}年)×0.8'
            # 优先级4：无分红历史
            else:
                r_policy = 0
                source = '无分红历史'

        # FCF 约束
        r_fcf_limit = None
        if fcf and fcf > 0 and net_profit > 0:
            r_fcf_limit = fcf / net_profit

        # 资金约束：r_eff = min(r_policy, FCF/N_cons)
        r_eff = r_policy
        if r_fcf_limit is not None:
            r_eff = min(r_policy, r_fcf_limit)

        # 上限 100%
        r_eff = min(r_eff, 1.0)

        return {
            'r_eff': r_eff,
            'source': source,
            'r_fcf_limit': r_fcf_limit
        }


def get_dividend_fetcher(db=None) -> DividendFetcher:
    """获取分红数据获取器单例"""
    global _dividend_fetcher_instance
    if _dividend_fetcher_instance is None:
        _dividend_fetcher_instance = DividendFetcher(db)
    return _dividend_fetcher_instance
