"""``_CodexCompletionsAdapter`` raw-response shim + non-streaming regression tests.

Two production bugs are locked in here:

1. ``AttributeError: '_CodexCompletionsAdapter' object has no attribute
   'with_raw_response'`` — langchain's ``_generate`` (with
   ``include_response_headers=True``) calls
   ``self.client.with_raw_response.create(**payload)`` then ``.parse()``. The
   adapter must expose that surface.

2. ``TypeError: 'NoneType' object is not iterable`` — the adapter previously used
   ``self._client.responses.stream(...)``, whose openai-SDK streaming parser
   crashed on Codex's SSE event stream. The adapter now calls NON-streaming
   ``self._client.responses.create(...)``. These tests mock ``responses.create``;
   a guard asserts the adapter works with a client that exposes ONLY ``create``
   (no ``stream``), so a regression back to streaming would fail here.

All HTTP/transport is mocked — no network, no OAuth token.
"""

import asyncio
from types import SimpleNamespace


def _fake_response(text="Hello back"):
    """A Responses-API ``Response``-like object the adapter walks via ``.output``."""
    return SimpleNamespace(
        id="resp_x",
        output=[
            SimpleNamespace(
                type="message", role="assistant", status="completed",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
    )


def _make_adapter(text="Hello back"):
    """Build a sync adapter over a fake OpenAI client whose NON-streaming
    ``responses.create(...)`` returns a fixed final response.

    The fake client exposes ONLY ``create`` (no ``stream``): if the adapter
    regressed to ``responses.stream`` it would raise AttributeError here.
    """
    from tradingagents.llm_adapters.codex_responses_adapter import _CodexCompletionsAdapter

    captured = {}

    def _create(**kwargs):
        captured["kwargs"] = kwargs
        captured["called"] = True
        return _fake_response(text)

    fake_client = SimpleNamespace(responses=SimpleNamespace(create=_create))
    return _CodexCompletionsAdapter(fake_client, "gpt-5.5-codex"), captured


def test_adapter_exposes_with_raw_response():
    adapter, _ = _make_adapter()
    assert hasattr(adapter, "with_raw_response")


def test_with_raw_response_create_parse_returns_completion():
    adapter, captured = _make_adapter()
    raw = adapter.with_raw_response.create(messages=[{"role": "user", "content": "Hi"}])
    parsed = raw.parse()
    assert captured.get("called") is True  # went through responses.create (non-streaming)
    assert parsed.choices[0].message.content == "Hello back"


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
    assert captured.get("called") is True
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
    assert captured.get("called") is True
    assert parsed.choices[0].message.content == "Hello back"
