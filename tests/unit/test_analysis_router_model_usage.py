from unittest.mock import AsyncMock, MagicMock, patch

import pytest


MODEL_USAGE_SAMPLE = {
    "summary": {
        "total_calls": 1,
        "input_tokens": 11,
        "output_tokens": 5,
        "duration_seconds": 0.2,
        "costs_by_currency": {"CNY": 0.01},
    },
    "nodes": {
        "value_analyst": {
            "display_name": "价值投资分析",
            "provider": "codex",
            "model": "gpt-5.5",
            "providers": ["codex"],
            "models": ["gpt-5.5"],
            "calls": 1,
            "input_tokens": 11,
            "output_tokens": 5,
            "duration_seconds": 0.2,
            "cost": 0.01,
            "currency": "CNY",
            "costs_by_currency": {"CNY": 0.01},
            "partial": False,
        }
    },
}


def _make_mongo_doc() -> dict:
    return {
        "analysis_id": "ana-model-usage",
        "task_id": "task-model-usage",
        "stock_symbol": "00001",
        "analysis_date": "2026-06-02",
        "summary": "摘要",
        "recommendation": "持有",
        "confidence_score": 0.7,
        "risk_level": "中等",
        "key_points": [],
        "execution_time": 1,
        "tokens_used": 16,
        "analysts": ["value"],
        "research_depth": "快速",
        "detailed_analysis": {},
        "decision": {},
        "reports": {"value_report": "# 价值分析\n\n内容内容内容。"},
        "state": {},
        "model_info": "gpt-5.5",
        "model_usage": MODEL_USAGE_SAMPLE,
    }


@pytest.mark.asyncio
async def test_result_endpoint_returns_model_usage_from_analysis_reports_branch():
    from app.routers import analysis as analysis_module

    fake_service = MagicMock()
    fake_service.get_task_status = AsyncMock(
        return_value={"status": "completed", "result_data": None}
    )

    fake_db = MagicMock()
    fake_db.analysis_reports.find_one = AsyncMock(return_value=_make_mongo_doc())
    fake_db.analysis_tasks.find_one = AsyncMock(return_value=None)

    with patch(
        "app.routers.analysis.get_simple_analysis_service",
        return_value=fake_service,
    ), patch("app.core.database.get_mongo_db", return_value=fake_db):
        result = await analysis_module.get_task_result(
            "task-model-usage",
            user={"username": "test", "id": "test-id"},
        )

    assert result["success"] is True
    assert result["data"]["model_usage"] == MODEL_USAGE_SAMPLE


@pytest.mark.asyncio
async def test_result_endpoint_preserves_model_info_from_analysis_reports_branch():
    from app.routers import analysis as analysis_module

    fake_service = MagicMock()
    fake_service.get_task_status = AsyncMock(
        return_value={"status": "completed", "result_data": None}
    )

    fake_db = MagicMock()
    fake_db.analysis_reports.find_one = AsyncMock(return_value=_make_mongo_doc())
    fake_db.analysis_tasks.find_one = AsyncMock(return_value=None)

    with patch(
        "app.routers.analysis.get_simple_analysis_service",
        return_value=fake_service,
    ), patch("app.core.database.get_mongo_db", return_value=fake_db):
        result = await analysis_module.get_task_result(
            "task-model-usage",
            user={"username": "test", "id": "test-id"},
        )

    assert result["success"] is True
    assert result["data"]["model_info"] == "gpt-5.5"
