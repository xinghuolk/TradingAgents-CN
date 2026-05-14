"""Financial report client adapter package."""

from .config import FinancialReportClientConfig, get_financial_report_client_config
from .formatter import format_annual_report_section, format_value_report_source_note
from .mapper import FinancialReportMergeResult, merge_financial_report_data
from .policy import FieldUseDecision, FinancialReportPolicy

__all__ = [
    "FieldUseDecision",
    "FinancialReportClientConfig",
    "FinancialReportMergeResult",
    "FinancialReportPolicy",
    "format_annual_report_section",
    "format_value_report_source_note",
    "get_financial_report_client_config",
    "merge_financial_report_data",
]
