"""LangChain chat model that authenticates against Anthropic via Claude Code OAuth.

Uses the subscription's OAuth access token (read from ~/.claude/.credentials.json
or macOS Keychain) instead of an API key. The token is automatically refreshed
when expiring.

Reference: hermes-agent/agent/anthropic_adapter.py:604-621
"""
from __future__ import annotations

import logging
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic

from tradingagents.llm_adapters import subscription_credentials as sc

logger = logging.getLogger(__name__)


# Beta features Anthropic requires for OAuth-authenticated callers.
# Removing any of these will result in 401 / 500 errors from the API.
# Reference: hermes-agent/agent/anthropic_adapter.py:_OAUTH_ONLY_BETAS
OAUTH_BETA_HEADERS = (
    "oauth-2025-04-20",
    "claude-code-20250219",
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
)

_CLAUDE_CLI_VERSION = "1.0.0"  # Header value; real Claude Code CLIs use their own.


def _oauth_default_headers() -> dict:
    return {
        "anthropic-beta": ",".join(OAUTH_BETA_HEADERS),
        "user-agent": f"claude-cli/{_CLAUDE_CLI_VERSION} (TradingAgents-CN)",
        "x-app": "cli",
    }


class ChatClaudeCodeOAuth(ChatAnthropic):
    """ChatAnthropic that authenticates via Claude Code OAuth instead of an API key.

    Differences from ChatAnthropic:
      * Reads the access token from local Claude Code credentials at construction
      * Constructs the underlying anthropic.Anthropic / .AsyncAnthropic client
        with `auth_token=` (Bearer auth) instead of `api_key=` (x-api-key auth)
      * Attaches Anthropic's OAuth-required beta headers + claude-cli identity
    """

    def __init__(self, model: str, **kwargs: Any) -> None:
        cred = sc.resolve("claude_code")
        # ChatAnthropic's validator requires *some* api_key value to construct.
        # We pass a placeholder; the real auth happens on the client we install
        # immediately afterwards.
        super().__init__(model=model, anthropic_api_key="placeholder-oauth", **kwargs)

        default_headers = _oauth_default_headers()
        sync_client = anthropic.Anthropic(
            auth_token=cred.access_token,
            default_headers=default_headers,
        )
        async_client = anthropic.AsyncAnthropic(
            auth_token=cred.access_token,
            default_headers=default_headers,
        )
        # Bypass pydantic's frozen-on-some-versions guard.
        object.__setattr__(self, "_client", sync_client)
        object.__setattr__(self, "_async_client", async_client)
        logger.info(
            "ChatClaudeCodeOAuth initialized: model=%s source=%s expires_at_ms=%s",
            model, cred.source, cred.expires_at_ms,
        )
