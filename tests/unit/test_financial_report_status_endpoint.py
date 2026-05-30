"""GET /api/config/financial-report/status returns the env-sourced extractor
config read-only. We call the route handler directly with env vars set."""

import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_financial_report_status_reads_env(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "true")
    monkeypatch.setenv("FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT", "true")
    monkeypatch.setenv("FINANCIAL_REPORT_PDF_ROOT", "/pdf")
    monkeypatch.setenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", "/cache")
    monkeypatch.setenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "codex,gpt-5.5")

    from app.routers.config import financial_report_status

    result = await financial_report_status(current_user=MagicMock())
    assert result["enabled"] is True
    assert result["include_llm_supplement"] is True
    assert result["pdf_root"] == "/pdf"
    assert result["extractor_cache_root"] == "/cache"
    assert result["allow_llm_models"] == ["codex", "gpt-5.5"]


@pytest.mark.asyncio
async def test_financial_report_status_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FINANCIAL_REPORT_CLIENT_ENABLED", raising=False)
    from app.routers.config import financial_report_status

    result = await financial_report_status(current_user=MagicMock())
    assert result["enabled"] is False
