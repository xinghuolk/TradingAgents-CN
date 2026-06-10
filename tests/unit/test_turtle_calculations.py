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


def ratio(name, value, source, reliability="reliable"):
    return TurtleFactValue(
        name=name,
        value=value,
        source_label="fixture",
        source_reference=source,
        reliability=reliability,
    )


def fact_value(name, value, source, reliability="reliable"):
    return TurtleFactValue(
        name=name,
        value=value,
        source_label="fixture",
        source_reference=source,
        reliability=reliability,
    )


def report_history(*period_ratios, buyback_values=(10, 8), currency="CNY"):
    historical = {}
    for offset, payout_ratio in enumerate(period_ratios, start=1):
        fields = {}
        if payout_ratio is not None:
            fields["dividend_payout_ratio_current_year"] = ratio(
                "dividend_payout_ratio_current_year",
                payout_ratio,
                f"report.payout.{offset}",
            )
        if offset - 1 < len(buyback_values) and buyback_values[offset - 1] is not None:
            fields["buyback_amount"] = money(
                "buyback_amount",
                buyback_values[offset - 1],
                f"report.buyback.{offset}",
                currency=currency,
            )
        historical[f"{2025 - offset}-12-31"] = TurtleReportFacts(fields=fields)
    return historical


def base_facts(*, market="A", report_fields=None, market_fields=None,
               report_metadata=None, caveats=None, status="complete",
               report_currency="CNY"):
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
        "dividend_payout_ratio_current_year": ratio(
            "dividend_payout_ratio_current_year",
            0.5,
            "report.payout.latest",
        ),
        "buyback_amount": money(
            "buyback_amount",
            12,
            "report.buyback.latest",
            currency=report_currency,
        ),
    }
    if report_fields:
        report_defaults.update(report_fields)
    report = TurtleReportFacts(
        fields=report_defaults,
        metadata=report_metadata or {},
        historical=report_history(0.4, 0.6, currency=report_currency),
    )
    market_defaults = {
        "market_cap": money("market_cap", 1000, "market.market_cap"),
        "tax_rate": number("tax_rate", 0.2, "market.tax"),
        "rf_rate": number("rf_rate", 0.03, "market.rf"),
    }
    if market_fields:
        market_defaults.update(market_fields)
    market_facts = TurtleMarketFacts(fields=market_defaults, caveats=caveats or [])
    return TurtleFacts(context=context, report=report, market=market_facts, status=status)


def test_compute_turtle_signals_calculates_payout_m_r_gg_hh():
    signals = compute_turtle_signals(base_facts())

    assert signals.status == "complete"
    assert "payout_M" in signals.results
    assert "payout_anchor" not in signals.results
    assert signals.results["payout_M"].value == pytest.approx(0.5)
    assert signals.results["payout_M"].status == "complete"
    assert "commitment_constraint_applied=true" in signals.results["payout_M"].substitution
    assert "commitment_ratio=0.4" in signals.results["payout_M"].substitution
    assert ") = 0.5;" in signals.results["payout_M"].substitution  # selected value in substitution
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["GG"].value == pytest.approx(5.0)
    assert signals.results["HH"].value == 0.0
    assert "100 * 0.5 * (1 - 0.2) + 10" in signals.results["R"].substitution


def test_compute_turtle_signals_payout_m_uses_latest_signal_when_above_three_year_average():
    facts = base_facts(report_fields={
        "dividend_payout_ratio_current_year": ratio(
            "dividend_payout_ratio_current_year",
            0.8,
            "report.payout.latest",
        ),
    })

    signals = compute_turtle_signals(facts)

    assert signals.results["payout_M"].value == pytest.approx(0.8)
    assert "payout_3y_avg=0.6" in signals.results["payout_M"].substitution
    assert "latest_signal=0.8" in signals.results["payout_M"].substitution
    assert ") = 0.8;" in signals.results["payout_M"].substitution  # selected value in substitution


def test_compute_turtle_signals_payout_m_ignores_market_payout_fields():
    signals = compute_turtle_signals(base_facts(market_fields={
        "avg_payout_ratio_3y": number("avg_payout_ratio_3y", 0.99, "market.payout"),
        "dividend_avg_payout_ratio_3y": number(
            "dividend_avg_payout_ratio_3y",
            0.88,
            "dividend_data.avg_payout_ratio_3y",
        ),
    }))

    assert signals.results["payout_M"].value == pytest.approx(0.5)
    assert "market.payout" not in signals.results["payout_M"].sources
    assert "dividend_data.avg_payout_ratio_3y" not in signals.results["payout_M"].sources


def test_compute_turtle_signals_payout_average_is_mean_of_per_year_ratios():
    facts = base_facts(report_fields={
        "dividend_payout_ratio_current_year": ratio(
            "dividend_payout_ratio_current_year",
            0.9,
            "report.payout.latest",
        ),
    })
    report = TurtleReportFacts(
        fields=facts.report.fields,
        metadata=facts.report.metadata,
        historical=report_history(0.3, 0.3),
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.results["payout_M"].value == pytest.approx(0.9)
    assert "payout_3y_avg=0.5" in signals.results["payout_M"].substitution


def test_compute_turtle_signals_payout_m_degraded_with_two_period_average():
    facts = base_facts()
    report = TurtleReportFacts(
        fields=facts.report.fields,
        metadata=facts.report.metadata,
        historical=report_history(0.4, None),
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.results["payout_M"].value == pytest.approx(0.5)
    assert "payout_3y_avg=0.45" in signals.results["payout_M"].substitution
    assert signals.results["payout_M"].status == "degraded"
    assert "dividend_payout_ratio_current_year_3y_avg computed from 2/3 periods" in signals.caveats


def test_compute_turtle_signals_degraded_payout_m_propagates_to_r_gg_hh():
    # Spec §5.4: formulas consuming a degraded payout_M are themselves degraded
    # (buyback degradation is handled separately and excluded from HH).
    facts = base_facts()
    report = TurtleReportFacts(
        fields=facts.report.fields,
        metadata=facts.report.metadata,
        historical=report_history(0.4, None),  # 2/3 periods -> payout_M degraded
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.results["payout_M"].status == "degraded"
    assert signals.results["R"].status == "degraded"
    assert signals.results["GG"].status == "degraded"
    assert signals.results["HH"].status == "degraded"
    assert signals.results["R"].value == pytest.approx(5.0)  # value still computed
    assert signals.results["HH"].value == pytest.approx(0.0)
    assert signals.status == "degraded"


def test_compute_turtle_signals_payout_m_degraded_with_latest_only():
    facts = base_facts()
    report = TurtleReportFacts(
        fields=facts.report.fields,
        metadata=facts.report.metadata,
        historical={},
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.results["payout_M"].value == pytest.approx(0.5)
    assert signals.results["payout_M"].status == "degraded"
    assert "dividend_payout_ratio_current_year_3y_avg" in signals.results["payout_M"].missing_inputs


def test_compute_turtle_signals_payout_m_non_decisionable_when_no_report_payout_inputs():
    facts = base_facts()
    report = TurtleReportFacts(
        fields={k: v for k, v in facts.report.fields.items() if k != "dividend_payout_ratio_current_year"},
        metadata=facts.report.metadata,
        historical={},
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.status == "non_decisionable"
    assert signals.results["payout_M"].status == "non_decisionable"
    assert "dividend_payout_ratio_current_year" in signals.results["payout_M"].missing_inputs
    assert "dividend_payout_ratio_current_year_3y_avg" in signals.results["payout_M"].missing_inputs


def test_compute_turtle_signals_payout_m_degraded_when_only_three_year_average_present():
    facts = base_facts()
    report = TurtleReportFacts(
        fields={k: v for k, v in facts.report.fields.items() if k != "dividend_payout_ratio_current_year"},
        metadata=facts.report.metadata,
        historical=report_history(0.4, 0.6),
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.status == "degraded"
    # commitment cap applies (2 historical years): commitment_ratio = min(0.4, 0.6) = 0.4,
    # payout_3y_avg = 0.5, latest_signal missing -> payout_M = max(capped_avg=0.4) = 0.4 (latest_signal absent, filtered out)
    assert signals.results["payout_M"].value == pytest.approx(0.4)
    assert signals.results["payout_M"].status == "degraded"
    assert "dividend_payout_ratio_current_year" in signals.results["payout_M"].missing_inputs


def test_compute_turtle_signals_uses_report_side_buyback_3y_average():
    signals = compute_turtle_signals(base_facts())

    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["GG"].value == pytest.approx(5.0)
    assert "buyback_amount_3y_avg" in signals.results["R"].formula
    assert "+ 10" in signals.results["R"].substitution
    assert "market.buyback" not in signals.results["R"].sources


def test_compute_turtle_signals_normalizes_negative_report_buyback_for_formula():
    facts = base_facts(report_fields={
        "buyback_amount": money("buyback_amount", -12, "report.buyback.latest"),
    })
    report = TurtleReportFacts(
        fields=facts.report.fields,
        metadata=facts.report.metadata,
        historical=report_history(0.4, 0.6, buyback_values=(-10, -8)),
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["GG"].value == pytest.approx(5.0)
    assert "+ 10" in signals.results["R"].substitution
    assert "+ 10" in signals.results["GG"].substitution


def test_compute_turtle_signals_ignores_market_buyback_when_report_buyback_exists():
    signals = compute_turtle_signals(base_facts(market_fields={
        "buyback_amount": money("buyback_amount", 999, "market.buyback"),
    }))

    assert signals.results["R"].value == pytest.approx(5.0)
    assert "market.buyback" not in signals.results["R"].sources


def test_compute_turtle_signals_report_buyback_missing_degrades_r_gg_only():
    facts = base_facts()
    report = TurtleReportFacts(
        fields={k: v for k, v in facts.report.fields.items() if k != "buyback_amount"},
        metadata=facts.report.metadata,
        historical={
            pe: TurtleReportFacts(
                fields={k: v for k, v in hist.fields.items() if k != "buyback_amount"},
                metadata=hist.metadata,
                caveats=hist.caveats,
                status=hist.status,
            )
            for pe, hist in facts.report.historical.items()
        },
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.status == "degraded"
    assert signals.results["R"].status == "degraded"
    assert signals.results["GG"].status == "degraded"
    assert signals.results["HH"].status == "complete"
    assert "buyback_amount_3y_avg" in signals.results["R"].missing_inputs
    assert "buyback_amount_3y_avg" in signals.results["GG"].missing_inputs
    assert "buyback_amount_3y_avg" not in signals.results["HH"].missing_inputs
    assert "buyback_amount_3y_avg missing; treated as 0 for degraded calculation" in signals.caveats


def test_compute_turtle_signals_market_buyback_only_is_excluded_from_formula():
    facts = base_facts(market_fields={
        "buyback_amount": money("buyback_amount", 999, "market.buyback"),
    })
    report = TurtleReportFacts(
        fields={k: v for k, v in facts.report.fields.items() if k != "buyback_amount"},
        metadata=facts.report.metadata,
        historical={
            pe: TurtleReportFacts(fields={
                k: v for k, v in hist.fields.items() if k != "buyback_amount"
            })
            for pe, hist in facts.report.historical.items()
        },
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)

    assert signals.results["R"].value == pytest.approx(4.0)
    assert "market.buyback" not in signals.results["R"].sources
    assert "market buyback_amount present but excluded from R/GG" in " ".join(signals.caveats)


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


def test_compute_turtle_signals_degrades_when_material_caveat_exists_with_complete_results():
    facts = base_facts(caveats=["rf_rate missing"])

    signals = compute_turtle_signals(facts)

    assert signals.status == "degraded"
    assert signals.results["R"].status == "complete"
    assert "rf_rate missing" in signals.caveats


def test_compute_turtle_signals_does_not_degrade_for_context_only_action_caveat():
    facts = base_facts(caveats=["dividend data missing", "buyback data missing"])

    signals = compute_turtle_signals(facts)

    assert signals.status == "complete"
    assert signals.results["R"].status == "complete"
    assert "dividend data missing" in signals.caveats
    assert "buyback data missing" in signals.caveats


def test_compute_turtle_signals_ignores_degraded_facts_status_when_core_results_complete():
    facts = base_facts(status="degraded", caveats=["single-year report payout proxy; not a 3-year average"])

    signals = compute_turtle_signals(facts)

    assert signals.status == "complete"
    assert signals.results["R"].status == "complete"


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
        },
        report_currency="HKD",
    ))

    assert signals.status == "degraded"
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
        },
        report_metadata={"fx_rates": {"HKD:CNY": 0.92}},
        report_currency="HKD",
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
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
        report_currency="HKD",
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
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
        report_currency="HKD",
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
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
        report_currency="HKD",
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
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
        report_metadata={"fx_rates": {"USD:CNY": 7.2}},
        report_currency="HKD",
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
        },
        report_metadata={"fx_rates": {"USD:CNY": 7.2}},
        report_currency="CNY",
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


from tradingagents.dataflows.value_investment.turtle.calculations import (
    _money_hm_report_3y_avg,
)


def _money_fact_3y(value, currency="CNY"):
    return TurtleFactValue(
        name="net_profit",
        value=MoneyAmount(
            value=value, currency=currency, unit="hundred_million",
            source_label="report", source_reference=f"p.{value}",
        ),
        source_label="report",
        source_reference=f"p.{value}",
        reliability="reliable",
    )


def _facts_money_history(latest_val, history_vals):
    """latest_val + per-year historical money facts for 'net_profit'.

    history_vals: list of values (None means that period has no net_profit fact).
    """
    ctx = TurtleRunContext(
        ticker="600519", market="A", trade_date="2025-05-19",
        period_end="2024-12-31", holding_channel="long_term_domestic",
        company_name="X",
    )
    historical = {}
    for i, v in enumerate(history_vals, start=1):
        pe = f"{2024 - i}-12-31"
        fields = {"net_profit": _money_fact_3y(v)} if v is not None else {}
        historical[pe] = TurtleReportFacts(fields=fields)
    report_fields = {"net_profit": _money_fact_3y(latest_val)} if latest_val is not None else {}
    report = TurtleReportFacts(fields=report_fields, historical=historical)
    return TurtleFacts(
        context=ctx, report=report, market=TurtleMarketFacts(),
        status="complete", caveats=[],
    )


class TestMoneyHM3yAvg:
    def test_three_periods_mean(self):
        facts = _facts_money_history(100.0, [90.0, 110.0])
        caveats = []
        value, sources, missing = _money_hm_report_3y_avg(facts, "net_profit", caveats, "CNY")
        assert value == 100.0   # mean(100, 90, 110)
        assert missing == []
        assert len(sources) == 3

    def test_two_periods_mean_with_caveat(self):
        facts = _facts_money_history(100.0, [80.0, None])
        caveats = []
        value, sources, missing = _money_hm_report_3y_avg(facts, "net_profit", caveats, "CNY")
        assert value == 90.0    # mean(100, 80)
        assert missing == []
        assert any("2/3 periods" in c for c in caveats)

    def test_one_period_below_threshold(self):
        facts = _facts_money_history(100.0, [None, None])
        caveats = []
        value, sources, missing = _money_hm_report_3y_avg(facts, "net_profit", caveats, "CNY")
        assert value is None
        assert missing == ["net_profit_3y_avg"]

    def test_zero_periods(self):
        facts = _facts_money_history(None, [None, None])
        caveats = []
        value, sources, missing = _money_hm_report_3y_avg(facts, "net_profit", caveats, "CNY")
        assert value is None
        assert missing == ["net_profit_3y_avg"]


from tradingagents.dataflows.value_investment.turtle.calculations import (
    _number_report_3y_avg,
)


def _ratio_fact_3y(value):
    return TurtleFactValue(
        name="payout_ratio",
        value=value,
        source_label="report",
        source_reference=f"ratio.{value}",
        reliability="reliable",
    )


def _facts_ratio_history(latest_val, history_vals):
    ctx = TurtleRunContext(
        ticker="600519", market="A", trade_date="2025-05-19",
        period_end="2024-12-31", holding_channel="long_term_domestic",
        company_name="X",
    )
    historical = {}
    for i, v in enumerate(history_vals, start=1):
        pe = f"{2024 - i}-12-31"
        fields = {"payout_ratio": _ratio_fact_3y(v)} if v is not None else {}
        historical[pe] = TurtleReportFacts(fields=fields)
    report_fields = {"payout_ratio": _ratio_fact_3y(latest_val)} if latest_val is not None else {}
    report = TurtleReportFacts(fields=report_fields, historical=historical)
    return TurtleFacts(
        context=ctx, report=report, market=TurtleMarketFacts(),
        status="complete", caveats=[],
    )


class TestNumber3yAvg:
    def test_three_periods_mean(self):
        facts = _facts_ratio_history(0.6, [0.5, 0.4])
        caveats = []
        value, sources, missing = _number_report_3y_avg(facts, "payout_ratio", caveats)
        assert abs(value - 0.5) < 1e-9   # mean(0.6, 0.5, 0.4)
        assert missing == []

    def test_two_periods_with_caveat(self):
        facts = _facts_ratio_history(0.6, [0.4, None])
        caveats = []
        value, sources, missing = _number_report_3y_avg(facts, "payout_ratio", caveats)
        assert abs(value - 0.5) < 1e-9
        assert any("2/3 periods" in c for c in caveats)

    def test_one_period_below_threshold(self):
        facts = _facts_ratio_history(0.6, [None, None])
        caveats = []
        value, sources, missing = _number_report_3y_avg(facts, "payout_ratio", caveats)
        assert value is None
        assert missing == ["payout_ratio_3y_avg"]


import tradingagents.dataflows.value_investment.turtle.calculations as calc


def _facts_single_currency(report_currency, market_currency):
    ctx = TurtleRunContext.for_ticker(ticker="X", market="HK", trade_date="2026-05-23", company_name="X")
    np_ = MoneyAmount(value=1e8, currency=report_currency, unit="yuan", source_label="t", source_reference="r")
    mc = MoneyAmount(value=2e8, currency=market_currency, unit="yuan", source_label="t", source_reference="m")
    report = TurtleReportFacts(fields={
        "net_profit": TurtleFactValue(name="net_profit", value=np_, source_label="t", source_reference="r"),
    })
    market = TurtleMarketFacts(fields={
        "market_cap": TurtleFactValue(name="market_cap", value=mc, source_label="t", source_reference="m"),
    })
    return TurtleFacts(context=ctx, report=report, market=market, status="complete")


def test_money_fact_currencies_collapses_hk_dollar_alias():
    facts = _facts_single_currency("HK$", "HKD")
    assert calc._money_fact_currencies(facts, ("net_profit", "market_cap")) == {"HKD"}
    assert calc._money_target_currency(facts, ("net_profit", "market_cap")) == "HKD"


def test_money_fact_currencies_rmb_alias_is_cny():
    facts = _facts_single_currency("RMB", "CNY")
    assert calc._money_fact_currencies(facts, ("net_profit", "market_cap")) == {"CNY"}
    assert calc._money_target_currency(facts, ("net_profit", "market_cap")) == "CNY"


def test_payout_m_applies_commitment_cap_from_historical_min():
    # latest=0.4, historical=[0.5, 0.9]: avg=0.6, commitment=min(0.5,0.9)=0.5
    # payout_M = max(min(0.6, 0.5), 0.4) = 0.5  (今天会是 max(0.6, 0.4)=0.6)
    facts = base_facts(report_fields={
        "dividend_payout_ratio_current_year": ratio(
            "dividend_payout_ratio_current_year", 0.4, "report.payout.latest"
        ),
    })
    report = TurtleReportFacts(
        fields=facts.report.fields,
        metadata=facts.report.metadata,
        historical=report_history(0.5, 0.9),
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

    signals = compute_turtle_signals(facts)
    pm = signals.results["payout_M"]
    assert pm.value == pytest.approx(0.5)
    assert pm.status == "complete"
    assert "commitment_constraint_applied=true" in pm.substitution
    assert "commitment_ratio=0.5" in pm.substitution
    assert (
        "commitment cap applied; commitment_ratio = min of historical payout ratios"
        in signals.caveats
    )
    assert "commitment payout ratio not extracted" not in " ".join(signals.caveats)
    # 下游：payout 由 0.6 被压到 0.5 → R 相应下降（未应用时 R 会是 5.8）
    assert signals.results["R"].value == pytest.approx(5.0)


def test_payout_m_commitment_cap_not_binding_when_latest_is_highest():
    # latest=0.8, historical=[0.4,0.6]: avg=0.6, commitment=0.4
    # payout_M = max(min(0.6,0.4), 0.8) = max(0.4,0.8) = 0.8 (latest 占优)
    facts = base_facts(report_fields={
        "dividend_payout_ratio_current_year": ratio(
            "dividend_payout_ratio_current_year", 0.8, "report.payout.latest"
        ),
    })
    signals = compute_turtle_signals(facts)
    pm = signals.results["payout_M"]
    assert pm.value == pytest.approx(0.8)
    assert "commitment_constraint_applied=true" in pm.substitution


def test_payout_m_commitment_cap_noop_when_all_years_equal():
    facts = base_facts(report_fields={
        "dividend_payout_ratio_current_year": ratio(
            "dividend_payout_ratio_current_year", 0.5, "report.payout.latest"
        ),
    })
    report = TurtleReportFacts(
        fields=facts.report.fields,
        metadata=facts.report.metadata,
        historical=report_history(0.5, 0.5),
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")
    signals = compute_turtle_signals(facts)
    pm = signals.results["payout_M"]
    assert pm.value == pytest.approx(0.5)
    assert "commitment_constraint_applied=true" in pm.substitution
    assert "commitment_ratio=0.5" in pm.substitution


def test_payout_m_falls_back_when_fewer_than_two_historical_years():
    # 只有 1 个历史年(2024=0.4, 2023 无 payout) → commitment 不可派生 → 回退到 max(avg, latest)
    facts = base_facts()
    report = TurtleReportFacts(
        fields=facts.report.fields,
        metadata=facts.report.metadata,
        historical=report_history(0.4, None),
    )
    facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")
    signals = compute_turtle_signals(facts)
    pm = signals.results["payout_M"]
    assert pm.value == pytest.approx(0.5)  # max(payout_3y_avg=0.45, latest=0.5)
    assert pm.status == "degraded"
    assert "commitment_ratio=null, commitment_constraint_applied=false" in pm.substitution
    assert (
        "commitment payout ratio not extracted; payout_M uses max(payout_3y_avg, latest_signal) "
        "without commitment cap"
    ) in signals.caveats
