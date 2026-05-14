"""LangChain chat model that authenticates against Anthropic via Claude Code OAuth.

Uses the subscription's OAuth access token (read from ~/.claude/.credentials.json
or macOS Keychain) instead of an API key. The token is refreshed once at
construction if it is already expiring; the resolved token is then baked into
the underlying Anthropic client and is NOT refreshed again during the object's
lifetime. For sessions longer than the token TTL (typically ~1 hour), construct
a new ChatClaudeCodeOAuth instance per request.

Reference: hermes-agent/agent/anthropic_adapter.py:604-621
"""
from __future__ import annotations

import logging
from typing import Any, Optional

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

_CLAUDE_CLI_VERSION_FALLBACK = "2.1.74"  # Used when `claude --version` is unavailable.


def _detect_claude_code_version() -> str:
    """Return the installed Claude Code version string, or a static fallback.

    Anthropic's OAuth infrastructure validates the user-agent version and may
    reject requests whose version is too far behind the current release.
    Reference: hermes-agent/agent/anthropic_adapter.py:_detect_claude_code_version
    """
    import subprocess as _sp

    for cmd in ("claude", "claude-code"):
        try:
            result = _sp.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                version = result.stdout.strip().split()[0]
                if version and version[0].isdigit():
                    return version
        except Exception:
            pass
    return _CLAUDE_CLI_VERSION_FALLBACK


def _oauth_default_headers() -> dict:
    version = _detect_claude_code_version()
    return {
        "anthropic-beta": ",".join(OAUTH_BETA_HEADERS),
        "user-agent": f"claude-cli/{version} (TradingAgents-CN)",
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

    def __init__(
        self,
        model: str,
        *,
        access_token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if access_token is None:
            # CLI / local-dev path: resolve from local credentials
            cred = sc.resolve("claude_code")
            access_token = cred.access_token
            source = cred.source
            expires_at_ms = cred.expires_at_ms
        else:
            # Web path: caller (oauth_service.resolve) provided the token
            source = "web_oauth"
            expires_at_ms = None

        # ChatAnthropic's validator requires *some* api_key value to construct.
        # We pass a placeholder; the real auth happens on the client we install
        # immediately afterwards.
        super().__init__(model=model, anthropic_api_key="placeholder-oauth", **kwargs)

        default_headers = _oauth_default_headers()
        sync_client = anthropic.Anthropic(
            auth_token=access_token,
            default_headers=default_headers,
        )
        async_client = anthropic.AsyncAnthropic(
            auth_token=access_token,
            default_headers=default_headers,
        )
        # _client / _async_client are functools.cached_property descriptors on
        # ChatAnthropic. Writing to __dict__ directly (via object.__setattr__)
        # shadows the descriptor before it fires, so the cached factory never
        # runs and our OAuth-authenticated client is what every request uses.
        object.__setattr__(self, "_client", sync_client)
        object.__setattr__(self, "_async_client", async_client)
        logger.info(
            "ChatClaudeCodeOAuth initialized: model=%s source=%s expires_at_ms=%s",
            model, source, expires_at_ms,
        )
