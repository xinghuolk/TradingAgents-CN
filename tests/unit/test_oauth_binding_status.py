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
    """test_llm_config must route OAuth subscription providers to the dedicated
    OAuth path (NOT the generic api_key/DB check). With no user_id it cannot
    resolve a binding, so it returns success=False with a user-related message —
    but crucially it must NOT reach the DB or api_key validity branch."""
    service = _make_service()

    cfg = LLMConfig(provider="codex", model_name="gpt-5.5-codex", enabled=True)
    result = await service.test_llm_config(cfg)

    # No user_id → cannot determine current user; OAuth path returns this msg.
    assert result["success"] is False
    msg = result.get("message", "")
    assert "用户" in msg, (
        f"Expected OAuth-path '用户' message, got: {msg!r}"
    )
    # Must not have fallen through to the generic api_key error.
    assert "API密钥" not in msg


@pytest.mark.asyncio
async def test_test_llm_config_claude_code_skips_key_check():
    """claude_code provider must also route through the OAuth path."""
    service = _make_service()

    cfg = LLMConfig(provider="claude_code", model_name="claude-opus-4-5", enabled=True)
    result = await service.test_llm_config(cfg)

    assert result["success"] is False
    msg = result.get("message", "")
    assert "用户" in msg, (
        f"Expected OAuth-path '用户' message, got: {msg!r}"
    )
    assert "API密钥" not in msg


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


# ---------------------------------------------------------------------------
# validate_config: OAuth provider status vs. authentication state
# ---------------------------------------------------------------------------


def _run_validate_config(monkeypatch, *, current_user, bound):
    """Drive app.routers.system_config.validate_config with all heavy
    collaborators stubbed so it deterministically reaches the OAuth branch
    with a single active OAuth provider named 'claude-code'.

    `bound` is the set returned by list_bound_providers (only consulted on the
    authenticated path). Returns the mongodb_validation dict from the response.
    """
    import app.routers.system_config as scmod
    import app.core.config_bridge as bridge_mod
    import app.core.startup_validator as sv_mod
    import app.services.oauth_service as oauth_service_mod
    import app.routers.oauth as oauth_router_mod
    import pymongo

    monkeypatch.setattr(bridge_mod, "bridge_config_to_env", lambda *a, **k: None)

    # StartupValidator().validate() must expose .success and the *_configs lists.
    class _FakeValidationResult:
        success = True
        missing_required = []
        missing_recommended = []
        invalid_configs = []
        warnings = []

    class _FakeStartupValidator:
        def validate(self):
            return _FakeValidationResult()

    monkeypatch.setattr(sv_mod, "StartupValidator", _FakeStartupValidator)

    provider_doc = {
        "name": "claude-code",
        "display_name": "Claude Code",
        "auth_kind": "oauth",
        "is_active": True,
    }

    class _FakeColl:
        def find(self, *a, **k):
            return [provider_doc]

    class _FakeDB:
        """Supports both ``db.llm_providers`` and ``db[name]`` access."""
        def __getattr__(self, _name):
            return _FakeColl()

        def __getitem__(self, _name):
            return _FakeColl()

    class _FakeMongoClient:
        def __init__(self, *a, **k):
            pass

        def __getitem__(self, _name):
            return _FakeDB()

        def close(self):
            pass

    monkeypatch.setattr(pymongo, "MongoClient", _FakeMongoClient)

    monkeypatch.setattr(
        oauth_router_mod, "get_credentials_collection", lambda: object(),
        raising=False,
    )

    async def _list_bound(*a, **k):
        return set(bound)

    monkeypatch.setattr(oauth_service_mod, "list_bound_providers", _list_bound)

    # config_service.get_system_config() is awaited for data-source validation;
    # return None so that section is skipped harmlessly.
    from app.services import config_service as cs_mod

    async def _no_system_config():
        return None

    monkeypatch.setattr(cs_mod.config_service, "get_system_config", _no_system_config)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        scmod.validate_config(current_user=current_user)
    ) if False else None
    # validate_config is async; call it directly from the async test instead.
    return scmod


@pytest.mark.asyncio
async def test_validate_config_unauthenticated_oauth_is_neutral(monkeypatch):
    """Unauthenticated caller (current_user=None): OAuth provider must show a
    NEUTRAL subscription status and MUST NOT emit a 未绑定 warning."""
    scmod = _run_validate_config(monkeypatch, current_user=None, bound=set())

    result = await scmod.validate_config(current_user=None)
    mongo = result["data"]["mongodb_validation"]
    items = {it["name"]: it for it in mongo["llm_providers"]}
    assert "claude-code" in items, mongo
    item = items["claude-code"]
    assert item["status"] == "订阅类(OAuth)"
    assert item["has_api_key"] is True
    assert item["source"] == "oauth"
    assert not any("未完成 OAuth 绑定" in w for w in mongo["warnings"])


@pytest.mark.asyncio
async def test_validate_config_authenticated_unbound_oauth_warns(monkeypatch):
    """Authenticated caller whose OAuth provider is NOT bound: status must be
    '未绑定' and a 未绑定 warning MUST be emitted."""
    scmod = _run_validate_config(monkeypatch, current_user={"id": "u1"}, bound=set())

    result = await scmod.validate_config(current_user={"id": "u1"})
    mongo = result["data"]["mongodb_validation"]
    items = {it["name"]: it for it in mongo["llm_providers"]}
    item = items["claude-code"]
    assert item["status"] == "未绑定"
    assert item["has_api_key"] is False
    assert any("未完成 OAuth 绑定" in w for w in mongo["warnings"])


@pytest.mark.asyncio
async def test_validate_config_authenticated_bound_oauth_ok(monkeypatch):
    """Authenticated caller whose OAuth provider IS bound: status must be
    '已配置(订阅)' and NO 未绑定 warning."""
    scmod = _run_validate_config(
        monkeypatch, current_user={"id": "u1"}, bound={"claude-code"}
    )

    result = await scmod.validate_config(current_user={"id": "u1"})
    mongo = result["data"]["mongodb_validation"]
    items = {it["name"]: it for it in mongo["llm_providers"]}
    item = items["claude-code"]
    assert item["status"] == "已配置(订阅)"
    assert item["has_api_key"] is True
    assert not any("未完成 OAuth 绑定" in w for w in mongo["warnings"])


if __name__ == "__main__":
    import sys
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
