"""Tests for extract_turtle_payload helper (Spec 4 §4.2)."""
import json
from pathlib import Path

import pytest


VALID_PAYLOAD = json.dumps({"facts": {"status": "complete"}, "signals": {"status": "complete", "results": {}}})


class TestExtractTurtlePayload:
    """Priority order: result.value_turtle_payload > state.value_turtle_payload
    > reports.value_turtle_payload > disk reports/value_turtle_payload.json."""

    def _call(self, result_data, reports_dir=None):
        from app.services.turtle_payload_helper import extract_turtle_payload
        return extract_turtle_payload(result_data, reports_dir=reports_dir)

    # --- Priority 1: top-level value_turtle_payload ---

    def test_returns_top_level_payload(self):
        """result_data.value_turtle_payload → returned directly."""
        result_data = {"value_turtle_payload": VALID_PAYLOAD, "state": {}, "reports": {}}
        assert self._call(result_data) == VALID_PAYLOAD

    def test_ignores_empty_top_level(self):
        """Empty top-level → falls through to next source."""
        result_data = {
            "value_turtle_payload": "",
            "state": {"value_turtle_payload": VALID_PAYLOAD},
            "reports": {},
        }
        assert self._call(result_data) == VALID_PAYLOAD

    def test_ignores_whitespace_top_level(self):
        """Whitespace-only top-level → falls through."""
        result_data = {
            "value_turtle_payload": "   \n",
            "state": {"value_turtle_payload": VALID_PAYLOAD},
            "reports": {},
        }
        assert self._call(result_data) == VALID_PAYLOAD

    # --- Priority 2: state.value_turtle_payload ---

    def test_returns_state_payload(self):
        """result_data has no top-level payload → uses state."""
        result_data = {"state": {"value_turtle_payload": VALID_PAYLOAD}, "reports": {}}
        assert self._call(result_data) == VALID_PAYLOAD

    # --- Priority 3: reports.value_turtle_payload ---

    def test_returns_reports_payload(self):
        """No top-level or state → uses reports dict."""
        result_data = {"state": {}, "reports": {"value_turtle_payload": VALID_PAYLOAD}}
        assert self._call(result_data) == VALID_PAYLOAD

    # --- Priority 4: disk fallback ---

    def test_reads_disk_fallback(self, tmp_path):
        """No MongoDB payload → reads value_turtle_payload.json from disk."""
        (tmp_path / "value_turtle_payload.json").write_text(VALID_PAYLOAD, encoding="utf-8")
        result_data = {"state": {}, "reports": {}}
        assert self._call(result_data, reports_dir=tmp_path) == VALID_PAYLOAD

    def test_disk_fallback_missing_file_returns_empty(self, tmp_path):
        """Disk dir exists but file absent → returns ''."""
        result_data = {"state": {}, "reports": {}}
        assert self._call(result_data, reports_dir=tmp_path) == ""

    def test_no_sources_returns_empty(self):
        """No payload anywhere → returns ''."""
        assert self._call({}) == ""

    def test_none_result_data_returns_empty(self):
        """None result_data → returns ''."""
        from app.services.turtle_payload_helper import extract_turtle_payload
        assert extract_turtle_payload(None) == ""

    def test_whitespace_in_reports_is_ignored(self):
        """Whitespace-only in reports → falls through to disk (none here) → ''."""
        result_data = {"state": {}, "reports": {"value_turtle_payload": "  "}}
        assert self._call(result_data) == ""
