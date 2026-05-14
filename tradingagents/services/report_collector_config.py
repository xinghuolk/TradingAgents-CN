"""Lightweight report-collector configuration helpers."""

import os
from typing import Any, Dict


def get_report_collector_config() -> Dict[str, Any]:
    """Read report-collector settings without importing agent/tool dependencies."""
    return {
        "url": os.getenv("REPORT_COLLECTOR_URL", "http://localhost"),
        "port": int(os.getenv("REPORT_COLLECTOR_PORT", "8001")),
        "enabled": os.getenv("REPORT_COLLECTOR_ENABLED", "false").lower() == "true",
        "analysis_enabled": os.getenv("REPORT_COLLECTOR_ANALYSIS_ENABLED", "false").lower() == "true",
        "timeout": int(os.getenv("REPORT_COLLECTOR_TIMEOUT", "60")),
        "max_reports": int(os.getenv("REPORT_COLLECTOR_MAX_REPORTS", "5")),
    }
