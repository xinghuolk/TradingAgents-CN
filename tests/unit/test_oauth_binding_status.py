"""Tests: OAuth provider status reflects per-user binding; test_llm_config
skips API-key checks for OAuth subscription providers."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.config_service import ConfigService
from app.models.config import LLMConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service():
    """Return a bare ConfigService instance without calling __init__."""
    return ConfigService.__new__(ConfigService)


# ---------------------------------------------------------------------------
# Edit 2: test_llm_config returns friendly success for OAuth providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_llm_config_oauth_model_skips_key_check():
    """test_llm_config must short-circuit for OAuth subscription providers
    and return success=True with a message mentioning 'OAuth' or '订阅'.
    The early-return must NOT reach the DB or api_key validity checks."""
    service = _make_service()

    cfg = LLMConfig(provider="codex", model_name="gpt-5.5-codex", enabled=True)
    result = await service.test_llm_config(cfg)

    assert result["success"] is True
    msg = result.get("message", "")
    assert "OAuth" in msg or "订阅" in msg, (
        f"Expected 'OAuth' or '订阅' in message, got: {msg!r}"
    )


@pytest.mark.asyncio
async def test_test_llm_config_claude_code_skips_key_check():
    """claude_code provider must also skip the key check."""
    service = _make_service()

    cfg = LLMConfig(provider="claude_code", model_name="claude-opus-4-5", enabled=True)
    result = await service.test_llm_config(cfg)

    assert result["success"] is True
    msg = result.get("message", "")
    assert "OAuth" in msg or "订阅" in msg, (
        f"Expected 'OAuth' or '订阅' in message, got: {msg!r}"
    )


@pytest.mark.asyncio
async def test_test_llm_config_non_oauth_provider_does_not_skip(monkeypatch):
    """Non-OAuth providers (e.g. openai) must NOT hit the early-return and
    instead proceed to the api_key check, returning failure when no key."""
    service = _make_service()

    # Provide a fake DB that returns a provider doc without api_key
    async def _fake_get_db():
        class _Coll:
            async def find_one(self, query):
                return {"name": "openai", "default_base_url": "https://api.openai.com/v1"}

        class _DB:
            llm_providers = _Coll()

        return _DB()

    monkeypatch.setattr(service, "_get_db", _fake_get_db)
    # Also ensure no env key leaks through
    monkeypatch.setattr(service, "_get_env_api_key", lambda provider: None)

    cfg = LLMConfig(
        provider="openai",
        model_name="gpt-4",
        api_base="https://api.openai.com/v1",
        enabled=True,
    )
    result = await service.test_llm_config(cfg)

    # Non-oauth path — should fail due to missing api_key
    assert result["success"] is False


# ---------------------------------------------------------------------------
# Edit 1: list_bound_providers returns the set of bound provider names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_bound_providers_returns_bound_set():
    """list_bound_providers must return a set of provider names for which
    the user has OAuth credentials stored."""
    from app.services.oauth_service import list_bound_providers

    class _FakeCursor:
        """Async iterator that yields the given docs."""

        def __init__(self, docs):
            self._docs = iter(docs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._docs)
            except StopIteration:
                raise StopAsyncIteration

    class _FakeCollection:
        def find(self, query, projection=None):
            # Simulate two bound providers for user "u1"
            return _FakeCursor([
                {"provider": "codex"},
                {"provider": "claude_code"},
            ])

    result = await list_bound_providers(_FakeCollection(), "u1")
    assert result == {"codex", "claude_code"}


@pytest.mark.asyncio
async def test_list_bound_providers_empty_when_none_bound():
    """list_bound_providers returns empty set when user has no bindings."""
    from app.services.oauth_service import list_bound_providers

    class _FakeCursor:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _FakeCollection:
        def find(self, query, projection=None):
            return _FakeCursor()

    result = await list_bound_providers(_FakeCollection(), "u_nobody")
    assert result == set()
