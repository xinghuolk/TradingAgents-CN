import json
from decimal import Decimal

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
    normalize_currency,
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


def test_turtle_fact_serializer_converts_decimal_values_to_json_safe_numbers():
    report = TurtleReportFacts(
        fields={
            "display_only_amount": TurtleFactValue(
                name="display_only_amount",
                value=Decimal("123.45"),
                source_label="financial-report-client",
                source_reference="field p.1",
                reliability="display_only",
            )
        }
    )

    payload = report.to_dict()

    assert payload["fields"]["display_only_amount"]["value"] == 123.45
    json.dumps(payload)


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


class TestTurtleReportFactsHistorical:
    def test_historical_defaults_to_empty(self):
        facts = TurtleReportFacts()
        assert facts.historical == {}

    def test_historical_stores_prior_periods(self):
        prior = TurtleReportFacts(status="complete")
        facts = TurtleReportFacts(historical={"2023-12-31": prior})
        assert "2023-12-31" in facts.historical
        assert facts.historical["2023-12-31"].status == "complete"

    def test_to_dict_includes_historical(self):
        prior = TurtleReportFacts(status="degraded", caveats=["x"])
        facts = TurtleReportFacts(historical={"2023-12-31": prior})
        d = facts.to_dict()
        assert "historical" in d
        assert d["historical"]["2023-12-31"]["status"] == "degraded"
        assert d["historical"]["2023-12-31"]["caveats"] == ["x"]

    def test_nested_historical_stripped(self):
        deep = TurtleReportFacts(status="complete")
        mid = TurtleReportFacts(status="complete", historical={"2022-12-31": deep})
        facts = TurtleReportFacts(historical={"2023-12-31": mid})
        assert facts.historical["2023-12-31"].historical == {}

    def test_empty_historical_dict_round_trips(self):
        facts = TurtleReportFacts()
        assert facts.to_dict()["historical"] == {}

    def test_historical_child_is_defensively_copied(self):
        child = TurtleReportFacts(caveats=["original"])
        parent = TurtleReportFacts(historical={"2023-12-31": child})
        child.caveats.append("leaked")
        assert parent.historical["2023-12-31"].caveats == ["original"]


@pytest.mark.parametrize("raw,expected", [
    ("RMB", "CNY"), ("rmb", "CNY"), ("CNY", "CNY"), ("人民币", "CNY"), ("元", "CNY"),
    ("HKD", "HKD"), ("HK$", "HKD"), ("港币", "HKD"), ("港元", "HKD"),
    ("USD", "USD"), ("US$", "USD"), ("美元", "USD"),
    ("EUR", "EUR"), ("  hkd  ", "HKD"),
])
def test_normalize_currency_aliases(raw, expected):
    assert normalize_currency(raw) == expected


def _money(value, currency, unit="yuan"):
    return MoneyAmount(value=value, currency=currency, unit=unit, source_label="t", source_reference="ref")


def test_to_hundred_million_rmb_alias_normalizes_no_fx():
    m = _money(100_000_000, "RMB").to_hundred_million(target_currency="CNY", fx_rates={})
    assert m.currency == "CNY"
    assert m.value == pytest.approx(1.0)


def test_to_hundred_million_hk_dollar_alias_uses_hkd_pair():
    m = _money(100_000_000, "HK$").to_hundred_million(target_currency="CNY", fx_rates={"HKD:CNY": 0.9})
    assert m.currency == "CNY"
    assert m.value == pytest.approx(0.9)


def test_to_hundred_million_missing_fx_still_raises():
    with pytest.raises(ValueError):
        _money(100_000_000, "HKD").to_hundred_million(target_currency="CNY", fx_rates={})


def test_market_facts_metadata_roundtrip():
    mf = TurtleMarketFacts(fields={}, caveats=[], status="complete", metadata={"market_as_of": "2026-05-23"})
    assert mf.metadata == {"market_as_of": "2026-05-23"}
    assert mf.to_dict()["metadata"] == {"market_as_of": "2026-05-23"}


def test_market_facts_metadata_defaults_empty():
    mf = TurtleMarketFacts()
    assert mf.metadata == {}
    assert mf.to_dict()["metadata"] == {}


def test_market_facts_metadata_defensive_copy():
    src = {"market_as_of": "2026-05-23"}
    mf = TurtleMarketFacts(metadata=src)
    src["market_as_of"] = "MUTATED"
    assert mf.metadata["market_as_of"] == "2026-05-23"
