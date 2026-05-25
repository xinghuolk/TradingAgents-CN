"""Tests for save-time canonical value_turtle_payload persistence (Spec 4 §4.1)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


VALID_PAYLOAD = json.dumps({
    "facts": {"status": "complete", "report": {"fields": {}}, "market": {"fields": {}}},
    "signals": {"status": "complete", "results": {}},
})


def _make_state(payload: str = VALID_PAYLOAD):
    return {
        "value_report": "# 价值分析\n\n内容。",
        "value_turtle_payload": payload,
        "market_report": "# 市场分析\n\n内容。",
    }


class TestSaveTimeCanonicalPersistence:
    """
    The 'document' inserted into analysis_reports must have a top-level
    'value_turtle_payload' key, and the $set dict for analysis_tasks must also
    include it.
    """

    def _extract_insert_call_doc(self, mock_db):
        """Return the document dict passed to insert_one."""
        return mock_db.analysis_reports.insert_one.call_args[0][0]

    def _extract_task_set_dict(self, mock_db):
        """Return the dict passed as the $set value to update_one."""
        return mock_db.analysis_tasks.update_one.call_args[0][1]["$set"]["result"]

    @pytest.mark.asyncio
    async def test_analysis_reports_doc_has_top_level_payload(self):
        """analysis_reports insert_one doc must have value_turtle_payload at top level."""
        from app.services.simple_analysis_service import SimpleAnalysisService

        state = _make_state(VALID_PAYLOAD)
        result = {
            "state": state,
            "analysis_date": "2026-05-25",
            "stock_symbol": "600519",
            "summary": "摘要",
            "recommendation": "买入",
            "confidence_score": 0.9,
            "risk_level": "低",
            "key_points": [],
            "execution_time": 10,
            "tokens_used": 1000,
            "analysts": ["value"],
            "research_depth": 1,
            "model_info": "test-model",
            "decision": {},
            "performance_metrics": {},
        }

        mock_db = MagicMock()
        insert_result = MagicMock()
        insert_result.inserted_id = "fake_id"
        mock_db.analysis_reports.insert_one = AsyncMock(return_value=insert_result)
        mock_db.analysis_tasks.update_one = AsyncMock()

        svc = SimpleAnalysisService.__new__(SimpleAnalysisService)

        with patch("app.services.simple_analysis_service.get_mongo_db", return_value=mock_db):
            # Actual method signature: _save_analysis_result_web_style(self, task_id, result)
            await svc._save_analysis_result_web_style(
                task_id="task-001",
                result=result,
            )

        doc = self._extract_insert_call_doc(mock_db)
        assert "value_turtle_payload" in doc, "top-level value_turtle_payload must be in analysis_reports doc"
        assert doc["value_turtle_payload"] == VALID_PAYLOAD

    @pytest.mark.asyncio
    async def test_analysis_tasks_result_has_payload(self):
        """analysis_tasks $set result must include value_turtle_payload."""
        from app.services.simple_analysis_service import SimpleAnalysisService

        state = _make_state(VALID_PAYLOAD)
        result = {
            "state": state,
            "analysis_date": "2026-05-25",
            "stock_symbol": "600519",
            "summary": "摘要",
            "recommendation": "买入",
            "confidence_score": 0.9,
            "risk_level": "低",
            "key_points": [],
            "execution_time": 10,
            "tokens_used": 1000,
            "analysts": ["value"],
            "research_depth": 1,
            "model_info": "test-model",
            "decision": {},
            "performance_metrics": {},
        }

        mock_db = MagicMock()
        insert_result = MagicMock()
        insert_result.inserted_id = "fake_id"
        mock_db.analysis_reports.insert_one = AsyncMock(return_value=insert_result)
        mock_db.analysis_tasks.update_one = AsyncMock()

        svc = SimpleAnalysisService.__new__(SimpleAnalysisService)

        with patch("app.services.simple_analysis_service.get_mongo_db", return_value=mock_db):
            await svc._save_analysis_result_web_style(
                task_id="task-001",
                result=result,
            )

        task_set = self._extract_task_set_dict(mock_db)
        assert "value_turtle_payload" in task_set, "value_turtle_payload must be in analysis_tasks.result $set"
        assert task_set["value_turtle_payload"] == VALID_PAYLOAD

    @pytest.mark.asyncio
    async def test_empty_payload_writes_empty_string(self):
        """Empty payload state → value_turtle_payload='' in both doc and task_set."""
        from app.services.simple_analysis_service import SimpleAnalysisService

        state = _make_state("")
        result = {
            "state": state,
            "analysis_date": "2026-05-25",
            "stock_symbol": "600519",
            "summary": "摘要",
            "recommendation": "买入",
            "confidence_score": 0.9,
            "risk_level": "低",
            "key_points": [],
            "execution_time": 10,
            "tokens_used": 1000,
            "analysts": ["value"],
            "research_depth": 1,
            "model_info": "test-model",
            "decision": {},
            "performance_metrics": {},
        }

        mock_db = MagicMock()
        insert_result = MagicMock()
        insert_result.inserted_id = "fake_id"
        mock_db.analysis_reports.insert_one = AsyncMock(return_value=insert_result)
        mock_db.analysis_tasks.update_one = AsyncMock()

        svc = SimpleAnalysisService.__new__(SimpleAnalysisService)

        with patch("app.services.simple_analysis_service.get_mongo_db", return_value=mock_db):
            await svc._save_analysis_result_web_style(
                task_id="task-002",
                result=result,
            )

        doc = self._extract_insert_call_doc(mock_db)
        assert doc.get("value_turtle_payload", "NOT_PRESENT") == ""

        task_set = self._extract_task_set_dict(mock_db)
        assert task_set.get("value_turtle_payload", "NOT_PRESENT") == ""
