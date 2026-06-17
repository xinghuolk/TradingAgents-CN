"""mapper 消费上游 normalized_value / canonical_unit 的单测。

对接上游 money-unit-normalization-funnel：下游优先读 normalized_value（已归一的同币种
绝对值），币种从 field 级 canonical_unit 取（修 extraction.currency 顶层=None 的旧问题），
上游未发布时回退 raw value / currency。
"""
import pytest
from types import SimpleNamespace

from tradingagents.dataflows.financial_reports.mapper import merge_financial_report_data
from tradingagents.dataflows.financial_reports.policy import FinancialReportPolicy


def _field(value=None, normalized_value=None, canonical_unit=None, currency=None,
           unit=None, is_reliable=True):
    return SimpleNamespace(
        value=value, normalized_value=normalized_value, canonical_unit=canonical_unit,
        currency=currency, unit=unit, is_reliable=is_reliable, is_present=True,
        source="akshare", confidence="high", field_id="x", raw_bucket="",
    )


def _extraction(fields, currency=None):
    return SimpleNamespace(
        fields=fields, company="603345", market="CN", period_end="2025-12-31",
        catalog_version="v3", currency=currency, llm_model="", llm_provider="",
    )


def _policy():
    return FinancialReportPolicy(allow_llm_models=["gpt-5.5"])


def test_uses_normalized_value_over_raw():
    # 万元 raw=10080.83，normalized=100808300 → 必须用 normalized（否则错 1e4 倍）
    ext = _extraction({"net_profit": _field(value=10080.83, normalized_value=100808300.0,
                                            canonical_unit="CNY", unit="万元")})
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=_policy())
    assert r.financial_data["net_profits"][0] == 100808300.0


def test_falls_back_to_raw_when_no_normalized():
    # 上游未发布 normalized_value（None）→ 回退 raw value
    ext = _extraction({"net_profit": _field(value=1359237139.62, normalized_value=None,
                                            canonical_unit="CNY")})
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=_policy())
    assert r.financial_data["net_profits"][0] == 1359237139.62


def test_currency_from_field_canonical_unit():
    # extraction 顶层 currency=None，但 field canonical_unit=CNY → _currency 应取 CNY
    ext = _extraction({"net_profit": _field(value=1.0, normalized_value=1.0,
                                            canonical_unit="CNY")}, currency=None)
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=_policy())
    assert r.financial_data["_currency"] == "CNY"


def test_currency_falls_back_to_field_currency():
    # 无 canonical_unit 时回退 field.currency
    ext = _extraction({"net_profit": _field(value=1.0, normalized_value=1.0,
                                            canonical_unit=None, currency="CNY")}, currency=None)
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=_policy())
    assert r.financial_data["_currency"] == "CNY"


def test_cross_currency_rejected():
    # financial_data 已是 CNY，上游 field canonical_unit=HKD → 拒绝合并
    ext = _extraction({"net_profit": _field(value=1.0, normalized_value=1.0,
                                            canonical_unit="HKD")})
    with pytest.raises(ValueError):
        merge_financial_report_data(financial_data={"_currency": "CNY"}, extraction=ext, policy=_policy())


def test_repurchase_uses_normalized_value():
    # 回购导出也优先 normalized_value
    ext = _extraction({"repurchase_of_stock": _field(value=6287.92, normalized_value=62879243.53,
                                                     canonical_unit="CNY", unit="万元")})
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=_policy())
    assert r.financial_data["repurchase_of_stock"] == 62879243.53
