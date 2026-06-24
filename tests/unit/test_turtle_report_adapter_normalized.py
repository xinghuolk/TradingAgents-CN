"""turtle report_adapter 消费上游 normalized_value / canonical_unit 的单测。

对接 money-unit-normalization-funnel：_adapt_value 优先用 normalized_value（已归元）
+ canonical_unit（货币码），跳过 raw unit 解析——不再因 "unsupported unit"（如 元/ones）
把字段降级为 display_only；上游未发布 normalized 时回退现有 raw value + _field_unit。
"""
from types import SimpleNamespace

from tradingagents.dataflows.value_investment.turtle.report_adapter import _adapt_value
from tradingagents.dataflows.value_investment.turtle.facts import MoneyAmount


def _field(**kw):
    base = dict(value=None, normalized_value=None, canonical_unit=None, currency=None,
                unit=None, field_id="dividends_paid", evidence_page=1)
    base.update(kw)
    return SimpleNamespace(**base)


def test_adapt_uses_normalized_skips_raw_unit():
    # unit="元"（原本 _field_unit 不识别 → unsupported）但有 normalized_value + canonical_unit
    field = _field(value=929784589.68, normalized_value=929784589.68, canonical_unit="CNY", unit="元")
    fv, degr = _adapt_value(field_id="dividends_paid", field=field, source_label="llm",
                            reliability="reliable", caveat=None)
    assert degr is None                                  # 无降级
    assert isinstance(fv.value, MoneyAmount)             # 是 MoneyAmount，不是 raw
    assert fv.value.unit == "yuan"
    assert fv.value.currency == "CNY"
    assert fv.value.value == 929784589.68
    assert fv.reliability == "reliable"                  # 不被降级为 display_only


def test_adapt_buyback_normalized_abs():
    # buyback_amount 取绝对值
    field = _field(value=-62879243.53, normalized_value=-62879243.53, canonical_unit="CNY",
                   unit="元", field_id="buyback_amount")
    fv, _ = _adapt_value(field_id="buyback_amount", field=field, source_label="llm",
                         reliability="reliable", caveat=None)
    assert isinstance(fv.value, MoneyAmount)
    assert fv.value.value == 62879243.53


def test_adapt_falls_back_to_raw_unit_when_no_normalized():
    # 无 normalized → 回退现有 raw value + _field_unit；unit="万元" 可解析为 ten_thousand
    field = _field(value=10080.83, normalized_value=None, canonical_unit=None,
                   currency="CNY", unit="万元", field_id="stock_based_compensation")
    fv, degr = _adapt_value(field_id="stock_based_compensation", field=field, source_label="llm",
                            reliability="reliable", caveat=None)
    assert degr is None
    assert isinstance(fv.value, MoneyAmount)
    assert fv.value.unit == "ten_thousand"


def test_adapt_unsupported_unit_still_display_only_without_normalized():
    # 无 normalized + unit="ones"(不识别) → 仍 display_only（回退现状，未引入新行为）
    field = _field(value=100.0, normalized_value=None, canonical_unit=None,
                   currency="CNY", unit="ones")
    fv, degr = _adapt_value(field_id="dividends_paid", field=field, source_label="llm",
                            reliability="reliable", caveat=None)
    assert fv.reliability == "display_only"
    assert "unsupported unit" in (degr or "")
