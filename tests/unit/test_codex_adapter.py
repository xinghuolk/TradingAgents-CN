"""Unit tests for ChatCodexOAuth.

These tests focus on wiring (credentials resolution, base URL, adapter
installation) rather than the conversion logic — that's covered exhaustively
in test_codex_responses_adapter.py.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from tradingagents.llm_adapters import subscription_credentials as sc
from tradingagents.llm_adapters.codex_adapter import (
    CODEX_BASE_URL,
    ChatCodexOAuth,
)
from tradingagents.llm_adapters.codex_responses_adapter import (
    _AsyncCodexCompletionsAdapter,
    _CodexCompletionsAdapter,
)


def _fresh_codex_cred() -> sc.SubscriptionCredential:
    return sc.SubscriptionCredential(
        access_token="cx-at-xyz",
        refresh_token="cx-rt-xyz",
        expires_at_ms=int(time.time() * 1000) + 3600_000,
        provider="codex",
        source="test",
    )


class TestChatCodexOAuth:
    def test_resolves_codex_credentials(self):
        """No explicit access_token → resolve('codex') is consulted."""
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()) as r:
            ChatCodexOAuth(model="gpt-5")
        r.assert_called_once_with("codex")

    def test_uses_codex_base_url(self):
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()):
            chat = ChatCodexOAuth(model="gpt-5")
        # ChatOpenAI exposes base URL on `openai_api_base` (older versions)
        # or `base_url` (newer); accept either.
        actual = getattr(chat, "openai_api_base", None) or getattr(chat, "base_url", None)
        assert str(actual).rstrip("/") == CODEX_BASE_URL.rstrip("/")

    def test_installs_responses_adapter_as_client(self):
        """`self.client` is the Responses adapter, not stock Completions."""
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()):
            chat = ChatCodexOAuth(model="gpt-5")
        assert isinstance(chat.client, _CodexCompletionsAdapter)
        assert isinstance(chat.async_client, _AsyncCodexCompletionsAdapter)
        # Both clients must expose `.create(**kwargs)` — that's the contract
        # ChatOpenAI._generate / _agenerate relies on.
        assert callable(getattr(chat.client, "create", None))
        assert callable(getattr(chat.async_client, "create", None))

    def test_root_clients_target_codex_base_url(self):
        """Underlying openai.OpenAI is pointed at the Codex endpoint."""
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()):
            chat = ChatCodexOAuth(model="gpt-5")
        # `root_client` is the openai.OpenAI instance; its base_url should be
        # the Codex endpoint, since that's what the adapter will hit.
        assert str(chat.root_client.base_url).rstrip("/") == CODEX_BASE_URL.rstrip("/")
        assert str(chat.root_async_client.base_url).rstrip("/") == CODEX_BASE_URL.rstrip("/")

    def test_passes_other_kwargs(self):
        """Untouched kwargs (temperature, etc.) don't break construction.

        Note: the adapter strips temperature/max_tokens before sending to the
        Codex backend — that's tested in test_codex_responses_adapter.py.
        Here we only verify they don't break construction.

        langchain-openai 1.x deliberately drops ``temperature`` for the gpt-5
        family (those models reject a non-default temperature), so
        ``chat.temperature`` is ``None`` for a gpt-5 Codex model — which is
        exactly what the Codex backend needs (it rejects ``temperature``). We
        therefore only assert construction succeeds and max_tokens survives;
        temperature being nulled is the new, correct SDK behavior.
        """
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()):
            chat = ChatCodexOAuth(model="gpt-5", temperature=0.4, max_tokens=2000)
        # gpt-5 family: langchain-openai 1.x nulls temperature (model rejects it).
        assert chat.temperature is None
        # ChatOpenAI may store max_tokens at top level or in model_kwargs.
        assert (
            getattr(chat, "max_tokens", None) == 2000
            or chat.model_kwargs.get("max_tokens") == 2000
        )

    def test_missing_credentials_propagates(self):
        with patch.object(
            sc, "resolve",
            side_effect=sc.SubscriptionCredentialError("no codex creds"),
        ):
            with pytest.raises(sc.SubscriptionCredentialError):
                ChatCodexOAuth(model="gpt-5")

    def test_explicit_access_token_skips_local_resolve(self):
        """When access_token is passed in, skip subscription_credentials.resolve."""
        with patch.object(sc, "resolve",
                          side_effect=AssertionError("should not be called")):
            chat = ChatCodexOAuth(
                model="gpt-5",
                access_token="web-cx-token",
            )
        # The web token should be plumbed into both adapter & root client.
        # Adapter doesn't expose the token directly (good — it lives in the
        # OpenAI() instance's auth headers), but we can verify via root_client.
        assert chat.root_client.api_key == "web-cx-token"
