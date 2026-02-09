# -*- coding: utf-8 -*-
"""
Report-Collector 数据映射与合并层

将 report-collector 提取的年报数据映射为 financial_data 字典格式，
并与 AKShare 数据合并（AKShare 优先，report-collector 补缺）。
"""

from typing import Dict, List, Any, Optional

from tradingagents.utils.logging_init import get_logger

logger = get_logger("report_data_mapper")


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """安全转换为 float"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def map_extracted_reports_to_financial_data(extracted_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 report-collector 提取的多份年报映射为 financial_data 字典格式

    Args:
        extracted_reports: report-collector 提取的年报数据列表（按年份降序）
            每项包含: income_statement, balance_sheet, cash_flow_statement, financial_metrics, _pdf_info

    Returns:
        与 _fetch_financial_data_structured() 返回格式兼容的字典
    """
    data: Dict[str, Any] = {
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
        'interest_expense': None,
        'short_term_debt': None,
        'long_term_debt': None,
        'interest_bearing_debt': None,
        'current_ratio': None,
        'current_assets': None,
        'current_liabilities': None,
        'capex': None,
        'total_assets': None,
    }

    if not extracted_reports:
        return data

    # 提取净利润列表（多份年报）
    net_profits = []
    roe_values = []
    revenues = []

    for report in extracted_reports:
        income = report.get("income_statement") or {}
        balance = report.get("balance_sheet") or {}
        cashflow = report.get("cash_flow_statement") or {}
        metrics = report.get("financial_metrics") or {}
        pdf_info = report.get("_pdf_info") or {}

        # 净利润
        np_val = _safe_float(income.get("net_profit"))
        if np_val is not None:
            net_profits.append(np_val)

        # ROE
        roe_val = _safe_float(metrics.get("roe"))
        if roe_val is not None:
            roe_values.append(roe_val)

        # 营业收入
        rev_val = _safe_float(income.get("revenue"))
        if rev_val is not None:
            revenues.append(rev_val)

        # 构建 financials_list 条目
        record = {
            'report_year': pdf_info.get('report_year'),
            'net_profit': np_val,
            'revenue': rev_val,
            'total_assets': _safe_float(balance.get("total_assets")),
            'source': 'report-collector',
        }
        data['financials_list'].append(record)

    # 填充 net_profits
    data['net_profits'] = net_profits[:5]  # 最近5期

    # 最新一期的详细数据
    latest = extracted_reports[0]
    latest_income = latest.get("income_statement") or {}
    latest_balance = latest.get("balance_sheet") or {}
    latest_cashflow = latest.get("cash_flow_statement") or {}
    latest_metrics = latest.get("financial_metrics") or {}

    # 现金流
    data['operating_cash_flow'] = _safe_float(latest_cashflow.get("operating_cash_flow"))
    data['free_cash_flow'] = _safe_float(latest_cashflow.get("free_cash_flow"))

    # 资产负债表
    data['total_assets'] = _safe_float(latest_balance.get("total_assets"))
    data['total_equity'] = _safe_float(latest_balance.get("total_equity"))
    data['cash_and_equivalents'] = _safe_float(latest_balance.get("cash_and_equivalents"))
    data['current_assets'] = _safe_float(latest_balance.get("current_assets"))
    data['current_liabilities'] = _safe_float(latest_balance.get("current_liabilities"))

    # 负债数据（report-collector schema 中没有分项，从总负债推算）
    total_liabilities = _safe_float(latest_balance.get("total_liabilities"))
    if data['total_assets'] and total_liabilities:
        data['debt_ratio'] = total_liabilities / data['total_assets']

    # 流动比率
    if data['current_assets'] and data['current_liabilities'] and data['current_liabilities'] > 0:
        data['current_ratio'] = data['current_assets'] / data['current_liabilities']

    # 财务指标
    debt_ratio_from_metrics = _safe_float(latest_metrics.get("debt_ratio"))
    if debt_ratio_from_metrics is not None and data['debt_ratio'] is None:
        # metrics 中的 debt_ratio 是百分比
        data['debt_ratio'] = debt_ratio_from_metrics / 100 if debt_ratio_from_metrics > 1 else debt_ratio_from_metrics

    # ROE 平均值（最多3年）
    if roe_values:
        recent_roe = roe_values[:3]
        data['roe_avg_3y'] = sum(recent_roe) / len(recent_roe)

    # 利润质量
    if data['operating_cash_flow'] and net_profits and net_profits[0] > 0:
        data['profit_quality'] = data['operating_cash_flow'] / net_profits[0]

    # 收入增长（需要3年数据）
    if len(revenues) >= 3 and revenues[-1] and revenues[-1] > 0:
        data['revenue_growth_3y'] = (revenues[0] / revenues[-1] - 1) * 100

    # 利润增长
    if len(net_profits) >= 3 and net_profits[-1] and net_profits[-1] > 0:
        data['profit_growth_3y'] = (net_profits[0] / net_profits[-1] - 1) * 100

    logger.info(
        f"[report-collector] 映射完成: {len(net_profits)}期净利润, "
        f"ROE={data['roe_avg_3y']}, 负债率={data['debt_ratio']}"
    )

    return data


def merge_financial_data(akshare_data: Dict[str, Any], rc_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    合并 AKShare 数据和 report-collector 数据

    策略：AKShare 优先，report-collector 补缺

    Args:
        akshare_data: AKShare 获取的财务数据
        rc_data: report-collector 提取的财务数据

    Returns:
        合并后的财务数据，附加 _data_source 字段记录每个值的来源
    """
    merged = dict(akshare_data)
    data_source: Dict[str, str] = {}

    # 需要合并的字段
    fields_to_merge = [
        'net_profits',
        'free_cash_flow',
        'operating_cash_flow',
        'roe_avg_3y',
        'profit_quality',
        'fcf_yield',
        'revenue_growth_3y',
        'profit_growth_3y',
        'debt_ratio',
        'total_equity',
        'cash_and_equivalents',
        'interest_expense',
        'short_term_debt',
        'long_term_debt',
        'interest_bearing_debt',
        'current_ratio',
        'current_assets',
        'current_liabilities',
        'capex',
        'total_assets',
    ]

    supplemented_count = 0

    for field in fields_to_merge:
        akshare_val = akshare_data.get(field)
        rc_val = rc_data.get(field)

        # 判断 AKShare 值是否为有效值
        akshare_valid = _is_valid_value(akshare_val)

        if akshare_valid:
            data_source[field] = 'akshare'
        elif _is_valid_value(rc_val):
            merged[field] = rc_val
            data_source[field] = 'report-collector'
            supplemented_count += 1
        else:
            data_source[field] = 'missing'

    # financials_list 特殊处理：如果 AKShare 为空，用 rc 的
    if not akshare_data.get('financials_list') and rc_data.get('financials_list'):
        merged['financials_list'] = rc_data['financials_list']
        data_source['financials_list'] = 'report-collector'
        supplemented_count += 1
    else:
        data_source['financials_list'] = 'akshare'

    merged['_data_source'] = data_source

    logger.info(f"[数据合并] report-collector 补充了 {supplemented_count} 个字段")

    return merged


def _is_valid_value(val: Any) -> bool:
    """判断值是否为有效的非空值"""
    if val is None:
        return False
    if isinstance(val, (list, dict)) and len(val) == 0:
        return False
    if isinstance(val, float) and val == 0.0:
        # 0.0 对于某些字段（如利息支出）可能是有效值，这里不过滤
        return True
    return True
