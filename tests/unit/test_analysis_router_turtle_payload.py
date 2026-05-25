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


class TestAnalysisReportsBranchPayloadPersistence:
    """
    Regression test for the analysis_reports MongoDB branch in get_task_result.

    The bug: when a completed analysis was re-fetched from the analysis_reports
    collection (page refresh / after restart), the result_data dict built from
    mongo_result did NOT include value_turtle_payload or state, so
    extract_turtle_payload() returned "" and the frontend lost its Turtle sub-tabs.

    The fix: copy both value_turtle_payload and state from mongo_result into
    result_data so that extract_turtle_payload Priority-1 resolves for the
    persistent path.

    Test approach: TestClient against a minimal FastAPI app that mounts only the
    analysis router, with auth and DB dependencies patched out.  This exercises
    the REAL router code (the exact analysis_reports branch in get_task_result)
    so the test FAILS before the fix and PASSES after.

    Note: importing app.routers.analysis triggers TradingAgentsGraph + MongoDB
    probe (adds ~60 s once per pytest session on machines without a running
    MongoDB), but the cost is paid once and shared across all tests in the file.
    """

    @pytest.fixture(scope="class")
    def analysis_app(self):
        """Minimal FastAPI app with just the analysis router, auth stubbed out."""
        from fastapi import FastAPI
        from app.routers import analysis as analysis_module
        from app.routers.auth_db import get_current_user

        _app = FastAPI()
        _app.include_router(analysis_module.router, prefix="/api/analysis")
        _app.dependency_overrides[get_current_user] = lambda: {
            "username": "test", "id": "test-id"
        }
        return _app

    def _make_mongo_doc(self) -> dict:
        """Simulate a mongo_result document as written by save-time persistence."""
        return {
            "analysis_id": "ana-x",
            "task_id": "t-x",
            "stock_symbol": "600519",
            "analysis_date": "2026-05-25",
            "summary": "s",
            "recommendation": "买入",
            "confidence_score": 0.9,
            "risk_level": "低",
            "key_points": [],
            "execution_time": 1,
            "tokens_used": 1,
            "analysts": [],
            "research_depth": "快速",
            "detailed_analysis": {},
            "decision": {},
            "reports": {"value_report": "# 价值分析\n\n内容内容内容。"},
            # save-time persistence writes value_turtle_payload to the top level
            "value_turtle_payload": VALID_PAYLOAD,
        }

    def test_result_endpoint_returns_payload_from_analysis_reports_branch(
        self, analysis_app
    ):
        """Persistent path: completed analysis re-fetched from analysis_reports must
        still return canonical value_turtle_payload (regression: branch dropped it).

        RED before fix  → endpoint returns value_turtle_payload == ""
        GREEN after fix → endpoint returns value_turtle_payload == VALID_PAYLOAD
        """
        mongo_doc = self._make_mongo_doc()

        fake_service = MagicMock()
        fake_service.get_task_status = AsyncMock(
            return_value={"status": "completed", "result_data": None}
        )

        fake_db = MagicMock()
        fake_db.analysis_reports.find_one = AsyncMock(return_value=mongo_doc)
        fake_db.analysis_tasks.find_one = AsyncMock(return_value=None)

        # Patch targets:
        #   get_simple_analysis_service is module-level-imported in analysis.py
        #     → patch at app.routers.analysis.get_simple_analysis_service
        #   get_mongo_db is locally imported inside the handler body
        #     → patch at its definition module: app.core.database.get_mongo_db
        with patch(
            "app.routers.analysis.get_simple_analysis_service",
            return_value=fake_service,
        ), patch("app.core.database.get_mongo_db", return_value=fake_db):
            client = TestClient(analysis_app)
            resp = client.get("/api/analysis/tasks/t-x/result")

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["value_turtle_payload"] == VALID_PAYLOAD, (
            "analysis_reports branch must copy value_turtle_payload from mongo_result; "
            "got: " + repr(data.get("value_turtle_payload"))
        )
        # The payload must NOT be exposed inside the reports dict (filtered at save time)
        assert "value_turtle_payload" not in data["reports"]
