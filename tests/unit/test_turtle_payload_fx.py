from tradingagents.tools import turtle_analysis_tool as tat
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
    assert tat._collect_currencies(report, market) == {"HKD"}


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
