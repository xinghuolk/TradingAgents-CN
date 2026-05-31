"""Regression tests for the Codex adapter's ``with_raw_response`` surface.

Newer ``langchain_openai`` releases capture response headers by calling
``self.client.with_raw_response.create(**payload)`` from ``_generate`` instead
of the plain ``self.client.create(**payload)``. The Codex chat-completions shim
(:class:`_CodexCompletionsAdapter`) is injected as ``ChatOpenAI``'s ``.client``,
so it must expose a ``with_raw_response`` namespace whose ``.create(...)``
returns an OpenAI-SDK-style raw response exposing ``.parse()`` and ``.headers``.

Without it, ``ChatCodexOAuth.invoke()`` raised::

    AttributeError: '_CodexCompletionsAdapter' object has no attribute
    'with_raw_response'

These tests mock the underlying Responses streaming transport, so no network
or OAuth token is required — they exercise only the
``with_raw_response`` -> ``create`` -> ``parse`` wiring.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingagents.llm_adapters.codex_responses_adapter import (
    _AsyncCodexCompletionsAdapter,
    _CodexCompletionsAdapter,
)


class _FakeStream:
    """Minimal context-manager stand-in for ``responses.stream(...)``."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter([])  # no streamed deltas; final response carries the text

    def get_final_response(self):
        return SimpleNamespace(
            id="resp_test",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[SimpleNamespace(type="output_text", text="Hello back")],
                )
            ],
            usage=SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3),
        )


def _make_adapter():
    fake_client = SimpleNamespace(responses=MagicMock())
    fake_client.responses.stream = MagicMock(return_value=_FakeStream())
    return _CodexCompletionsAdapter(fake_client, "gpt-5-codex")


def test_adapter_exposes_with_raw_response():
    adapter = _make_adapter()
    assert hasattr(adapter, "with_raw_response")


def test_with_raw_response_create_parse_returns_completion():
    adapter = _make_adapter()
    raw = adapter.with_raw_response.create(
        messages=[{"role": "user", "content": "Hi"}],
    )
    # OpenAI-SDK raw-response contract consumed by langchain_openai._generate.
    assert hasattr(raw, "parse")
    assert hasattr(raw, "headers")
    assert dict(raw.headers) == {}

    completion = raw.parse()
    assert completion.choices[0].message.content == "Hello back"
    assert completion.object == "chat.completion"


def test_raw_response_matches_plain_create():
    """``with_raw_response.create().parse()`` must equal plain ``create()``."""
    adapter = _make_adapter()
    plain = adapter.create(messages=[{"role": "user", "content": "Hi"}])
    raw = adapter.with_raw_response.create(
        messages=[{"role": "user", "content": "Hi"}],
    ).parse()
    assert plain.choices[0].message.content == raw.choices[0].message.content


def test_async_with_raw_response():
    sync_adapter = _make_adapter()
    async_adapter = _AsyncCodexCompletionsAdapter(sync_adapter)
    assert hasattr(async_adapter, "with_raw_response")

    raw = asyncio.run(
        async_adapter.with_raw_response.create(
            messages=[{"role": "user", "content": "Hi"}],
        )
    )
    assert dict(raw.headers) == {}
    assert raw.parse().choices[0].message.content == "Hello back"


def test_plain_create_path_unaffected():
    """The original ``.create()`` path must keep working unchanged."""
    adapter = _make_adapter()
    completion = adapter.create(messages=[{"role": "user", "content": "Hi"}])
    assert completion.choices[0].message.content == "Hello back"
    assert completion.choices[0].finish_reason == "stop"
