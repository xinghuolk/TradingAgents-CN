from pathlib import Path


def test_value_tool_imports_lightweight_financial_report_helper():
    source = Path("tradingagents/tools/value_investment_tool.py").read_text(encoding="utf-8")

    assert "apply_financial_report_client_data" in source
    assert "format_value_report_source_note" in source
    assert "financial_data = apply_financial_report_client_data(" in source
    assert "financial_report_note = format_value_report_source_note" in source


def test_financial_report_source_note_uses_unnumbered_heading():
    source = Path("tradingagents/dataflows/financial_reports/formatter.py").read_text(encoding="utf-8")

    assert "▶ 年报数据来源说明（FinancialReportClient）" in source
    assert "▶ 七、年报数据来源说明" not in source
