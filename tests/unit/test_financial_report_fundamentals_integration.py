from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from tradingagents.dataflows.financial_reports.formatter import format_annual_report_section


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    currency: str | None = "HKD"
    unit: str | None = "million"
    confidence: object = "verified"
    source: str | None = "yahoo"
    raw_bucket: str | None = "clean_present"
    evidence_page: int | None = 10
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
    company: str = "00001"
    market: str = "HK"
    period_end: str = "2025-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    staleness: FakeStaleness = FakeStaleness()
    fields: dict | None = None


def test_fundamentals_formatter_section_is_ready_for_agent_output():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("32000")),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("41000")),
    })

    section = format_annual_report_section(extraction, caveats=[], max_fields=10)

    assert section.startswith("## 年报权威数据（FinancialReportClient）")
    assert "00001 HK 2025-12-31" in section
    assert "net_profit: 32000 HKD million" in section


def test_agent_utils_calls_financial_report_client_before_report_collector_analysis():
    source = Path("tradingagents/agents/utils/agent_utils.py").read_text(encoding="utf-8")

    assert "_try_financial_report_client_section" in source
    assert "FinancialReportPolicy" in source
    assert "format_annual_report_section" in source
    assert "create_financial_report_adapter" in source
    assert "get_financial_report_client_config" in source
    assert "financial_report_section = _try_financial_report_client_section()" in source
    assert "max_fields=12" in source
    assert "display_fields = {}" in source
    assert "if decision.can_display:" in source
    assert "SimpleNamespace(" in source
    assert "f\"- caveat: {_one_line(item)}\"" in source
    assert "ticker_text.endswith((\".SH\", \".SZ\"))" in source
    assert "report_collector_analysis_enabled" in source

    helper_pos = source.index("financial_report_section = _try_financial_report_client_section()")
    china_pos = source.index("if is_china:")
    hk_report_collector_pos = source.index("def _get_report_collector_client():")
    assert helper_pos < china_pos
    assert helper_pos < hk_report_collector_pos
