"""LangChain chat model authenticated against Codex (ChatGPT subscription).

Codex (``https://chatgpt.com/backend-api/codex``) speaks the OpenAI **Responses
API**, not Chat Completions. The original PR-2 thin ``ChatOpenAI`` subclass
assumed wire-compat with chat.completions and would have 404'd on the first
``invoke()``. This rewrite ports hermes-agent's ``_CodexCompletionsAdapter``
pattern: we build an ``openai.OpenAI`` client targeted at the Codex base URL,
wrap its ``responses.stream(...)`` call in a chat.completions-shaped shim,
and plug the shim into ChatOpenAI's ``.client`` / ``.async_client`` slots.

The end result: every ``ChatOpenAI._generate`` / ``_agenerate`` path that
calls ``self.client.create(**payload)`` transparently routes through Codex
Responses while reading back a chat.completions-shaped response.

The token is refreshed once at construction if it's already expiring; it's
then baked into the underlying OpenAI client and is NOT refreshed during the
object's lifetime. For long sessions construct a fresh ChatCodexOAuth.

Reference: ``hermes-agent/agent/auxiliary_client.py`` lines 614–870.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import openai
from langchain_openai import ChatOpenAI

from tradingagents.llm_adapters import subscription_credentials as sc
from tradingagents.llm_adapters.codex_responses_adapter import (
    _AsyncCodexCompletionsAdapter,
    _CodexCompletionsAdapter,
    _codex_default_headers,
)

logger = logging.getLogger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


class ChatCodexOAuth(ChatOpenAI):
    """ChatOpenAI variant that routes through the Codex Responses API.

    Differences from ``ChatOpenAI``:

    * ``api_key`` is the user's Codex OAuth access token (read from local
      credentials or supplied by the web OAuth flow).
    * ``base_url`` is pinned to the Codex endpoint.
    * The underlying OpenAI client carries Codex-specific headers
      (``ChatGPT-Account-ID`` from the JWT, ``originator: codex_cli_rs``,
      Cloudflare-allowlisted User-Agent).
    * ``.client`` and ``.async_client`` are replaced with a Responses-API shim
      so each call lands at ``responses.stream(...)`` rather than
      ``chat.completions.create(...)``.

    Reference: ``hermes-agent/agent/auxiliary_client.py:_CodexCompletionsAdapter``
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
        # proxy). `api_key` is NOT overridable — the whole point of this
        # adapter is to inject the OAuth token; letting a caller override
        # would silently send the wrong auth.
        kwargs.setdefault("base_url", CODEX_BASE_URL)

        # ChatOpenAI's validator requires *some* api_key value. We seed it with
        # the access token so any introspection (``chat.openai_api_key``) shows
        # a sensible value, but the real auth happens on the OpenAI client we
        # install below. Note: temperature/max_tokens kwargs are accepted but
        # the adapter strips them before they reach the Codex backend, which
        # rejects both. See _CodexCompletionsAdapter.create().
        super().__init__(
            model=model,
            api_key=access_token,
            **kwargs,
        )

        default_headers = _codex_default_headers(access_token)
        real_sync_client = openai.OpenAI(
            api_key=access_token,
            base_url=CODEX_BASE_URL,
            default_headers=default_headers,
        )
        real_async_client = openai.AsyncOpenAI(
            api_key=access_token,
            base_url=CODEX_BASE_URL,
            default_headers=default_headers,
        )

        sync_adapter = _CodexCompletionsAdapter(real_sync_client, model)
        async_adapter = _AsyncCodexCompletionsAdapter(sync_adapter)

        # In this version of langchain_openai (>=0.3, openai 1.86), the field
        # `self.client` is the `Completions` object itself, i.e. the thing whose
        # `.create()` gets called from `_generate()`. Likewise `self.async_client`
        # is the `AsyncCompletions` object. Both are pydantic fields on
        # `ChatOpenAI`; reassigning via `object.__setattr__` bypasses the model
        # validator. Our adapter exposes a `.create(**kwargs)` method, so it's
        # a drop-in for `Completions` from langchain_openai's POV.
        #
        # Verified empirically: see ChatOpenAI source lines 911, 995, 1111, 1199
        # in `langchain_openai/chat_models/base.py` for the call sites.
        object.__setattr__(self, "client", sync_adapter)
        object.__setattr__(self, "async_client", async_adapter)
        # `root_client` / `root_async_client` are also used (lines 788, 794,
        # 903, 971, 977 etc.) for the alternate Responses path. ChatOpenAI's
        # built-in Responses path expects OpenAI standard Responses semantics
        # — not the Codex variant — so we leave the real OpenAI clients in
        # place there. Codex models go through the chat.completions path,
        # which is what we've hooked.
        object.__setattr__(self, "root_client", real_sync_client)
        object.__setattr__(self, "root_async_client", real_async_client)

        logger.info(
            "ChatCodexOAuth initialized via Responses API: model=%s source=%s expires_at_ms=%s",
            model, source, expires_at_ms,
        )
