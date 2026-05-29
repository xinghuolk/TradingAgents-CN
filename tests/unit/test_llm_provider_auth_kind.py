"""auth_kind field on LLMProvider + LLMProviderResponse.
LLMProviderRequest deliberately does NOT expose this field — server forces
the default "api_key" on client-supplied add/update requests so untrusted
clients cannot create rogue OAuth providers with arbitrary names."""

import pytest

from app.models.config import LLMProvider, LLMProviderRequest, LLMProviderResponse


def test_llm_provider_default_is_api_key():
    p = LLMProvider(name="openai", display_name="OpenAI")
    assert p.auth_kind == "api_key"


def test_llm_provider_accepts_oauth():
    p = LLMProvider(name="codex", display_name="Codex", auth_kind="oauth")
    assert p.auth_kind == "oauth"


def test_llm_provider_rejects_unknown_auth_kind():
    with pytest.raises(Exception):
        LLMProvider(name="x", display_name="X", auth_kind="weird")


def test_legacy_doc_without_auth_kind_loads_with_default():
    # Existing Mongo docs predate this field — Pydantic must backfill default.
    legacy = {"name": "openai", "display_name": "OpenAI"}
    p = LLMProvider(**legacy)
    assert p.auth_kind == "api_key"


def test_llm_provider_response_serializes_auth_kind():
    r = LLMProviderResponse(
        id="000000000000000000000001",
        name="codex",
        display_name="Codex",
        is_active=True,
        supported_features=["chat"],
        auth_kind="oauth",
    )
    assert r.model_dump()["auth_kind"] == "oauth"


def test_llm_provider_response_default_is_api_key():
    r = LLMProviderResponse(
        id="000000000000000000000002",
        name="openai",
        display_name="OpenAI",
        is_active=True,
        supported_features=["chat"],
    )
    assert r.auth_kind == "api_key"


def test_llm_provider_request_does_not_expose_auth_kind():
    """Security guard: a client-side dict with auth_kind must be ignored by
    the request model. The server constructs LLMProvider with the default."""
    # The field must not be a declared attribute on the request model.
    assert "auth_kind" not in LLMProviderRequest.model_fields
    # Even if a client smuggles it in, Pydantic v2 with default config silently
    # drops unknown fields (model_config has no `extra="forbid"`).
    req = LLMProviderRequest(name="evil", display_name="Evil", auth_kind="oauth")
    assert not hasattr(req, "auth_kind")
