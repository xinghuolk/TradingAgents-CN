import pytest

from tradingagents.dataflows.value_investment.turtle.calculations import compute_turtle_signals
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
)


def money(name, value, source, currency="CNY", unit="hundred_million"):
    return TurtleFactValue(
        name=name,
        value=MoneyAmount(value, currency, unit, "fixture", source),
        source_label="fixture",
        source_reference=source,
    )


def number(name, value, source):
    return TurtleFactValue(name=name, value=value, source_label="fixture", source_reference=source)


def fact_value(name, value, source, reliability="reliable"):
    return TurtleFactValue(
        name=name,
        value=value,
        source_label="fixture",
        source_reference=source,
        reliability=reliability,
    )


def base_facts(*, market="A", report_fields=None, market_fields=None, report_metadata=None, caveats=None, status="complete"):
    context = TurtleRunContext.for_ticker(
        ticker="600519",
        market=market,
        trade_date="2026-05-19",
        company_name="贵州茅台",
    )
    report_defaults = {
        "net_profit": money("net_profit", 100, "report.net_profit"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf"),
        "capex": money("capex", 20, "report.capex"),
        "cash": money("cash", 500, "report.cash"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt"),
    }
    if report_fields:
        report_defaults.update(report_fields)
    report = TurtleReportFacts(fields=report_defaults, metadata=report_metadata or {})
    market_defaults = {
        "market_cap": money("market_cap", 1000, "market.market_cap"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback"),
        "avg_payout_ratio_3y": number("avg_payout_ratio_3y", 0.5, "market.payout"),
        "tax_rate": number("tax_rate", 0.2, "market.tax"),
        "rf_rate": number("rf_rate", 0.03, "market.rf"),
    }
    if market_fields:
        market_defaults.update(market_fields)
    market_facts = TurtleMarketFacts(fields=market_defaults, caveats=caveats or [])
    return TurtleFacts(context=context, report=report, market=market_facts, status=status)


def test_compute_turtle_signals_calculates_r_gg_hh():
    signals = compute_turtle_signals(base_facts())

    assert signals.status == "complete"
    assert signals.results["payout_anchor"].value == 0.5
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["GG"].value == pytest.approx(5.0)
    assert signals.results["HH"].value == 0.0
    assert "100 * 0.5 * (1 - 0.2) + 10" in signals.results["R"].substitution


def test_compute_turtle_signals_switches_to_ev_when_cash_is_large():
    signals = compute_turtle_signals(base_facts())

    assert signals.results["net_cash_ratio"].value == 45.0
    assert signals.results["ev_switch"].value == 1.0
    assert signals.results["cash_protection"].value == 20.0


def test_compute_turtle_signals_is_non_decisionable_without_market_cap():
    facts = base_facts()
    market = TurtleMarketFacts(fields={key: value for key, value in facts.market.fields.items() if key != "market_cap"})
    broken = TurtleFacts(context=facts.context, report=facts.report, market=market, status="complete")

    signals = compute_turtle_signals(broken)

    assert signals.status == "non_decisionable"
    assert "market_cap" in signals.results["R"].missing_inputs


def test_compute_turtle_signals_accepts_integrated_dividend_payout_field():
    facts = base_facts(market_fields={
        "avg_payout_ratio_3y": None,
        "dividend_avg_payout_ratio_3y": number("dividend_avg_payout_ratio_3y", 0.5, "dividend_data.avg_payout_ratio_3y"),
    })
    market = TurtleMarketFacts(fields={key: value for key, value in facts.market.fields.items() if value is not None})
    facts = TurtleFacts(context=facts.context, report=facts.report, market=market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.status == "complete"
    assert signals.results["payout_anchor"].value == 0.5
    assert "dividend_data.avg_payout_ratio_3y" in signals.results["payout_anchor"].sources


def test_compute_turtle_signals_degrades_without_buyback():
    facts = base_facts()
    market = TurtleMarketFacts(fields={key: value for key, value in facts.market.fields.items() if key != "buyback_amount"})
    broken = TurtleFacts(context=facts.context, report=facts.report, market=market, status="complete")

    signals = compute_turtle_signals(broken)

    assert signals.status == "degraded"
    assert signals.results["R"].status == "degraded"
    assert signals.results["GG"].status == "degraded"
    assert signals.results["R"].value == pytest.approx(4.0)
    assert signals.results["GG"].value == pytest.approx(4.0)
    assert "buyback_amount" in signals.results["GG"].missing_inputs
    assert "buyback_amount missing; treated as 0 for degraded calculation" in signals.caveats


def test_compute_turtle_signals_degrades_when_only_buyback_currency_needs_missing_fx():
    hkd_report = {
        "net_profit": money("net_profit", 100, "report.net_profit", currency="HKD"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="HKD"),
        "capex": money("capex", 20, "report.capex", currency="HKD"),
        "cash": money("cash", 500, "report.cash", currency="HKD"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="HKD"),
    }
    market_fields = {
        "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="CNY"),
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=market_fields,
    ))

    assert signals.status == "degraded"
    assert signals.results["R"].status == "degraded"
    assert signals.results["R"].value == pytest.approx(4.0)
    assert signals.results["GG"].value == pytest.approx(4.0)
    assert "buyback_amount" in signals.results["R"].missing_inputs
    assert "FX rate required for CNY:HKD" in signals.caveats


def test_compute_turtle_signals_keeps_hh_complete_without_buyback():
    facts = base_facts()
    market = TurtleMarketFacts(fields={key: value for key, value in facts.market.fields.items() if key != "buyback_amount"})
    broken = TurtleFacts(context=facts.context, report=facts.report, market=market, status="complete")

    signals = compute_turtle_signals(broken)

    assert signals.results["R"].status == "degraded"
    assert signals.results["GG"].status == "degraded"
    assert signals.results["HH"].status == "complete"
    assert signals.results["HH"].value == pytest.approx(0.0)
    assert "buyback_amount" not in signals.results["HH"].missing_inputs


def test_compute_turtle_signals_uses_reliable_market_fallback_when_report_fact_display_only():
    signals = compute_turtle_signals(base_facts(
        report_fields={
            "net_profit": fact_value(
                "net_profit",
                MoneyAmount(999, "CNY", "hundred_million", "fixture", "report.net_profit"),
                "report.net_profit",
                reliability="display_only",
            ),
        },
        market_fields={
            "net_profit": money("net_profit", 100, "market.net_profit"),
        },
    ))

    assert signals.results["R"].value == pytest.approx(5.0)
    assert "net_profit" not in signals.results["R"].missing_inputs
    assert "market.net_profit" in signals.results["R"].sources


def test_compute_turtle_signals_uses_reliable_later_numeric_alias_when_first_alias_display_only():
    signals = compute_turtle_signals(base_facts(market_fields={
        "avg_payout_ratio_3y": fact_value("avg_payout_ratio_3y", 0.9, "market.stale_payout", reliability="display_only"),
        "dividend_avg_payout_ratio_3y": number("dividend_avg_payout_ratio_3y", 0.5, "dividend_data.avg_payout_ratio_3y"),
    }))

    assert signals.results["payout_anchor"].value == pytest.approx(0.5)
    assert "avg_payout_ratio_3y" not in signals.results["payout_anchor"].missing_inputs
    assert "dividend_data.avg_payout_ratio_3y" in signals.results["payout_anchor"].sources


def test_compute_turtle_signals_rejects_zero_market_cap():
    signals = compute_turtle_signals(base_facts(market_fields={
        "market_cap": money("market_cap", 0, "market.market_cap"),
    }))

    assert signals.status == "non_decisionable"
    assert signals.results["R"].value is None
    assert "market_cap" in signals.results["R"].missing_inputs
    assert "market_cap must be positive" in signals.caveats


def test_compute_turtle_signals_rejects_negative_market_cap():
    signals = compute_turtle_signals(base_facts(market_fields={
        "market_cap": money("market_cap", -1000, "market.market_cap"),
    }))

    assert signals.status == "non_decisionable"
    assert signals.results["GG"].value is None
    assert "market_cap" in signals.results["GG"].missing_inputs
    assert signals.results["net_cash_ratio"].value is None
    assert "market_cap" in signals.results["net_cash_ratio"].missing_inputs
    assert "market_cap must be positive" in signals.caveats


def test_compute_turtle_signals_marks_ev_related_results_non_decisionable_without_market_cap():
    facts = base_facts()
    market = TurtleMarketFacts(fields={key: value for key, value in facts.market.fields.items() if key != "market_cap"})
    broken = TurtleFacts(context=facts.context, report=facts.report, market=market, status="complete")

    signals = compute_turtle_signals(broken)

    assert signals.results["net_cash_ratio"].status == "non_decisionable"
    assert signals.results["ev_switch"].status == "non_decisionable"
    assert signals.results["cash_protection"].status == "non_decisionable"


def test_compute_turtle_signals_preserves_unsupported_input_status():
    signals = compute_turtle_signals(base_facts(status="unsupported"))

    assert signals.status == "unsupported"
    assert signals.results == {}


def test_compute_turtle_signals_degrades_when_caveats_exist_with_complete_critical_results():
    facts = base_facts(caveats=["rf_rate missing"])

    signals = compute_turtle_signals(facts)

    assert signals.status == "degraded"
    assert signals.results["R"].status == "complete"
    assert "rf_rate missing" in signals.caveats


def test_compute_turtle_signals_treats_money_conversion_failure_as_missing():
    signals = compute_turtle_signals(base_facts(report_fields={
        "net_profit": money("net_profit", 100, "report.net_profit", unit="billion"),
    }))

    assert signals.status == "non_decisionable"
    assert signals.results["R"].value is None
    assert "net_profit" in signals.results["R"].missing_inputs


def test_compute_turtle_signals_target_currency_ignores_invalid_first_money_candidate():
    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields={
            "net_profit": money("net_profit", 100, "report.invalid_net_profit", currency="CNY", unit="billion"),
            "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="HKD"),
            "capex": money("capex", 20, "report.capex", currency="HKD"),
            "cash": money("cash", 500, "report.cash", currency="HKD"),
            "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="HKD"),
        },
        market_fields={
            "net_profit": money("net_profit", 100, "market.net_profit", currency="HKD"),
            "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
            "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
        },
    ))

    assert signals.status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert "market.net_profit" in signals.results["R"].sources
    assert "Unsupported money unit: billion" in signals.caveats
    assert "FX rate required" not in " ".join(signals.caveats)


def test_compute_turtle_signals_rejects_bool_money_value():
    signals = compute_turtle_signals(base_facts(report_fields={
        "net_profit": money("net_profit", True, "report.net_profit"),
    }))

    assert signals.status == "non_decisionable"
    assert signals.results["R"].value is None
    assert "net_profit" in signals.results["R"].missing_inputs
    assert "net_profit invalid money value" in signals.caveats


def test_compute_turtle_signals_rejects_display_only_money_fact_for_critical_formula():
    signals = compute_turtle_signals(base_facts(report_fields={
        "net_profit": fact_value(
            "net_profit",
            MoneyAmount(100, "CNY", "hundred_million", "fixture", "report.net_profit"),
            "report.net_profit",
            reliability="display_only",
        ),
    }))

    assert signals.status == "non_decisionable"
    assert signals.results["R"].value is None
    assert "net_profit" in signals.results["R"].missing_inputs
    assert "net_profit unreliable: display_only" in signals.caveats


def test_compute_turtle_signals_rejects_display_only_numeric_fact_for_critical_formula():
    signals = compute_turtle_signals(base_facts(market_fields={
        "tax_rate": fact_value("tax_rate", 0.2, "market.tax", reliability="display_only"),
    }))

    assert signals.status == "non_decisionable"
    assert signals.results["R"].value is None
    assert "tax_rate" in signals.results["R"].missing_inputs
    assert "tax_rate unreliable: display_only" in signals.caveats


def test_compute_turtle_signals_converts_mixed_hkd_money_with_report_fx_rates():
    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields={
            "net_profit": money("net_profit", 92, "report.net_profit", currency="CNY"),
            "operating_cash_flow": money("operating_cash_flow", 110.4, "report.ocf", currency="CNY"),
            "capex": money("capex", 18.4, "report.capex", currency="CNY"),
            "cash": money("cash", 460, "report.cash", currency="CNY"),
            "interest_bearing_debt": money("interest_bearing_debt", 46, "report.debt", currency="CNY"),
        },
        market_fields={
            "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
            "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
        },
        report_metadata={"fx_rates": {"HKD:CNY": 0.92}},
    ))

    assert signals.status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["GG"].value == pytest.approx(5.0)
    assert any("FX HKD:CNY=0.92" in source for source in signals.results["R"].sources)


def test_compute_turtle_signals_uses_common_hkd_currency_without_fx_rates():
    hkd_report = {
        "net_profit": money("net_profit", 100, "report.net_profit", currency="HKD"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="HKD"),
        "capex": money("capex", 20, "report.capex", currency="HKD"),
        "cash": money("cash", 500, "report.cash", currency="HKD"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="HKD"),
    }
    hkd_market = {
        "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
    ))

    assert signals.status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["GG"].value == pytest.approx(5.0)
    assert signals.results["owner_earnings"].unit == "hundred_million HKD"
    assert "FX rate required for HKD:CNY" not in signals.caveats


def test_compute_turtle_signals_keeps_r_gg_complete_when_only_net_cash_currency_mixed():
    hkd_report = {
        "net_profit": money("net_profit", 100, "report.net_profit", currency="HKD"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="HKD"),
        "capex": money("capex", 20, "report.capex", currency="HKD"),
        "cash": money("cash", 500, "report.cash", currency="CNY"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="CNY"),
    }
    hkd_market = {
        "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
    ))

    assert signals.status == "degraded"
    assert signals.results["R"].status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["GG"].status == "complete"
    assert signals.results["GG"].value == pytest.approx(5.0)
    assert "market_cap" not in signals.results["R"].missing_inputs
    assert "cash" not in signals.results["R"].missing_inputs
    assert "interest_bearing_debt" not in signals.results["R"].missing_inputs
    assert "market_cap" not in signals.results["GG"].missing_inputs
    assert "cash" not in signals.results["GG"].missing_inputs
    assert "interest_bearing_debt" not in signals.results["GG"].missing_inputs
    assert signals.results["net_cash_ratio"].status == "non_decisionable"
    assert signals.results["net_cash_ratio"].value is None
    assert "market_cap" in signals.results["net_cash_ratio"].missing_inputs
    assert signals.results["ev_switch"].status == "non_decisionable"
    assert signals.results["cash_protection"].status == "non_decisionable"
    assert "FX rate required for HKD:CNY" in signals.caveats


def test_compute_turtle_signals_ignores_unrelated_money_field_currency_for_hkd_native_calculation():
    hkd_report = {
        "net_profit": money("net_profit", 100, "report.net_profit", currency="HKD"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="HKD"),
        "capex": money("capex", 20, "report.capex", currency="HKD"),
        "cash": money("cash", 500, "report.cash", currency="HKD"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="HKD"),
        "revenue": money("revenue", 999, "report.revenue", currency="USD"),
    }
    hkd_market = {
        "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
    ))

    assert signals.status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["owner_earnings"].unit == "hundred_million HKD"
    assert "FX rate required for HKD:CNY" not in signals.caveats


def test_compute_turtle_signals_ignores_unrelated_fx_rates_for_hkd_native_calculation():
    hkd_report = {
        "net_profit": money("net_profit", 100, "report.net_profit", currency="HKD"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="HKD"),
        "capex": money("capex", 20, "report.capex", currency="HKD"),
        "cash": money("cash", 500, "report.cash", currency="HKD"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="HKD"),
    }
    hkd_market = {
        "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
        report_metadata={"fx_rates": {"USD:CNY": 7.2}},
    ))

    assert signals.status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["owner_earnings"].unit == "hundred_million HKD"
    assert "FX rate required for HKD:CNY" not in signals.caveats


def test_compute_turtle_signals_mixed_formula_currencies_require_relevant_fx():
    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields={
            "net_profit": money("net_profit", 100, "report.net_profit", currency="CNY"),
            "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="CNY"),
            "capex": money("capex", 20, "report.capex", currency="CNY"),
            "cash": money("cash", 500, "report.cash", currency="CNY"),
            "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="CNY"),
        },
        market_fields={
            "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
            "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
        },
        report_metadata={"fx_rates": {"USD:CNY": 7.2}},
    ))

    assert signals.status == "non_decisionable"
    assert signals.results["R"].value is None
    assert "market_cap" in signals.results["R"].missing_inputs
    assert "FX rate required for HKD:CNY" in signals.caveats


def test_compute_turtle_signals_is_non_decisionable_when_hk_fx_rate_missing():
    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields={"net_profit": money("net_profit", 100, "report.net_profit", currency="HKD")},
    ))

    assert signals.status == "non_decisionable"
    assert signals.results["R"].value is None
    assert "net_profit" in signals.results["R"].missing_inputs
    assert "FX rate required for HKD:CNY" in signals.caveats


class TestEvSwitchAndCashProtectionNonDecisionable:
    """A.5: 当 net_cash_ratio 缺输入时，ev_switch / cash_protection 应为 non_decisionable，不再有 degraded 中间态。"""

    def _facts_missing_cash(self):
        """构造 facts：market_cap 完整，但 cash / debt 缺失。"""
        facts = base_facts()
        report_without_cash = TurtleReportFacts(
            fields={k: v for k, v in facts.report.fields.items() if k not in ("cash", "interest_bearing_debt")},
            metadata=facts.report.metadata,
        )
        return TurtleFacts(context=facts.context, report=report_without_cash, market=facts.market, status="complete")

    def test_ev_switch_non_decisionable_when_cash_missing(self):
        facts = self._facts_missing_cash()
        signals = compute_turtle_signals(facts)
        assert signals.results["ev_switch"].status == "non_decisionable"
        assert signals.results["ev_switch"].value is None

    def test_cash_protection_non_decisionable_when_cash_missing(self):
        facts = self._facts_missing_cash()
        signals = compute_turtle_signals(facts)
        assert signals.results["cash_protection"].status == "non_decisionable"
        assert signals.results["cash_protection"].value is None

    def test_net_cash_ratio_non_decisionable_when_cash_missing(self):
        facts = self._facts_missing_cash()
        signals = compute_turtle_signals(facts)
        assert signals.results["net_cash_ratio"].status == "non_decisionable"
        assert signals.results["net_cash_ratio"].value is None
