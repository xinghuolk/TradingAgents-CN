from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    build_report_facts_from_extraction,
    get_turtle_report_facts,
)
from tradingagents.dataflows.value_investment.turtle.calculations import compute_turtle_signals
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleRunContext,
)


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    source: str = "akshare"
    confidence: object = "verified"
    raw_bucket: str = "clean_present"
    currency: str = "CNY"
    unit: str = "yuan"
    evidence_page: int | None = 7
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False


@dataclass(frozen=True)
class FakeExtraction:
    company: str = "600519"
    market: str = "CN"
    period_end: str = "2025-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    staleness: object = FakeStaleness()
    fields: dict | None = None


@dataclass(frozen=True)
class FakeAdapterResult:
    available: bool
    company: str
    market: str
    period_end: str
    extraction: object | None
    warnings: list[str]
    errors: list[str]


def test_build_report_facts_preserves_reliable_and_display_only_boundaries():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("10000000000"), unit="万元"),
        "cash": FakeField("cash", Decimal("25000000000")),
        "audit_opinion": FakeField(
            "audit_opinion",
            "标准无保留意见",
            source="llm",
            confidence="llm_supplement",
            is_reliable=False,
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert facts.fields["net_profit"].value.value == 10_000_000_000.0
    assert facts.fields["net_profit"].value.currency == "CNY"
    assert facts.fields["net_profit"].value.unit == "ten_thousand"
    assert facts.fields["net_profit"].value.source_reference == "net_profit p.7"
    assert facts.fields["net_profit"].reliability == "reliable"
    assert facts.fields["audit_opinion"].reliability == "display_only"
    assert "display-only" in " ".join(facts.caveats)


def test_missing_extraction_returns_caveat_only_facts():
    facts = build_report_facts_from_extraction(
        extraction=None,
        allow_llm_models=(),
        adapter_caveats=["annual-report extraction missing"],
    )

    assert facts.fields == {}
    assert facts.caveats == ["annual-report extraction missing"]


def test_stale_extraction_fields_are_display_only_even_when_reliable():
    extraction = FakeExtraction(
        staleness=FakeStaleness(is_fresh=False, is_stale=True),
        fields={
            "net_profit": FakeField("net_profit", Decimal("10000000000"), unit="万元"),
        },
    )

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=["annual-report extraction stale"],
    )

    assert facts.fields["net_profit"].reliability == "display_only"
    assert facts.fields["net_profit"].value.reliability == "display_only"
    assert "stale extraction is display-only by policy" in facts.caveats


def test_public_catalog_field_names_are_normalized_for_turtle_calculations():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("10000000000"), unit="亿元"),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("12000000000"), unit="亿元"),
        "capital_expenditures": FakeField("capital_expenditures", Decimal("2000000000"), unit="亿元"),
        "cash_and_equivalents": FakeField("cash_and_equivalents", Decimal("50000000000"), unit="亿元"),
        "interest_bearing_debt": FakeField("interest_bearing_debt", Decimal("5000000000"), unit="亿元"),
    })

    report = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "capex" in report.fields
    assert "cash" in report.fields
    assert "capital_expenditures" not in report.fields
    assert "cash_and_equivalents" not in report.fields

    facts = TurtleFacts(
        context=TurtleRunContext.for_ticker(
            ticker="600519",
            market="A",
            trade_date="2026-05-19",
            company_name="贵州茅台",
        ),
        report=report,
        market=TurtleMarketFacts(fields={
            "market_cap": TurtleFactValue(
                name="market_cap",
                value=MoneyAmount(200000000000, "CNY", "yuan", "fixture", "market.market_cap"),
                source_label="fixture",
                source_reference="market.market_cap",
            ),
            "dividend_avg_payout_ratio_3y": TurtleFactValue(
                name="dividend_avg_payout_ratio_3y",
                value=0.5,
                source_label="fixture",
                source_reference="market.dividend",
            ),
            "tax_rate": TurtleFactValue(
                name="tax_rate",
                value=0.0,
                source_label="fixture",
                source_reference="market.tax",
            ),
            "buyback_amount": TurtleFactValue(
                name="buyback_amount",
                value=MoneyAmount(0, "CNY", "yuan", "fixture", "market.buyback"),
                source_label="fixture",
                source_reference="market.buyback",
            ),
        }),
        status="complete",
    )

    signals = compute_turtle_signals(facts)

    assert signals.results["owner_earnings"].missing_inputs == []
    assert signals.results["net_cash_ratio"].missing_inputs == []


def test_unsupported_unit_and_missing_currency_become_display_only():
    extraction = FakeExtraction(fields={
        "revenue": FakeField("revenue", Decimal("123"), unit="shares"),
        "cash": FakeField("cash", Decimal("456"), currency=""),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert facts.fields["revenue"].value == Decimal("123")
    assert facts.fields["revenue"].reliability == "display_only"
    assert facts.fields["cash"].value == Decimal("456")
    assert facts.fields["cash"].reliability == "display_only"
    caveats = " ".join(facts.caveats)
    assert "unsupported unit" in caveats
    assert "missing currency" in caveats


def test_non_finite_numeric_value_becomes_display_only():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", float("inf")),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert facts.fields["net_profit"].value == float("inf")
    assert facts.fields["net_profit"].reliability == "display_only"
    assert "non-finite numeric value" in " ".join(facts.caveats)


def test_bool_value_does_not_become_money_amount():
    extraction = FakeExtraction(fields={
        "has_clean_audit": FakeField("has_clean_audit", True),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert facts.fields["has_clean_audit"].value is True
    assert facts.fields["has_clean_audit"].reliability == "display_only"
    assert "boolean value" in " ".join(facts.caveats)


def test_policy_caveat_is_not_duplicated_when_numeric_degrades():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField(
            "net_profit",
            float("inf"),
            source="llm",
            confidence="llm_supplement",
            is_reliable=False,
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    policy_caveat = "LLM supplement from unknown is display-only by policy"
    numeric_caveat = "net_profit is display-only: non-finite numeric value"
    assert facts.fields["net_profit"].caveat == (
        f"{policy_caveat}; {numeric_caveat}"
    )
    assert facts.caveats == [policy_caveat, numeric_caveat]


def test_non_dict_extraction_fields_returns_empty_fields():
    facts = build_report_facts_from_extraction(
        extraction=FakeExtraction(fields=["not", "a", "dict"]),
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert facts.fields == {}


def test_get_turtle_report_facts_passes_turtle_period_end_to_adapter(monkeypatch):
    captured = {}

    class FakeAdapter:
        def get_annual_report_data(self, **kwargs):
            captured.update(kwargs)
            return FakeAdapterResult(
                available=True,
                company=kwargs["ticker"],
                market=kwargs["market"],
                period_end=kwargs["period_end"],
                extraction=FakeExtraction(period_end=kwargs["period_end"], fields={}),
                warnings=["annual-report extraction stale"],
                errors=["adapter timeout"],
            )

    facts = get_turtle_report_facts(
        ticker="600519",
        market="A",
        trade_date="2026-04-01",
        adapter=FakeAdapter(),
        allow_llm_models=(),
    )

    assert captured["period_end"] == "2025-12-31"
    assert captured["market"] == "CN"
    assert facts.metadata["period_end"] == "2025-12-31"
    assert "annual-report extraction stale" in facts.caveats
    assert "adapter timeout" in facts.caveats


def test_report_adapter_derives_caveated_single_year_payout_proxy():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    payout = facts.fields["dividend_avg_payout_ratio_3y"]
    assert payout.value == 0.35
    assert payout.source_reference == "dividends_paid p.7; net_profit p.7"
    assert payout.caveat == "single-year report payout proxy; not a 3-year average"
    assert "single-year report payout proxy; not a 3-year average" in facts.caveats


def test_report_adapter_does_not_derive_payout_from_unreliable_dividend_field():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField(
            "dividends_paid",
            Decimal("-35"),
            currency="HKD",
            unit="million",
            is_reliable=False,
            source="llm",
            confidence="llm_supplement",
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "dividend_avg_payout_ratio_3y" not in facts.fields


def test_report_adapter_does_not_derive_payout_from_mixed_currency_fields():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="CNY", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "dividend_avg_payout_ratio_3y" not in facts.fields
    assert "report payout proxy skipped: currency mismatch" in facts.caveats
