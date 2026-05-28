"""Route-level guard: GET /api/config/llm/providers must surface auth_kind
in the JSON response. The model has the field and a default; the route
must explicitly pass it through, otherwise oauth providers serialize as
api_key (silent regression of every OAuth UI behavior)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models.config import LLMProvider, LLMProviderResponse


@pytest.mark.asyncio
async def test_get_llm_providers_response_passes_auth_kind(monkeypatch):
    """When the service returns a provider with auth_kind='oauth', the route
    must construct LLMProviderResponse with auth_kind='oauth' (not the default)."""
    from app.routers import config as config_router

    oauth_provider = LLMProvider(
        name="codex",
        display_name="OpenAI Codex (订阅)",
        is_active=True,
        auth_kind="oauth",
    )
    api_key_provider = LLMProvider(
        name="openai",
        display_name="OpenAI",
        is_active=True,
        # auth_kind defaults to "api_key"
    )

    fake_service = MagicMock()
    fake_service.get_llm_providers = AsyncMock(return_value=[oauth_provider, api_key_provider])
    monkeypatch.setattr(config_router, "config_service", fake_service)

    # Build a fake user object — only used by Depends() signature
    fake_user = MagicMock()

    # Call the route handler directly (bypassing FastAPI)
    result = await config_router.get_llm_providers(current_user=fake_user)

    assert len(result) == 2
    by_name = {r.name: r for r in result}
    assert by_name["codex"].auth_kind == "oauth", f"oauth not preserved, got {by_name['codex'].auth_kind!r}"
    assert by_name["openai"].auth_kind == "api_key"
