from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.financial_reports.formatter import (
    format_annual_report_section,
    format_value_report_source_note,
)


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    currency: str | None = "CNY"
    unit: str | None = "yuan"
    confidence: object = "verified"
    source: str | None = "akshare"
    raw_bucket: str | None = "clean_present"
    evidence_page: int | None = 8
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False
    value: str = "fresh"


@dataclass(frozen=True)
class FakeExtraction:
    company: str = "600519"
    market: str = "CN"
    period_end: str = "2024-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    staleness: FakeStaleness = FakeStaleness()
    fields: dict[str, FakeField] | None = None


def test_format_annual_report_section_includes_reliable_fields():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100")),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("130")),
    })

    text = format_annual_report_section(
        extraction=extraction,
        caveats=[],
        max_fields=5,
    )

    assert "## 年报权威数据（FinancialReportClient）" in text
    assert "600519 CN 2024-12-31" in text
    assert "catalog_version: 2026-05-02" in text
    assert "net_profit: 100 CNY yuan" in text
    assert "operating_cash_flow: 130 CNY yuan" in text
    assert "page 8" in text


def test_format_annual_report_section_marks_stale_and_llm_caveats():
    extraction = FakeExtraction(
        llm_provider="openai",
        llm_model="codex-subscription",
        staleness=FakeStaleness(is_fresh=False, is_stale=True, value="stale"),
        fields={
            "capital_expenditures": FakeField(
                "capital_expenditures",
                Decimal("30"),
                source="llm",
                raw_bucket="llm_supplement_present",
                is_reliable=False,
            )
        },
    )

    text = format_annual_report_section(
        extraction=extraction,
        caveats=["LLM supplement from codex-subscription allowed by policy"],
        max_fields=5,
    )

    assert "staleness: stale" in text
    assert "stale extraction; display-only unless explicitly allowed" in text
    assert "LLM supplement from codex-subscription allowed by policy" in text


def test_format_value_report_source_note_uses_financial_data_metadata():
    financial_data = {
        "_data_source": {
            "net_profits": "financial-report-client",
            "free_cash_flow": "financial-report-client:derived",
            "roe_avg_3y": "akshare",
        },
        "_financial_report_client": {
            "company": "600519",
            "market": "CN",
            "period_end": "2024-12-31",
            "catalog_version": "2026-05-02",
            "caveats": ["capital_expenditures unavailable: source_unavailable"],
        },
    }

    text = format_value_report_source_note(financial_data)

    assert "▶ 七、年报数据来源说明" in text
    assert "net_profits, free_cash_flow" in text
    assert "600519 CN 2024-12-31" in text
    assert "capital_expenditures unavailable: source_unavailable" in text
