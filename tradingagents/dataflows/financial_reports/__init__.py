"""Financial report client adapter package."""

from .adapter import (
    FinancialReportAdapter,
    FinancialReportAdapterResult,
    create_financial_report_adapter,
    infer_annual_period_end,
    resolve_injected_codex_token,
)
from .config import FinancialReportClientConfig, get_financial_report_client_config
from .formatter import format_annual_report_section, format_value_report_source_note
from .integration import apply_financial_report_client_data
from .mapper import FinancialReportMergeResult, merge_financial_report_data
from .policy import FieldUseDecision, FinancialReportPolicy

__all__ = [
    "FieldUseDecision",
    "FinancialReportAdapter",
    "FinancialReportAdapterResult",
    "FinancialReportClientConfig",
    "FinancialReportMergeResult",
    "FinancialReportPolicy",
    "apply_financial_report_client_data",
    "create_financial_report_adapter",
    "format_annual_report_section",
    "format_value_report_source_note",
    "get_financial_report_client_config",
    "infer_annual_period_end",
    "merge_financial_report_data",
    "resolve_injected_codex_token",
]
