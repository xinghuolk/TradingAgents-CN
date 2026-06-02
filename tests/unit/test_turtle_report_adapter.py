from dataclasses import dataclass
from decimal import Decimal

import pytest

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


def test_report_adapter_normalizes_negative_repurchase_of_stock_to_positive_buyback_amount():
    extraction = FakeExtraction(fields={
        "repurchase_of_stock": FakeField("repurchase_of_stock", Decimal("-1000000000")),
        "capital_expenditures": FakeField("capital_expenditures", Decimal("-2000000000")),
    })

    report = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert report.fields["buyback_amount"].value.value == 1_000_000_000.0
    assert report.fields["capex"].value.value == -2_000_000_000.0


def test_report_adapter_treats_raw_money_as_absolute_amount_with_field_currency():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField(
            "net_profit",
            Decimal("11841000000"),
            currency="HKD",
            unit="raw",
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    field = facts.fields["net_profit"]
    assert isinstance(field.value, MoneyAmount)
    assert field.reliability == "reliable"
    assert field.value.value == 11_841_000_000.0
    assert field.value.currency == "HKD"
    assert field.value.unit == "yuan"
    assert "unsupported unit raw" not in " ".join(facts.caveats)


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

    payout = facts.fields["dividend_payout_ratio_proxy_single_year"]
    assert payout.value == 0.35
    assert payout.reliability == "display_only"
    assert payout.source_reference == "dividends_paid p.7; net_profit p.7"
    assert payout.caveat == "single-year report payout proxy; not a 3-year average"
    assert "single-year report payout proxy; not a 3-year average" in facts.caveats


def test_report_adapter_derives_reliable_current_year_payout_ratio_and_display_proxy():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    current = facts.fields["dividend_payout_ratio_current_year"]
    assert current.value == 0.35
    assert current.reliability == "reliable"
    assert current.source_reference == "dividends_paid p.7; net_profit p.7"
    assert "derived from report dividends_paid/net_profit" in (current.caveat or "")

    proxy = facts.fields["dividend_payout_ratio_proxy_single_year"]
    assert proxy.value == 0.35
    assert proxy.reliability == "display_only"
    assert proxy.caveat == "single-year report payout proxy; not a 3-year average"


def test_report_adapter_derives_current_year_payout_with_currency_aliases():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HK$", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert facts.fields["dividend_payout_ratio_current_year"].value == 0.35
    assert "report payout ratio skipped: currency mismatch" not in facts.caveats


def test_report_adapter_maps_repurchase_of_stock_to_buyback_amount():
    extraction = FakeExtraction(fields={
        "repurchase_of_stock": FakeField(
            "repurchase_of_stock",
            Decimal("123000000"),
            currency="HKD",
            unit="yuan",
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "buyback_amount" in facts.fields
    assert "repurchase_of_stock" not in facts.fields
    assert facts.fields["buyback_amount"].name == "buyback_amount"
    assert facts.fields["buyback_amount"].value.value == 123000000.0
    assert facts.fields["buyback_amount"].value.currency == "HKD"


def test_report_adapter_derives_interest_bearing_debt_from_reliable_debt_primitives():
    extraction = FakeExtraction(fields={
        "st_borr": FakeField("st_borr", Decimal("38416000000"), currency="HKD", unit="raw"),
        "lt_borr": FakeField("lt_borr", Decimal("229699000000"), currency="HKD", unit="raw"),
        "bond_payable": FakeField("bond_payable", Decimal("165366"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    debt = facts.fields["interest_bearing_debt"]
    assert isinstance(debt.value, MoneyAmount)
    assert debt.reliability == "reliable"
    assert debt.value.currency == "HKD"
    assert debt.value.unit == "yuan"
    assert debt.value.value == 38416000000 + 229699000000 + 165366000000
    assert debt.source_reference == "st_borr p.7; lt_borr p.7; bond_payable p.7"


def test_report_adapter_does_not_overwrite_direct_interest_bearing_debt():
    extraction = FakeExtraction(fields={
        "interest_bearing_debt": FakeField(
            "interest_bearing_debt",
            Decimal("5000000000"),
            currency="HKD",
            unit="raw",
        ),
        "st_borr": FakeField("st_borr", Decimal("38416000000"), currency="HKD", unit="raw"),
        "lt_borr": FakeField("lt_borr", Decimal("229699000000"), currency="HKD", unit="raw"),
        "bond_payable": FakeField("bond_payable", Decimal("165366"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    debt = facts.fields["interest_bearing_debt"]
    assert isinstance(debt.value, MoneyAmount)
    assert debt.value.value == 5_000_000_000.0
    assert debt.source_label == "financial-report-client"
    assert debt.source_reference == "interest_bearing_debt p.7"


def test_report_adapter_skips_interest_bearing_debt_derivation_for_mixed_currencies():
    extraction = FakeExtraction(fields={
        "st_borr": FakeField("st_borr", Decimal("38416000000"), currency="HKD", unit="raw"),
        "lt_borr": FakeField("lt_borr", Decimal("229699000000"), currency="CNY", unit="raw"),
        "bond_payable": FakeField("bond_payable", Decimal("165366"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "interest_bearing_debt" not in facts.fields
    assert "interest_bearing_debt derivation skipped: currency mismatch" in facts.caveats


def test_report_adapter_caveats_partial_interest_bearing_debt_primitives():
    extraction = FakeExtraction(fields={
        "st_borr": FakeField("st_borr", Decimal("38416000000"), currency="HKD", unit="raw"),
        "lt_borr": FakeField("lt_borr", Decimal("229699000000"), currency="HKD", unit="raw"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "interest_bearing_debt" not in facts.fields
    assert "interest_bearing_debt derivation skipped: missing bond_payable" in facts.caveats


def test_report_adapter_is_silent_when_no_interest_bearing_debt_primitives():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "interest_bearing_debt" not in facts.fields
    assert not any("interest_bearing_debt" in caveat for caveat in facts.caveats)


def test_report_adapter_caveats_non_reliable_interest_bearing_debt_primitive():
    extraction = FakeExtraction(fields={
        "st_borr": FakeField("st_borr", Decimal("38416000000"), currency="HKD", unit="raw"),
        "lt_borr": FakeField("lt_borr", Decimal("229699000000"), currency="HKD", unit="raw"),
        "bond_payable": FakeField(
            "bond_payable", Decimal("165366"), currency="HKD", unit="million",
            is_reliable=False, raw_bucket="estimate",
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "interest_bearing_debt" not in facts.fields
    assert "interest_bearing_debt derivation skipped: non-reliable bond_payable" in facts.caveats


def test_report_adapter_skips_current_year_payout_when_profit_non_positive():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("0"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "dividend_payout_ratio_current_year" not in facts.fields
    assert "dividend_payout_ratio_proxy_single_year" not in facts.fields
    assert "report payout ratio skipped: non-positive net_profit" in facts.caveats


def test_report_adapter_derives_current_year_payout_for_historical_periods():
    class FakeAdapter:
        def get_annual_report_data(self, **kwargs):
            year = int(kwargs["period_end"][:4])
            ratio_by_year = {
                2025: Decimal("-50"),
                2024: Decimal("-40"),
                2023: Decimal("-30"),
            }
            return FakeAdapterResult(
                available=True,
                company=kwargs["ticker"],
                market=kwargs["market"],
                period_end=kwargs["period_end"],
                extraction=FakeExtraction(
                    period_end=kwargs["period_end"],
                    fields={
                        "net_profit": FakeField(
                            "net_profit",
                            Decimal("100"),
                            currency="HKD",
                            unit="million",
                        ),
                        "dividends_paid": FakeField(
                            "dividends_paid",
                            ratio_by_year[year],
                            currency="HKD",
                            unit="million",
                        ),
                    },
                ),
                warnings=[],
                errors=[],
            )

    facts = get_turtle_report_facts(
        ticker="00700",
        market="HK",
        trade_date="2026-05-26",
        adapter=FakeAdapter(),
        allow_llm_models=(),
        history_periods=2,
    )

    assert facts.fields["dividend_payout_ratio_current_year"].value == 0.5
    assert facts.historical["2024-12-31"].fields["dividend_payout_ratio_current_year"].value == 0.4
    assert facts.historical["2023-12-31"].fields["dividend_payout_ratio_current_year"].value == 0.3


def test_report_adapter_adds_proxy_alongside_display_only_extraction_payout():
    extraction = FakeExtraction(fields={
        "dividend_avg_payout_ratio_3y": FakeField(
            "dividend_avg_payout_ratio_3y",
            Decimal("0.99"),
            source="llm",
            confidence="llm_supplement",
            is_reliable=False,
            unit="ratio",
        ),
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    # The extraction's display-only field stays under its original key.
    assert facts.fields["dividend_avg_payout_ratio_3y"].reliability == "display_only"
    # The calculated proxy is stored under the new key as display_only.
    proxy = facts.fields["dividend_payout_ratio_proxy_single_year"]
    assert proxy.value == 0.35
    assert proxy.reliability == "display_only"
    assert proxy.caveat == "single-year report payout proxy; not a 3-year average"


def test_report_adapter_adds_proxy_alongside_non_numeric_extraction_payout():
    extraction = FakeExtraction(fields={
        "dividend_avg_payout_ratio_3y": FakeField(
            "dividend_avg_payout_ratio_3y",
            "35%",
            currency="",
            unit="",
            is_reliable=True,
        ),
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    # The extraction's string "35%" field stays under its original key (display_only due to missing currency/unit).
    assert facts.fields["dividend_avg_payout_ratio_3y"].value == "35%"
    # The calculated proxy is stored under the new key as display_only.
    proxy = facts.fields["dividend_payout_ratio_proxy_single_year"]
    assert proxy.value == 0.35
    assert proxy.reliability == "display_only"
    assert proxy.caveat == "single-year report payout proxy; not a 3-year average"


def test_report_adapter_skips_non_finite_derived_payout_ratio():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("1e-308"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-1e308"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "dividend_payout_ratio_proxy_single_year" not in facts.fields
    assert "report payout ratio skipped: invalid payout ratio" in facts.caveats


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

    assert "dividend_payout_ratio_proxy_single_year" not in facts.fields


def test_report_adapter_does_not_derive_current_year_payout_from_unreliable_dividend_field():
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

    assert "dividend_payout_ratio_current_year" not in facts.fields


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

    assert "dividend_payout_ratio_proxy_single_year" not in facts.fields
    assert "report payout ratio skipped: currency mismatch" in facts.caveats


@pytest.mark.parametrize(
    ("currency", "unit", "expected_unit"),
    [
        ("HKD", "HKD", "yuan"),
        ("HKD", "HK$", "yuan"),
        ("HKD", "港元", "yuan"),
        ("HKD", "HK$ million", "million"),
        ("HKD", "HKD'000", "thousand"),
        ("HKD", "HK$'000", "thousand"),
        ("USD", "USD", "yuan"),
        ("USD", "US$", "yuan"),
        ("USD", "US$ million", "million"),
        ("USD", "US$'000", "thousand"),
        ("CNY", "RMB million", "million"),
        ("CNY", "hundred_million", "hundred_million"),
        ("CNY", "hundred million", "hundred_million"),
    ],
)
def test_report_adapter_accepts_common_currency_unit_aliases(currency, unit, expected_unit):
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("123"), currency=currency, unit=unit),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    field = facts.fields["net_profit"]
    assert isinstance(field.value, MoneyAmount)
    assert field.value.currency == currency
    assert field.value.unit == expected_unit
    assert field.reliability == "reliable"


def test_report_adapter_rejects_million_shares_as_money_unit():
    extraction = FakeExtraction(fields={
        "share_count": FakeField(
            "share_count",
            Decimal("123"),
            currency="HKD",
            unit="million shares",
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    field = facts.fields["share_count"]
    assert field.value == Decimal("123")
    assert not isinstance(field.value, MoneyAmount)
    assert field.reliability == "display_only"
    assert "unsupported unit million shares" in " ".join(facts.caveats)


class TestGetTurtleReportFactsPreservesStatusOnPeriodEndFallback:
    """Regression: get_turtle_report_facts 在 period_end 缺失而重建 TurtleReportFacts 时
    必须保留 status 字段；否则 non_decisionable / degraded 会被静默 upgrade 到 complete。"""

    def test_extraction_none_preserves_non_decisionable_status(self):
        class FakeAdapterMissingPeriodEnd:
            def get_annual_report_data(self, **kwargs):
                return FakeAdapterResult(
                    available=False,
                    company=kwargs["ticker"],
                    market=kwargs["market"],
                    period_end=kwargs["period_end"],
                    extraction=None,  # → build_report_facts 返回 status="non_decisionable"
                    warnings=[],
                    errors=[],
                )

        facts = get_turtle_report_facts(
            ticker="600519",
            market="A",
            trade_date="2026-04-01",
            adapter=FakeAdapterMissingPeriodEnd(),
            allow_llm_models=(),
        )

        # extraction=None → facts.metadata 的 period_end 是 None → 触发 metadata 重建路径
        # 重建后 status 必须仍是 non_decisionable，不能被默认值 complete 覆盖
        assert facts.status == "non_decisionable"
        assert facts.metadata["period_end"] == "2025-12-31"


class TestReportAdapterStatus:
    def test_none_extraction_is_non_decisionable(self):
        facts = build_report_facts_from_extraction(
            extraction=None, allow_llm_models=(), adapter_caveats=[],
        )
        assert facts.status == "non_decisionable"

    def test_empty_fields_is_non_decisionable(self):
        class FakeExtraction:
            fields = {}
            staleness = None
            company = market = period_end = catalog_version = None
        facts = build_report_facts_from_extraction(
            extraction=FakeExtraction(), allow_llm_models=(), adapter_caveats=[],
        )
        assert facts.status == "non_decisionable"

    def test_non_dict_fields_is_non_decisionable(self):
        class FakeExtraction:
            fields = "not a dict"
            staleness = None
            company = market = period_end = catalog_version = None
        facts = build_report_facts_from_extraction(
            extraction=FakeExtraction(), allow_llm_models=(), adapter_caveats=[],
        )
        assert facts.status == "non_decisionable"

    def test_adapter_caveats_make_status_degraded(self):
        class FakeField:
            field_id = "net_profit"
            unit = "yuan"
            currency = "CNY"
            evidence_page = 45
            value = 1_000_000_000

        class FakeExtraction:
            fields = {"net_profit": FakeField()}
            staleness = None
            company = market = period_end = catalog_version = None
        facts = build_report_facts_from_extraction(
            extraction=FakeExtraction(),
            allow_llm_models=(),
            adapter_caveats=["unrelated warning"],
        )
        assert facts.status == "degraded"


class TestPayoutProxyRenameAndDowngrade:
    def _build_with_dividends_and_profit(self):
        """构造含 dividends_paid 与 net_profit 的 fake extraction，触发 proxy 派生。"""
        class FakeField:
            def __init__(self, field_id, value, unit="yuan", currency="CNY"):
                self.field_id = field_id
                self.value = value
                self.unit = unit
                self.currency = currency
                self.evidence_page = 1
                self.is_reliable = True
                self.source = "akshare"
                self.confidence = "verified"
                self.raw_bucket = "clean_present"

        class FakeExtraction:
            staleness = None
            company = market = period_end = catalog_version = None
            fields = {
                "net_profit": FakeField("net_profit", 1_000_000_000),
                "dividends_paid": FakeField("dividends_paid", 300_000_000),
            }
        return build_report_facts_from_extraction(
            extraction=FakeExtraction(), allow_llm_models=(), adapter_caveats=[],
        )

    def test_proxy_uses_new_key_name(self):
        facts = self._build_with_dividends_and_profit()
        assert "dividend_payout_ratio_proxy_single_year" in facts.fields
        assert "dividend_avg_payout_ratio_3y" not in facts.fields

    def test_proxy_reliability_is_display_only(self):
        facts = self._build_with_dividends_and_profit()
        proxy = facts.fields["dividend_payout_ratio_proxy_single_year"]
        assert proxy.reliability == "display_only"

    def test_proxy_carries_single_year_caveat(self):
        facts = self._build_with_dividends_and_profit()
        proxy = facts.fields["dividend_payout_ratio_proxy_single_year"]
        assert "single-year" in (proxy.caveat or "")
