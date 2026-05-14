"""Unit tests for ChatClaudeCodeOAuth.

We don't make real network calls — we verify that the underlying anthropic
SDK client is constructed with the right auth and headers.
"""
import time
from unittest.mock import patch, MagicMock

import pytest

from tradingagents.llm_adapters import subscription_credentials as sc
from tradingagents.llm_adapters.claude_code_adapter import (
    ChatClaudeCodeOAuth,
    OAUTH_BETA_HEADERS,
)


def _fresh_cred() -> sc.SubscriptionCredential:
    return sc.SubscriptionCredential(
        access_token="oauth-at-xyz",
        refresh_token="rt-xyz",
        expires_at_ms=int(time.time() * 1000) + 3600_000,
        provider="claude_code",
        source="test",
    )


class TestChatClaudeCodeOAuth:
    def test_resolves_credentials_on_init(self):
        with patch.object(sc, "resolve", return_value=_fresh_cred()) as resolve_mock:
            ChatClaudeCodeOAuth(model="claude-opus-4-7")
        resolve_mock.assert_called_once_with("claude_code")

    def test_underlying_clients_use_auth_token(self):
        cred = _fresh_cred()
        fake_sync = MagicMock(name="anthropic.Anthropic")
        fake_async = MagicMock(name="anthropic.AsyncAnthropic")
        with patch.object(sc, "resolve", return_value=cred), \
             patch("anthropic.Anthropic", return_value=fake_sync) as sync_ctor, \
             patch("anthropic.AsyncAnthropic", return_value=fake_async) as async_ctor:
            chat = ChatClaudeCodeOAuth(model="claude-opus-4-7", temperature=0.5)

        # Both clients constructed with auth_token, not api_key
        for ctor in (sync_ctor, async_ctor):
            kwargs = ctor.call_args.kwargs
            assert "auth_token" in kwargs and kwargs["auth_token"] == "oauth-at-xyz"
            assert "api_key" not in kwargs or not kwargs.get("api_key")
            # OAuth headers must be present
            hdr = kwargs["default_headers"]
            assert "anthropic-beta" in hdr
            for required in OAUTH_BETA_HEADERS:
                assert required in hdr["anthropic-beta"], f"missing beta: {required}"
            assert hdr.get("x-app") == "cli"
            assert hdr.get("user-agent", "").startswith("claude-cli/")
        # And ChatAnthropic's internal client attrs were replaced
        assert chat._client is fake_sync
        assert chat._async_client is fake_async

    def test_passes_temperature_and_max_tokens_to_parent(self):
        with patch.object(sc, "resolve", return_value=_fresh_cred()), \
             patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            chat = ChatClaudeCodeOAuth(
                model="claude-opus-4-7",
                temperature=0.3,
                max_tokens=8000,
            )
        assert chat.temperature == 0.3
        assert chat.max_tokens == 8000
        assert chat.model == "claude-opus-4-7"

    def test_missing_credentials_propagates(self):
        with patch.object(
            sc, "resolve",
            side_effect=sc.SubscriptionCredentialError("no creds"),
        ):
            with pytest.raises(sc.SubscriptionCredentialError):
                ChatClaudeCodeOAuth(model="claude-opus-4-7")
