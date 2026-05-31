"""OAuth providers must report as configured (not 未配置) across the status
surfaces, since they authenticate via OAuth tokens, not api_key."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.config_service import ConfigService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service():
    """Return a bare ConfigService instance without calling __init__."""
    return ConfigService.__new__(ConfigService)


def _fake_get_db(provider_doc: dict):
    """Return an async callable that yields a fake DB with the given provider doc."""

    async def _get_db():
        class _Coll:
            async def find_one(self, query):
                # Return the oauth doc regardless of query (ObjectId or string match)
                return provider_doc

        class _DB:
            llm_providers = _Coll()

        return _DB()

    return _get_db


# ---------------------------------------------------------------------------
# Edit 3: test_provider_api returns a friendly success for oauth providers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_test_provider_api_oauth_returns_friendly_success(monkeypatch):
    """test_provider_api must short-circuit for auth_kind=='oauth' and return
    success=True with a message mentioning 'OAuth' or '订阅'."""
    service = _make_service()

    oauth_doc = {
        "name": "codex",
        "display_name": "OpenAI Codex (订阅)",
        "auth_kind": "oauth",
        "api_key": "",
    }
    monkeypatch.setattr(service, "_get_db", _fake_get_db(oauth_doc))

    # Use a valid 24-hex ObjectId string so the ObjectId() constructor doesn't
    # raise; the fake find_one ignores the actual query value anyway.
    result = await service.test_provider_api("000000000000000000000001")

    assert result["success"] is True
    msg = result.get("message", "")
    assert "OAuth" in msg or "订阅" in msg, (
        f"Expected 'OAuth' or '订阅' in message, got: {msg!r}"
    )


@pytest.mark.asyncio
async def test_test_provider_api_non_oauth_provider_checks_api_key(monkeypatch):
    """Non-oauth providers without an api_key must still fail with the
    standard 'no valid api key' message (regression guard)."""
    service = _make_service()

    non_oauth_doc = {
        "name": "openai",
        "display_name": "OpenAI",
        "auth_kind": "api_key",
        "api_key": "",  # empty → invalid
    }
    monkeypatch.setattr(service, "_get_db", _fake_get_db(non_oauth_doc))

    # _get_env_api_key must also return nothing so we hit the failure branch
    monkeypatch.setattr(service, "_get_env_api_key", lambda name: None)

    result = await service.test_provider_api("000000000000000000000002")

    assert result["success"] is False
    # Should mention lack of valid API key
    assert "API" in result.get("message", "") or "密钥" in result.get("message", "")


# ---------------------------------------------------------------------------
# Edit 1: get_llm_providers sets source="oauth" for oauth providers;
# has_api_key is left False at service layer — route layer overrides it
# based on per-user binding via oauth_service.list_bound_providers.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_llm_providers_oauth_sets_source_and_defers_has_api_key(monkeypatch):
    """get_llm_providers must return the oauth provider with source='oauth'
    and has_api_key=False (deferred to route layer which checks binding).
    The service layer must NOT touch api_key validation."""
    service = _make_service()

    from bson import ObjectId
    oauth_doc = {
        "_id": ObjectId("000000000000000000000001"),
        "name": "claude_code",
        "display_name": "Claude Code (订阅)",
        "auth_kind": "oauth",
        "api_key": "",          # deliberately empty
        "is_active": True,
        "extra_config": {},
    }

    async def fake_get_db():
        class _Cursor:
            """Motor cursor: find() is sync, to_list() is async."""
            async def to_list(self, length=None):
                return [oauth_doc]

        class _Coll:
            def find(self):
                return _Cursor()

        class _DB:
            llm_providers = _Coll()

        return _DB()

    monkeypatch.setattr(service, "_get_db", fake_get_db)

    providers = await service.get_llm_providers()
    assert len(providers) == 1
    p = providers[0]
    # Service layer sets has_api_key=False (route layer overrides based on binding)
    assert p.extra_config.get("has_api_key") is False
    assert p.extra_config.get("source") == "oauth"
