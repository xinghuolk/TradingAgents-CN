"""Tests for /api/reports/{id}/detail canonical value_turtle_payload (Spec 4 §4.2)."""
import json
from pathlib import Path

import pytest


VALID_PAYLOAD = json.dumps({"facts": {"status": "complete"}, "signals": {"status": "complete", "results": {}}})


class TestReportsDetailTurtlePayload:
    """
    These tests verify the extraction and filtering logic as pure functions,
    matching the patterns used in get_report_detail.
    """

    def test_extract_from_analysis_reports_top_level(self):
        """analysis_reports doc has value_turtle_payload at top level → returned."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        doc_as_result = {
            "value_turtle_payload": VALID_PAYLOAD,
            "reports": {"value_report": "# 价值分析"},
            "state": {},
        }
        result = extract_turtle_payload(doc_as_result)
        assert result == VALID_PAYLOAD

    def test_extract_from_analysis_tasks_fallback_state(self):
        """analysis_tasks.result has payload in state → returned."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        task_result = {
            "state": {"value_turtle_payload": VALID_PAYLOAD},
            "reports": {},
        }
        result = extract_turtle_payload(task_result)
        assert result == VALID_PAYLOAD

    def test_extract_from_analysis_tasks_fallback_reports(self):
        """analysis_tasks.result has payload in reports → returned."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        task_result = {
            "state": {},
            "reports": {"value_turtle_payload": VALID_PAYLOAD},
        }
        result = extract_turtle_payload(task_result)
        assert result == VALID_PAYLOAD

    def test_disk_fallback_for_historical_record(self, tmp_path):
        """No MongoDB payload → disk file → returned."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        (tmp_path / "value_turtle_payload.json").write_text(VALID_PAYLOAD, encoding="utf-8")
        result = extract_turtle_payload({"state": {}, "reports": {}}, reports_dir=tmp_path)
        assert result == VALID_PAYLOAD

    def test_reports_dict_filters_value_turtle_payload(self):
        """reports dict must not expose value_turtle_payload as a report tab."""
        reports = {
            "value_report": "# 价值分析\n\n内容。",
            "market_report": "# 市场",
            "value_turtle_payload": VALID_PAYLOAD,
        }
        filtered = {k: v for k, v in reports.items() if k != "value_turtle_payload"}
        assert "value_turtle_payload" not in filtered
        assert "value_report" in filtered

    def test_empty_payload_returns_empty_string(self):
        """No payload in any source → empty string."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        result = extract_turtle_payload({"state": {}, "reports": {}})
        assert result == ""

    def test_whitespace_payload_treated_as_empty(self):
        """Whitespace-only payload → falls through all sources → ''."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        result = extract_turtle_payload({
            "value_turtle_payload": "   ",
            "state": {},
            "reports": {},
        })
        assert result == ""
