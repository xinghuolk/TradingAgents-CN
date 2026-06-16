# -*- coding: utf-8 -*-
"""Value 多源数据的币种归一边界。canonical: 金额=元, 比率不动, 币种显式 _currency。"""
from typing import Dict, Any, Optional
from tradingagents.utils.logging_init import get_logger

logger = get_logger("unit_normalizer")

_MARKET_CURRENCY = {'A': 'CNY', 'HK': 'HKD', 'US': 'USD'}

# 参与币种校验的金额字段并集（FIELD_TO_KEY 目标 ∪ fields_to_merge ∪ calculator 读取）
AMOUNT_FIELDS = {
    'net_profits', 'revenue', 'operating_cash_flow', 'free_cash_flow', 'capex',
    'cash_and_equivalents', 'interest_expense', 'short_term_debt', 'long_term_debt',
    'bond_payable', 'current_portion_of_long_term_debt', 'interest_bearing_debt',
    'current_assets', 'current_liabilities', 'total_assets', 'total_liabilities',
    'total_equity', 'equity_attributable_to_owners', 'minority_int',
    'st_borr', 'lt_borr', 'repurchase_of_stock',
    'market_cap', 'total_cancelled_amount', 'latest_year_amount',
}


def tag_currency(data: Dict[str, Any], source_currency: Optional[str], market: str) -> Dict[str, Any]:
    """写 _currency（优先源自带，否则市场推断）。不缩放金额、不动比率、跳过 _ 前缀键。"""
    out = dict(data)
    out['_currency'] = source_currency or _MARKET_CURRENCY.get(market, 'CNY')
    return out


def _has_amount(data: Dict[str, Any]) -> bool:
    return any(
        k in AMOUNT_FIELDS and v is not None and not (isinstance(v, list) and len(v) == 0)
        for k, v in data.items()
    )


def assert_same_currency(existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """merge 次闸：两者都标币种且不一致 → 拒绝。空币种放行（向后兼容旧调用）。"""
    a, b = existing.get('_currency'), incoming.get('_currency')
    if a and b and a != b:
        raise ValueError(f"币种不一致，拒绝合并: {a} vs {b}")


def assert_consistent_currency(*dicts: Dict[str, Any]) -> None:
    """主闸：calculator 调用前校验各含金额 dict 同币种。
    含金额非空 dict 必须带 _currency（缺失=配置错误，拒绝）；全空/无金额跳过。"""
    seen = set()
    for d in dicts:
        if not d or not _has_amount(d):
            continue
        cur = d.get('_currency')
        if not cur:
            raise ValueError("含金额的数据缺少 _currency 标记（配置错误）")
        seen.add(cur)
    if len(seen) > 1:
        raise ValueError(f"跨数据源币种不一致，拒绝分析: {sorted(seen)}")
