import pytest

from tradingagents.dataflows.value_investment.turtle.facts import (
    FormulaResult,
    MoneyAmount,
    TurtleComputedSignals,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
    infer_turtle_period_end,
    merge_status,
)


def test_infer_turtle_period_end_uses_march_cutoff():
    assert infer_turtle_period_end("2026-03-31") == "2024-12-31"
    assert infer_turtle_period_end("2026-04-01") == "2025-12-31"
    assert infer_turtle_period_end("2026-12-31") == "2025-12-31"


def test_infer_turtle_period_end_rejects_blank_reference_date():
    with pytest.raises(ValueError, match="reference_date cannot be blank"):
        infer_turtle_period_end("")

    with pytest.raises(ValueError, match="reference_date cannot be blank"):
        infer_turtle_period_end("   ")


def test_money_amount_converts_report_units_to_hundred_million():
    assert MoneyAmount(100, "CNY", "million", "src", "ref").to_hundred_million().value == pytest.approx(1.0)
    assert MoneyAmount(100000, "CNY", "thousand", "src", "ref").to_hundred_million().value == pytest.approx(1.0)
    assert MoneyAmount(10000, "CNY", "ten_thousand", "src", "ref").to_hundred_million().value == pytest.approx(1.0)
    assert MoneyAmount(3, "CNY", "hundred_million", "src", "ref").to_hundred_million().value == pytest.approx(3.0)


def test_money_amount_rejects_unsupported_runtime_unit():
    amount = MoneyAmount(100, "CNY", "billion", "src", "ref")

    with pytest.raises(ValueError, match="Unsupported money unit"):
        amount.to_hundred_million()


def test_money_amount_requires_fx_for_non_rmb_normalization():
    amount = MoneyAmount(100, "HKD", "million", "src", "ref")

    with pytest.raises(ValueError, match="FX rate required"):
        amount.to_hundred_million(target_currency="CNY")

    converted = amount.to_hundred_million(target_currency="CNY", fx_rates={"HKD:CNY": 0.92})

    assert converted.value == pytest.approx(0.92)
    assert converted.currency == "CNY"
    assert "FX HKD:CNY=0.92" in converted.source_reference


def test_turtle_run_context_tracks_defaults_and_period_end():
    context = TurtleRunContext.for_ticker(
        ticker="00001",
        market="HK",
        trade_date="2026-05-19",
        company_name="CK Hutchison",
    )

    assert context.period_end == "2025-12-31"
    assert context.holding_channel == "stock_connect"


def test_turtle_fact_serializers_return_defensive_copies():
    report = TurtleReportFacts(
        fields={
            "cash": TurtleFactValue(
                name="cash",
                value=MoneyAmount(100, "CNY", "million", "report", "page 1"),
                source_label="report",
                source_reference="page 1",
            )
        },
        metadata={"pages": [1, 2], "source": {"provider": "extractor"}},
        caveats=["report caveat"],
    )
    market = TurtleMarketFacts(
        fields={
            "price": TurtleFactValue(
                name="price",
                value=12.3,
                source_label="market",
                source_reference="quote",
            )
        },
        caveats=["market caveat"],
    )
    facts = TurtleFacts(
        context=TurtleRunContext.for_ticker(
            ticker="00001",
            market="HK",
            trade_date="2026-05-19",
            company_name="CK Hutchison",
        ),
        report=report,
        market=market,
        status="degraded",
        caveats=["combined caveat"],
    )
    signals = TurtleComputedSignals(
        status="degraded",
        results={
            "R": FormulaResult(
                name="R",
                formula="profit / market cap",
                substitution="1 / 10",
                value=0.1,
                unit="ratio",
                sources=["source 1"],
                missing_inputs=["input 1"],
                status="degraded",
            )
        },
        veto_reasons=["veto"],
        caveats=["signal caveat"],
    )

    report_payload = report.to_dict()
    market_payload = market.to_dict()
    facts_payload = facts.to_dict()
    signals_payload = signals.to_dict()

    report_payload["metadata"]["pages"].append(3)
    report_payload["metadata"]["source"]["provider"] = "mutated"
    report_payload["caveats"].append("mutated")
    market_payload["caveats"].append("mutated")
    facts_payload["caveats"].append("mutated")
    signals_payload["results"]["R"]["sources"].append("mutated")
    signals_payload["results"]["R"]["missing_inputs"].append("mutated")
    signals_payload["veto_reasons"].append("mutated")
    signals_payload["caveats"].append("mutated")

    assert report.metadata == {"pages": [1, 2], "source": {"provider": "extractor"}}
    assert report.caveats == ["report caveat"]
    assert market.caveats == ["market caveat"]
    assert facts.caveats == ["combined caveat"]
    assert signals.results["R"].sources == ["source 1"]
    assert signals.results["R"].missing_inputs == ["input 1"]
    assert signals.veto_reasons == ["veto"]
    assert signals.caveats == ["signal caveat"]


class TestMergeStatus:
    def test_single_status_passes_through(self):
        assert merge_status("complete") == "complete"
        assert merge_status("degraded") == "degraded"

    def test_picks_most_severe(self):
        assert merge_status("complete", "degraded") == "degraded"
        assert merge_status("degraded", "non_decisionable") == "non_decisionable"
        assert merge_status("complete", "non_decisionable") == "non_decisionable"

    def test_unsupported_dominates(self):
        assert merge_status("complete", "unsupported") == "unsupported"
        assert merge_status("non_decisionable", "unsupported") == "unsupported"

    def test_ordering_is_complete_lt_degraded_lt_non_decisionable_lt_unsupported(self):
        # 多参数 + 乱序也是最严重
        assert merge_status("unsupported", "complete", "degraded") == "unsupported"
        assert merge_status("degraded", "complete", "non_decisionable") == "non_decisionable"


class TestTurtleReportFactsStatus:
    def test_status_defaults_to_complete(self):
        facts = TurtleReportFacts()
        assert facts.status == "complete"

    def test_status_can_be_set(self):
        facts = TurtleReportFacts(status="degraded")
        assert facts.status == "degraded"

    def test_to_dict_includes_status(self):
        facts = TurtleReportFacts(status="non_decisionable", caveats=["x"])
        d = facts.to_dict()
        assert d["status"] == "non_decisionable"
        assert d["caveats"] == ["x"]


class TestTurtleMarketFactsStatus:
    def test_status_defaults_to_complete(self):
        facts = TurtleMarketFacts()
        assert facts.status == "complete"

    def test_status_can_be_set(self):
        facts = TurtleMarketFacts(status="non_decisionable")
        assert facts.status == "non_decisionable"

    def test_to_dict_includes_status(self):
        facts = TurtleMarketFacts(status="degraded", caveats=["y"])
        d = facts.to_dict()
        assert d["status"] == "degraded"
        assert d["caveats"] == ["y"]
