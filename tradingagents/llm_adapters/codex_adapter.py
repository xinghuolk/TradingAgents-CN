"""LangChain chat model authenticated against Codex (ChatGPT subscription).

NOTE: The Codex API at https://chatgpt.com/backend-api/codex is undocumented.
This adapter assumes an OpenAI Chat Completions–compatible surface. If real-API
validation reveals a different wire format, expect this file to need a rewrite.

The token is refreshed once at construction if it is already expiring; it is
then baked into the underlying OpenAI client and is NOT refreshed during the
object's lifetime. For long sessions, construct a new ChatCodexOAuth instance.

Codex write-back is also deferred — `subscription_credentials.resolve("codex")`
intentionally does not persist the rotated refresh token back to
`~/.codex/auth.json` (the file schema is partly private). Repeated constructions
within the same expiry window will each consume a fresh refresh token. For
heavy use, defer to `codex login` rather than relying on auto-refresh.

Reference: hermes-agent/hermes_cli/auth.py:74-91 (Codex endpoint + client id)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_openai import ChatOpenAI

from tradingagents.llm_adapters import subscription_credentials as sc

logger = logging.getLogger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


class ChatCodexOAuth(ChatOpenAI):
    """ChatOpenAI variant that authenticates via the user's ChatGPT subscription.

    The Codex OAuth access token is used as the `api_key` (LangChain places it
    on the Authorization header), and the OpenAI base URL is swapped to the
    Codex endpoint.

    NOTE: This adapter is best-effort and has NOT yet been validated against a
    live Codex API. The wire format is assumed to match OpenAI Chat Completions
    based on available documentation. If validation in Task 11 reveals
    divergences, this adapter will need a follow-up rewrite. Any confirmed
    divergences should be documented in the design doc.
    """

    def __init__(
        self,
        model: str,
        *,
        access_token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if access_token is None:
            cred = sc.resolve("codex")
            access_token = cred.access_token
            source = cred.source
            expires_at_ms = cred.expires_at_ms
        else:
            source = "web_oauth"
            expires_at_ms = None

        # `base_url` is overridable for diagnostics (e.g. pointing at a local
        # proxy during smoke testing). `api_key` is NOT overridable — the whole
        # point of this adapter is to inject the OAuth token; allowing a caller
        # to override would silently send the wrong auth.
        kwargs.setdefault("base_url", CODEX_BASE_URL)
        super().__init__(
            model=model,
            api_key=access_token,
            **kwargs,
        )
        logger.info(
            "ChatCodexOAuth initialized: model=%s source=%s expires_at_ms=%s",
            model, source, expires_at_ms,
        )
