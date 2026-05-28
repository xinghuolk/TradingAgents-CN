"""Both bridging paths must skip OAuth subscription providers when bridging
api_key env vars. DB path uses LLMProvider.auth_kind; JSON fallback uses
the shared constant (no LLMProvider object available there)."""

import os

from app.models.config import LLMProvider
from app.core.config_bridge import _provider_is_oauth_db, _provider_is_oauth_json


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
