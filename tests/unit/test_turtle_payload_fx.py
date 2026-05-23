from tradingagents.tools import turtle_analysis_tool as tat
from tradingagents.dataflows.value_investment.turtle import collect_fx_currencies
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleFactValue,
    TurtleMarketFacts,
    TurtleReportFacts,
)


def _money_fact(name, value, currency, ref):
    return TurtleFactValue(
        name=name,
        value=MoneyAmount(value=value, currency=currency, unit="yuan", source_label="t", source_reference=ref),
        source_label="t",
        source_reference=ref,
    )


def test_collect_currencies_normalizes_and_dedups():
    report = TurtleReportFacts(fields={"net_profit": _money_fact("net_profit", 5e8, "HK$", "r")})
    market = TurtleMarketFacts(fields={"market_cap": _money_fact("market_cap", 1e10, "HKD", "m")})
    assert collect_fx_currencies(report, market) == {"HKD"}


import json
from unittest.mock import patch


def _report(np_currency):
    return TurtleReportFacts(
        fields={"net_profit": _money_fact("net_profit", 5e8, np_currency, "report.net_profit")},
        status="complete",
    )


def _market(cap_currency, market_as_of="2026-05-23"):
    md = {} if market_as_of is None else {"market_as_of": market_as_of}
    return TurtleMarketFacts(
        fields={"market_cap": _money_fact("market_cap", 1e10, cap_currency, "market_data.market_cap")},
        status="complete",
        metadata=md,
    )


def _run(report, market):
    with patch.object(tat, "get_turtle_report_facts", return_value=report), \
         patch.object(tat, "get_turtle_market_facts", return_value=market):
        return json.loads(tat.prepare_turtle_analysis_payload("00700", "HK", "2026-05-23", "腾讯"))


def test_payload_cross_currency_resolves_fx():
    meta = {"HKD:CNY": {"provider": "yfinance", "as_of": "2026-05-23", "fetched_at": "t", "rate": 0.9}}
    with patch.object(tat, "resolve_fx_rates", return_value=({"HKD:CNY": 0.9}, meta, [])) as rfx:
        out = _run(_report("CNY"), _market("HKD"))
    rfx.assert_called_once()
    rep_meta = out["facts"]["report"]["metadata"]
    assert rep_meta["fx_rates"] == {"HKD:CNY": 0.9}
    assert "HKD:CNY" in rep_meta["fx_rates_meta"]


def test_payload_pure_hkd_skips_fx():
    with patch.object(tat, "resolve_fx_rates") as rfx:
        out = _run(_report("HKD"), _market("HKD"))
    rfx.assert_not_called()
    assert out["facts"]["report"]["metadata"].get("fx_rates", {}) == {}
    assert not any("FX" in c for c in out["facts"]["report"]["caveats"])


def test_payload_fx_failure_adds_caveat():
    with patch.object(tat, "resolve_fx_rates", return_value=({}, {}, ["FX HKD:CNY 取数失败，跨币计算降级"])):
        out = _run(_report("CNY"), _market("HKD"))
    assert any("取数失败" in c for c in out["facts"]["report"]["caveats"])


def test_payload_missing_market_as_of_uses_fetch_date_not_trade_date():
    captured = {}

    def fake_resolve(currencies, target, as_of):
        captured["as_of"] = as_of
        return ({"HKD:CNY": 0.9}, {"HKD:CNY": {}}, [])

    with patch.object(tat, "get_turtle_report_facts", return_value=_report("CNY")), \
         patch.object(tat, "get_turtle_market_facts", return_value=_market("HKD", market_as_of=None)), \
         patch.object(tat, "resolve_fx_rates", side_effect=fake_resolve):
        out = json.loads(tat.prepare_turtle_analysis_payload("00700", "HK", "2020-01-01", "x"))
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", captured["as_of"])
    assert captured["as_of"] != "2020-01-01"
    assert any("market_as_of 缺失" in c for c in out["facts"]["report"]["caveats"])


def test_payload_snapshot_caveat_when_market_as_of_differs_from_trade_date():
    with patch.object(tat, "get_turtle_report_facts", return_value=_report("CNY")), \
         patch.object(tat, "get_turtle_market_facts", return_value=_market("HKD", market_as_of="2026-05-23")), \
         patch.object(tat, "resolve_fx_rates", return_value=({"HKD:CNY": 0.9}, {"HKD:CNY": {}}, [])):
        out = json.loads(tat.prepare_turtle_analysis_payload("00700", "HK", "2020-01-01", "x"))
    assert any("快照" in c for c in out["facts"]["report"]["caveats"])


def test_market_facts_coercion_preserves_metadata():
    class _Obj:
        fields = {}
        caveats = []
        status = "complete"
        metadata = {"market_as_of": "2026-05-23"}

    mf = tat._market_facts(_Obj())
    assert mf.metadata == {"market_as_of": "2026-05-23"}


def test_payload_historical_fx_caveat_when_multi_currency_with_history():
    hist = {"2024-12-31": TurtleReportFacts(fields={"net_profit": _money_fact("net_profit", 4e8, "CNY", "r24")})}
    report = TurtleReportFacts(
        fields={"net_profit": _money_fact("net_profit", 5e8, "CNY", "report.net_profit")},
        status="complete",
        historical=hist,
    )
    with patch.object(tat, "resolve_fx_rates", return_value=({"HKD:CNY": 0.9}, {"HKD:CNY": {}}, [])):
        out = _run(report, _market("HKD"))
    assert any("历史各期" in c for c in out["facts"]["report"]["caveats"])


def test_payload_no_historical_fx_caveat_when_no_history():
    with patch.object(tat, "resolve_fx_rates", return_value=({"HKD:CNY": 0.9}, {"HKD:CNY": {}}, [])):
        out = _run(_report("CNY"), _market("HKD"))  # _report has no historical
    assert not any("历史各期" in c for c in out["facts"]["report"]["caveats"])


def test_payload_fx_failure_degrades_report_status():
    with patch.object(tat, "resolve_fx_rates", return_value=({}, {}, ["FX HKD:CNY 取数失败，跨币计算降级"])):
        out = _run(_report("CNY"), _market("HKD"))
    assert out["facts"]["report"]["status"] == "degraded"
    assert out["facts"]["status"] in ("degraded", "non_decisionable")


def test_payload_fx_success_keeps_report_status_complete():
    with patch.object(tat, "resolve_fx_rates", return_value=({"HKD:CNY": 0.9}, {"HKD:CNY": {}}, [])):
        out = _run(_report("CNY"), _market("HKD"))
    assert out["facts"]["report"]["status"] == "complete"


def test_collect_currencies_ignores_non_formula_fields():
    report = TurtleReportFacts(fields={
        "net_profit": _money_fact("net_profit", 5e8, "HKD", "r"),
        "revenue": _money_fact("revenue", 9e8, "USD", "rev"),  # not a formula input
    })
    market = TurtleMarketFacts(fields={"market_cap": _money_fact("market_cap", 1e10, "HKD", "m")})
    assert collect_fx_currencies(report, market) == {"HKD"}


def test_collect_currencies_skips_unreliable_money():
    np_unreliable = TurtleFactValue(
        name="net_profit",
        value=MoneyAmount(value=5e8, currency="USD", unit="yuan", source_label="t",
                          source_reference="r", reliability="display_only"),
        source_label="t", source_reference="r", reliability="display_only",
    )
    report = TurtleReportFacts(fields={"net_profit": np_unreliable})
    market = TurtleMarketFacts(fields={"market_cap": _money_fact("market_cap", 1e10, "HKD", "m")})
    assert collect_fx_currencies(report, market) == {"HKD"}


def test_collect_fx_currencies_report_priority_over_market_fallback():
    # 计算层 report 优先：同名 market_cap 在 report=HKD 时，market fallback=USD 应被忽略
    report = TurtleReportFacts(fields={"market_cap": _money_fact("market_cap", 1e10, "HKD", "r")})
    market = TurtleMarketFacts(fields={"market_cap": _money_fact("market_cap", 2e10, "USD", "m")})
    assert collect_fx_currencies(report, market) == {"HKD"}


def test_payload_market_fallback_currency_does_not_trigger_fx():
    report = TurtleReportFacts(fields={
        "net_profit": _money_fact("net_profit", 5e8, "HKD", "rnp"),
        "market_cap": _money_fact("market_cap", 1e10, "HKD", "rmc"),  # report market_cap=HKD (calc uses this)
    }, status="complete")
    market = TurtleMarketFacts(
        fields={"market_cap": _money_fact("market_cap", 2e10, "USD", "mmc")},  # market fallback=USD
        status="complete",
        metadata={"market_as_of": "2026-05-23"},
    )
    with patch.object(tat, "resolve_fx_rates") as rfx:
        out = _run(report, market)
    rfx.assert_not_called()                                  # USD fallback must NOT trigger FX
    assert out["facts"]["report"]["status"] == "complete"    # not falsely degraded
    assert not any(("FX" in c or "取数失败" in c) for c in out["facts"]["report"]["caveats"])


def test_payload_unrelated_currency_does_not_trigger_fx_or_degrade():
    report = TurtleReportFacts(fields={
        "net_profit": _money_fact("net_profit", 5e8, "HKD", "r"),
        "revenue": _money_fact("revenue", 9e8, "USD", "rev"),
    }, status="complete")
    with patch.object(tat, "resolve_fx_rates") as rfx:
        out = _run(report, _market("HKD"))
    rfx.assert_not_called()
    assert out["facts"]["report"]["status"] == "complete"
    assert not any(("FX" in c or "取数失败" in c) for c in out["facts"]["report"]["caveats"])
