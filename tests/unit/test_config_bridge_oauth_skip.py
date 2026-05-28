"""Test that config_bridge does not bridge OAuth providers' (nonexistent) api_key."""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.models.config import LLMProvider
from app.core.config_bridge import _provider_is_oauth_db, _provider_is_oauth_json


@pytest.fixture(autouse=True)
def _stubs(stub_optional_llm_deps):
    pass


def _make_llm_config(provider: str, api_key: str = "") -> object:
    """Build a minimal stand-in for app.models.config.LLMConfig."""
    return type("LLMConfig", (), {
        "provider": provider,
        "model_name": f"{provider}-test-model",
        "api_key": api_key,
        "api_base": "",
        "enabled": True,
    })()


def test_oauth_providers_dont_set_api_key_env(monkeypatch):
    """When an LLM config has provider=claude_code/codex, bridge_config_to_env
    must not set CLAUDE_CODE_API_KEY or CODEX_API_KEY env vars (these don't
    exist; OAuth path uses access_token instead, injected per-request).

    The bridge function tries MongoDB first; when that fails (as it does in
    unit tests with no MongoDB available) it falls back to
    ``unified_config.get_llm_configs()``. We patch that fallback to return
    OAuth-provider configs and assert the env vars stay clean.
    """
    monkeypatch.delenv("CLAUDE_CODE_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)

    # Defensive: simulate a (mis)configured row with a stale api_key value.
    # Even if a value is present, OAuth providers must NOT bridge it — the
    # OAuth path injects an access_token per-request from oauth_service.resolve.
    fake_unified_config = MagicMock()
    fake_unified_config.get_llm_configs.return_value = [
        _make_llm_config("claude_code", api_key="stale-should-not-leak"),
        _make_llm_config("codex", api_key="stale-should-not-leak"),
    ]
    fake_unified_config.get_data_source_configs.return_value = []
    fake_unified_config.get_default_model.return_value = None
    fake_unified_config.get_quick_analysis_model.return_value = None
    fake_unified_config.get_deep_analysis_model.return_value = None

    with patch("app.core.unified_config.unified_config", fake_unified_config), \
         patch("app.core.config_bridge._bridge_system_settings", return_value=0), \
         patch("app.core.config_bridge._bridge_datasource_details", return_value=0):
        # Force the MongoDB-first branch to fail so the JSON-file fallback
        # path (which iterates the patched llm_configs) is exercised.
        with patch("pymongo.MongoClient", side_effect=RuntimeError("no mongo in tests")):
            from app.core.config_bridge import bridge_config_to_env
            bridge_config_to_env()

    # Must not have set spurious env vars for OAuth providers
    assert "CLAUDE_CODE_API_KEY" not in os.environ, \
        "OAuth provider claude_code must NOT bridge an api_key env var"
    assert "CODEX_API_KEY" not in os.environ, \
        "OAuth provider codex must NOT bridge an api_key env var"


def test_non_oauth_providers_still_bridge_api_key(monkeypatch):
    """Regression guard: skip should be limited to claude_code/codex.

    A non-OAuth provider with a real api_key must still bridge to its env var.
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    fake_unified_config = MagicMock()
    fake_unified_config.get_llm_configs.return_value = [
        _make_llm_config("deepseek", api_key="sk-deepseek-real-key"),
    ]
    fake_unified_config.get_data_source_configs.return_value = []
    fake_unified_config.get_default_model.return_value = None
    fake_unified_config.get_quick_analysis_model.return_value = None
    fake_unified_config.get_deep_analysis_model.return_value = None

    with patch("app.core.unified_config.unified_config", fake_unified_config), \
         patch("app.core.config_bridge._bridge_system_settings", return_value=0), \
         patch("app.core.config_bridge._bridge_datasource_details", return_value=0):
        with patch("pymongo.MongoClient", side_effect=RuntimeError("no mongo in tests")):
            from app.core.config_bridge import bridge_config_to_env
            bridge_config_to_env()

    assert os.environ.get("DEEPSEEK_API_KEY") == "sk-deepseek-real-key", \
        "Non-OAuth providers must continue to bridge to env vars"
    # Cleanup so we don't pollute other tests.
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


# ────────────────────────────────────────────────────────────────────────
# Unit tests for the helper functions added in the OAuth-first-class refactor.
# These prove the helpers in isolation; the integration tests above prove they
# are correctly used by bridge_config_to_env() end-to-end.
# ────────────────────────────────────────────────────────────────────────

def test_db_path_recognizes_oauth_via_auth_kind():
    p = LLMProvider(name="codex", display_name="Codex", auth_kind="oauth")
    assert _provider_is_oauth_db(p) is True


def test_db_path_treats_api_key_provider_as_non_oauth():
    p = LLMProvider(name="openai", display_name="OpenAI")  # default api_key
    assert _provider_is_oauth_db(p) is False


def test_db_path_ignores_provider_name_when_auth_kind_is_api_key():
    """Defensive: even if a legacy row was named 'codex' but auth_kind defaults
    to api_key (no migration), we trust the field, not the name."""
    p = LLMProvider(name="codex", display_name="Codex Legacy")
    assert p.auth_kind == "api_key"
    assert _provider_is_oauth_db(p) is False


def test_json_path_recognizes_oauth_by_name():
    assert _provider_is_oauth_json("codex") is True
    assert _provider_is_oauth_json("claude_code") is True


def test_json_path_treats_unknown_name_as_non_oauth():
    assert _provider_is_oauth_json("openai") is False
    assert _provider_is_oauth_json("dashscope") is False
    assert _provider_is_oauth_json("") is False
