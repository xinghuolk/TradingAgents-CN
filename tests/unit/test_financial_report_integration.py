from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.financial_reports.integration import (
    apply_financial_report_client_data,
)


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    source: str | None = "akshare"
    confidence: object = "verified"
    raw_bucket: str | None = "clean_present"
    currency: str | None = "CNY"
    unit: str | None = "yuan"
    evidence_page: int | None = 1
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeExtraction:
    company: str = "600519"
    market: str = "CN"
    period_end: str = "2024-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    staleness: object | None = None
    fields: dict | None = None


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False
    value: str = "fresh"


@dataclass(frozen=True)
class FakeAdapterResult:
    available: bool
    company: str
    market: str
    period_end: str
    extraction: object | None
    warnings: list[str]
    errors: list[str]


def test_apply_financial_report_client_data_merges_reliable_fields(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "true")
    extraction = FakeExtraction(staleness=FakeStaleness(), fields={
        "net_profit": FakeField("net_profit", Decimal("100")),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("150")),
        "capital_expenditures": FakeField("capital_expenditures", Decimal("40")),
    })

    class FakeAdapter:
        def get_annual_report_data(self, **kwargs):
            return FakeAdapterResult(
                available=True,
                company=kwargs["ticker"],
                market=kwargs["market"],
                period_end=kwargs["period_end"],
                extraction=extraction,
                warnings=[],
                errors=[],
            )

    monkeypatch.setattr(
        "tradingagents.dataflows.financial_reports.integration.create_financial_report_adapter",
        lambda config: FakeAdapter(),
    )
    base = {"net_profits": [1.0], "operating_cash_flow": None, "free_cash_flow": None}

    merged = apply_financial_report_client_data(
        financial_data=base,
        ticker="600519",
        market="A",
        period_end="2024-12-31",
    )

    assert merged["net_profits"][0] == 100.0
    assert merged["operating_cash_flow"] == 150.0
    assert merged["free_cash_flow"] == 110.0
    assert merged["_data_source"]["net_profits"] == "financial-report-client"


def test_apply_financial_report_client_data_noops_when_disabled(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "false")
    base = {"net_profits": [1.0]}

    merged = apply_financial_report_client_data(
        financial_data=base,
        ticker="600519",
        market="CN",
        period_end="2024-12-31",
    )

    assert merged == base


def test_stale_extraction_is_display_only_and_does_not_override(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "true")
    extraction = FakeExtraction(
        staleness=FakeStaleness(is_fresh=False, is_stale=True, value="stale"),
        fields={"net_profit": FakeField("net_profit", Decimal("100"))},
    )

    class FakeAdapter:
        def get_annual_report_data(self, **kwargs):
            return FakeAdapterResult(
                available=True,
                company=kwargs["ticker"],
                market=kwargs["market"],
                period_end=kwargs["period_end"],
                extraction=extraction,
                warnings=["annual-report extraction stale"],
                errors=[],
            )

    monkeypatch.setattr(
        "tradingagents.dataflows.financial_reports.integration.create_financial_report_adapter",
        lambda config: FakeAdapter(),
    )
    base = {"net_profits": [1.0], "_data_source": {"net_profits": "akshare"}}

    merged = apply_financial_report_client_data(
        financial_data=base,
        ticker="600519",
        market="CN",
        period_end="2024-12-31",
    )

    assert merged["net_profits"] == [1.0]
    assert merged["_data_source"]["net_profits"] == "akshare"
    assert "stale" in " ".join(merged["_financial_report_client"]["caveats"])


def test_missing_extraction_is_display_only_and_does_not_override(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "true")
    extraction = FakeExtraction(
        staleness=FakeStaleness(is_fresh=False, is_missing=True, value="missing"),
        fields={"net_profit": FakeField("net_profit", Decimal("999"))},
    )

    class FakeAdapter:
        def get_annual_report_data(self, **kwargs):
            return FakeAdapterResult(
                available=False,
                company=kwargs["ticker"],
                market=kwargs["market"],
                period_end=kwargs["period_end"],
                extraction=extraction,
                warnings=["annual-report extraction missing"],
                errors=[],
            )

    monkeypatch.setattr(
        "tradingagents.dataflows.financial_reports.integration.create_financial_report_adapter",
        lambda config: FakeAdapter(),
    )
    base = {"net_profits": [1.0], "_data_source": {"net_profits": "akshare"}}

    merged = apply_financial_report_client_data(
        financial_data=base,
        ticker="600519",
        market="CN",
        period_end="2024-12-31",
    )

    assert merged["net_profits"] == [1.0]
    assert merged["_data_source"]["net_profits"] == "akshare"
    assert "missing" in " ".join(merged["_financial_report_client"]["caveats"])
