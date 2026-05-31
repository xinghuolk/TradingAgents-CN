"""ChatCodexOAuth must pin ``use_responses_api=False``.

Root cause of the production error ``codex 调用失败: TypeError: 'NoneType' object
is not iterable``:

``langchain_openai`` auto-routes some models / payloads through its BUILT-IN
Responses API path (``self.root_client.responses.create``), which bypasses our
``_CodexCompletionsAdapter`` and hits the Codex backend with OpenAI-standard
Responses semantics it doesn't speak. langchain then iterates the empty
``response.output`` → ``TypeError: 'NoneType' object is not iterable``.

``ChatCodexOAuth`` hooks ONLY the chat.completions path (``self.client``), so it
must force ``use_responses_api=False`` to keep every call on that path. These
tests lock that behaviour in (no network: the underlying ``responses.stream`` is
mocked).
"""

from types import SimpleNamespace

import pytest

from tradingagents.llm_adapters.codex_adapter import ChatCodexOAuth

# A syntactically valid (unsigned) JWT so construction doesn't need the network.
_FAKE_TOKEN = "eyJhbGciOiJub25lIn0.eyJ4IjoxfQ."


def _build():
    return ChatCodexOAuth(
        model="gpt-5.5-codex",
        access_token=_FAKE_TOKEN,
        temperature=0,
        max_tokens=256,
        timeout=30,
    )


def _patch_create(llm, final):
    # The adapter calls NON-streaming ``responses.create(**kwargs)`` and reads
    # ``final.output`` directly (see codex_responses_adapter._CodexCompletionsAdapter.create).
    # Replace the REAL openai client's responses surface inside the adapter so no
    # HTTP happens. ``llm.client`` is the _CodexCompletionsAdapter; ``._client``
    # is the underlying openai.OpenAI we stub.
    llm.client._client.responses = SimpleNamespace(create=lambda **kw: final)


def _final(output):
    return SimpleNamespace(
        id="resp_1",
        output=output,
        usage=SimpleNamespace(input_tokens=3, output_tokens=1, total_tokens=4),
    )


def test_use_responses_api_is_pinned_false():
    llm = _build()
    assert llm.use_responses_api is False


def test_use_responses_api_stays_false_even_for_responses_only_payload():
    """Even a payload that would normally flip langchain to the Responses API
    (built-in tools, ``include``/``text``/``truncation`` keys) must be forced to
    the chat.completions path, because our adapter only hooks that path."""
    llm = _build()
    assert llm._use_responses_api({"reasoning": {"effort": "low"}}) is False
    assert llm._use_responses_api({"include": ["reasoning.encrypted_content"]}) is False
    assert llm._use_responses_api({"truncation": "auto"}) is False


def test_invoke_routes_through_adapter_normal_text():
    llm = _build()
    final = _final([
        SimpleNamespace(
            type="message", role="assistant", status="completed",
            content=[SimpleNamespace(type="output_text", text="OK")],
        )
    ])
    _patch_create(llm, final)
    resp = llm.invoke("Hi, please reply with OK.")
    assert resp.content == "OK"


@pytest.mark.parametrize("output", [[], None])
def test_invoke_handles_empty_or_none_output(output):
    """Reasoning-only / empty replies must not raise (the chat.completions path
    yields empty content rather than crashing)."""
    llm = _build()
    _patch_create(llm, _final(output))
    resp = llm.invoke("Hi")
    assert resp.content == ""
