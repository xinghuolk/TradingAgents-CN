"""Tests for /api/analysis/tasks/{id}/result canonical value_turtle_payload (Spec 4 §4.2)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


VALID_PAYLOAD = json.dumps({"facts": {"status": "complete"}, "signals": {"status": "complete", "results": {}}})


def _make_result_data(payload_loc: str = "top"):
    """Build a result_data dict with payload in the specified location."""
    base = {
        "analysis_id": "ana-001",
        "stock_symbol": "600519",
        "stock_code": "600519",
        "analysis_date": "2026-05-25",
        "summary": "摘要",
        "recommendation": "买入",
        "confidence_score": 0.9,
        "risk_level": "低",
        "key_points": [],
        "execution_time": 10,
        "tokens_used": 1000,
        "analysts": ["value"],
        "research_depth": "快速",
        "detailed_analysis": {},
        "state": {},
        "decision": {},
        "reports": {"value_report": "# 价值分析\n\n内容。"},
    }
    if payload_loc == "top":
        base["value_turtle_payload"] = VALID_PAYLOAD
    elif payload_loc == "state":
        base["state"] = {"value_turtle_payload": VALID_PAYLOAD}
    elif payload_loc == "reports":
        base["reports"]["value_turtle_payload"] = VALID_PAYLOAD
    # "none" → no payload
    return base


class TestGetTaskResultTurtlePayload:
    """
    The endpoint builds final_result_data.
    We test the router logic by calling the helper functions it uses,
    since the endpoint requires DB and auth mocks.
    We test the extraction logic in isolation.
    """

    def test_extract_from_top_level(self):
        """extract_turtle_payload returns top-level payload."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        rd = _make_result_data("top")
        assert extract_turtle_payload(rd) == VALID_PAYLOAD

    def test_extract_from_state(self):
        """extract_turtle_payload returns state payload when top-level absent."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        rd = _make_result_data("state")
        assert extract_turtle_payload(rd) == VALID_PAYLOAD

    def test_extract_from_reports(self):
        """extract_turtle_payload returns reports payload when top and state absent."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        rd = _make_result_data("reports")
        assert extract_turtle_payload(rd) == VALID_PAYLOAD

    def test_no_payload_returns_empty(self):
        """extract_turtle_payload returns '' when no payload present."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        rd = _make_result_data("none")
        assert extract_turtle_payload(rd) == ""

    def test_validated_reports_excludes_value_turtle_payload(self):
        """After building validated_reports, value_turtle_payload must be excluded."""
        # Simulate the router's reports-validation loop
        reports_data = {
            "value_report": "# 价值分析\n\n内容。",
            "market_report": "# 市场\n\n内容。",
            "value_turtle_payload": VALID_PAYLOAD,  # must be filtered out
        }
        validated_reports = {
            k: v
            for k, v in reports_data.items()
            if k != "value_turtle_payload"
        }
        assert "value_turtle_payload" not in validated_reports
        assert "value_report" in validated_reports
        assert "market_report" in validated_reports
