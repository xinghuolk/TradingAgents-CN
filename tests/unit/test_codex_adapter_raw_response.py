"""``_CodexCompletionsAdapter`` raw-response shim + streaming regression tests.

Two production bugs are locked in here:

1. ``AttributeError: '_CodexCompletionsAdapter' object has no attribute
   'with_raw_response'`` — langchain's ``_generate`` (with
   ``include_response_headers=True``) calls
   ``self.client.with_raw_response.create(**payload)`` then ``.parse()``. The
   adapter must expose that surface.

2. ``400 {'detail': 'Stream must be set to true'}`` — the Codex backend REQUIRES
   ``stream=true``. The adapter must call ``self._client.responses.stream(...)``
   (which sets ``stream=true``), NOT the non-streaming ``responses.create(...)``.
   These tests mock ``responses.stream`` with a context-manager fake that yields
   SSE-style events and exposes ``get_final_response()``. A guard asserts the
   adapter calls ``.stream`` (and never ``.create``), so a regression back to
   non-streaming would fail here.

   (The ``TypeError: 'NoneType' object is not iterable`` that the streaming path
   hit on openai 1.86.0 is fixed by the openai 2.x upgrade, whose Responses
   streaming parser is compatible with Codex's SSE — the same path the sibling
   ``hermes-agent`` project runs on openai 2.24.0.)

All HTTP/transport is mocked — no network, no OAuth token.
"""

import asyncio
from types import SimpleNamespace


class _FakeStream:
    """Context-manager stand-in for ``responses.stream(...)``.

    Yields ``output_text.delta`` events and exposes ``get_final_response()``
    returning a Responses-API ``Response``-like object the adapter walks via
    ``.output``.
    """

    def __init__(self, text="Hello back"):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        # Stream the text in two deltas to exercise the delta-accumulation path.
        yield SimpleNamespace(type="response.output_text.delta", delta=self._text[:5])
        yield SimpleNamespace(type="response.output_text.delta", delta=self._text[5:])

    def get_final_response(self):
        return SimpleNamespace(
            id="resp_x",
            output=[
                SimpleNamespace(
                    type="message", role="assistant", status="completed",
                    content=[SimpleNamespace(type="output_text", text=self._text)],
                )
            ],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def _make_adapter(text="Hello back"):
    """Build a sync adapter over a fake OpenAI client whose ``responses.stream``
    returns a context-manager fake.

    The fake client exposes ONLY ``stream`` (no ``create``): if the adapter
    regressed to the non-streaming ``responses.create`` it would raise
    AttributeError here, and Codex would reject the call with
    ``400 Stream must be set to true``.
    """
    from tradingagents.llm_adapters.codex_responses_adapter import _CodexCompletionsAdapter

    captured = {}

    def _stream(**kwargs):
        captured["kwargs"] = kwargs
        captured["stream_called"] = True
        return _FakeStream(text)

    fake_client = SimpleNamespace(responses=SimpleNamespace(stream=_stream))
    return _CodexCompletionsAdapter(fake_client, "gpt-5.5-codex"), captured


def test_adapter_exposes_with_raw_response():
    adapter, _ = _make_adapter()
    assert hasattr(adapter, "with_raw_response")


def test_with_raw_response_create_parse_returns_completion():
    adapter, captured = _make_adapter()
    raw = adapter.with_raw_response.create(messages=[{"role": "user", "content": "Hi"}])
    parsed = raw.parse()
    # Guard: went through responses.stream (streaming), NOT responses.create.
    assert captured.get("stream_called") is True
    assert parsed.choices[0].message.content == "Hello back"


def test_adapter_calls_stream_not_create():
    """Guard against regression to the non-streaming path: Codex needs stream=true."""
    adapter, captured = _make_adapter()
    adapter.create(messages=[{"role": "user", "content": "Hi"}])
    assert captured.get("stream_called") is True


def test_with_raw_response_headers_is_empty_mapping():
    adapter, _ = _make_adapter()
    raw = adapter.with_raw_response.create(messages=[{"role": "user", "content": "Hi"}])
    # langchain does ``dict(raw.headers)`` — must be a mapping, not None.
    assert raw.headers == {}
    assert dict(raw.headers) == {}


def test_raw_response_matches_plain_create():
    adapter, _ = _make_adapter()
    plain = adapter.create(messages=[{"role": "user", "content": "Hi"}])
    raw = adapter.with_raw_response.create(messages=[{"role": "user", "content": "Hi"}]).parse()
    assert plain.choices[0].message.content == raw.choices[0].message.content


def test_plain_create_path_unaffected():
    """The original ``.create()`` path must keep working unchanged."""
    adapter, captured = _make_adapter()
    completion = adapter.create(messages=[{"role": "user", "content": "Hi"}])
    assert captured.get("stream_called") is True
    assert completion.choices[0].message.content == "Hello back"


def test_async_with_raw_response():
    sync_adapter, captured = _make_adapter()
    from tradingagents.llm_adapters.codex_responses_adapter import _AsyncCodexCompletionsAdapter
    async_adapter = _AsyncCodexCompletionsAdapter(sync_adapter)

    async def _run():
        raw = await async_adapter.with_raw_response.create(
            messages=[{"role": "user", "content": "Hi"}]
        )
        return raw.parse()

    parsed = asyncio.run(_run())
    assert captured.get("stream_called") is True
    assert parsed.choices[0].message.content == "Hello back"
