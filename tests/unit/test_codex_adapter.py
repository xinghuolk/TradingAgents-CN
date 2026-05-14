"""Unit tests for ChatCodexOAuth."""
import time
from unittest.mock import patch

import pytest

from tradingagents.llm_adapters import subscription_credentials as sc
from tradingagents.llm_adapters.codex_adapter import (
    ChatCodexOAuth,
    CODEX_BASE_URL,
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
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()) as r:
            ChatCodexOAuth(model="gpt-5")
        r.assert_called_once_with("codex")

    def test_uses_codex_base_url(self):
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()):
            chat = ChatCodexOAuth(model="gpt-5")
        # ChatOpenAI stores base URL on `openai_api_base` in some versions and
        # `base_url` in others. Accept either.
        actual = getattr(chat, "openai_api_base", None) or getattr(chat, "base_url", None)
        assert str(actual).rstrip("/") == CODEX_BASE_URL.rstrip("/")

    def test_uses_access_token_as_api_key(self):
        cred = _fresh_codex_cred()
        with patch.object(sc, "resolve", return_value=cred):
            chat = ChatCodexOAuth(model="gpt-5")
        # ChatOpenAI uses `openai_api_key` (SecretStr) in some versions, `api_key` in others
        api_key_field = getattr(chat, "openai_api_key", None) or getattr(chat, "api_key", None)
        # SecretStr requires .get_secret_value(); raw str compares directly
        raw = api_key_field.get_secret_value() if hasattr(api_key_field, "get_secret_value") else api_key_field
        assert raw == "cx-at-xyz"

    def test_passes_other_kwargs(self):
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()):
            chat = ChatCodexOAuth(model="gpt-5", temperature=0.4, max_tokens=2000)
        assert chat.temperature == 0.4
        # ChatOpenAI may store as max_tokens or model_kwargs; accept either
        assert getattr(chat, "max_tokens", None) == 2000 or chat.model_kwargs.get("max_tokens") == 2000

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
        api_key_field = getattr(chat, "openai_api_key", None) or getattr(chat, "api_key", None)
        raw = api_key_field.get_secret_value() if hasattr(api_key_field, "get_secret_value") else api_key_field
        assert raw == "web-cx-token"
