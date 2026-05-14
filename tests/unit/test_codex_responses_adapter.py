"""Unit tests for the Codex Responses → chat.completions adapter."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingagents.llm_adapters.codex_responses_adapter import (
    _AsyncCodexCompletionsAdapter,
    _CodexCompletionsAdapter,
    _chat_messages_to_responses_input,
    _codex_default_headers,
    _derive_responses_function_call_id,
    _deterministic_call_id,
    _responses_tools_from_chat,
)


# ---------------------------------------------------------------------------
# _codex_default_headers (JWT decode)
# ---------------------------------------------------------------------------

def _make_jwt(payload: dict) -> str:
    """Build a JWT-shaped string (header.payload.sig) with the given payload.

    Signature is verified — we deliberately use an unverified decode in the
    adapter — so any bytes are fine for the third segment.
    """
    header_b64 = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode().rstrip("=")
    return f"{header_b64}.{payload_b64}.sig"


class TestCodexDefaultHeaders:
    def test_baseline_headers_always_present(self):
        h = _codex_default_headers("not.a.jwt")
        assert h["User-Agent"].startswith("codex_cli_rs/")
        assert h["originator"] == "codex_cli_rs"

    def test_extracts_chatgpt_account_id_from_valid_jwt(self):
        token = _make_jwt({
            "https://api.openai.com/auth": {"chatgpt_account_id": "acc_xyz"},
        })
        h = _codex_default_headers(token)
        assert h["ChatGPT-Account-ID"] == "acc_xyz"
        assert h["originator"] == "codex_cli_rs"

    def test_omits_account_id_when_jwt_missing_claim(self):
        token = _make_jwt({"some_other_claim": "value"})
        h = _codex_default_headers(token)
        assert "ChatGPT-Account-ID" not in h

    def test_malformed_jwt_does_not_crash(self):
        # Single segment, no dots: would IndexError without the guard.
        h = _codex_default_headers("not-a-jwt-at-all")
        assert "ChatGPT-Account-ID" not in h
        assert h["originator"] == "codex_cli_rs"

    def test_empty_token_returns_baseline(self):
        h = _codex_default_headers("")
        assert "ChatGPT-Account-ID" not in h
        assert "originator" in h

    def test_non_string_token_returns_baseline(self):
        h = _codex_default_headers(None)  # type: ignore[arg-type]
        assert "ChatGPT-Account-ID" not in h
        assert "User-Agent" in h


# ---------------------------------------------------------------------------
# _derive_responses_function_call_id / _deterministic_call_id
# ---------------------------------------------------------------------------

class TestCallIdConversion:
    def test_call_prefix_maps_to_fc_prefix(self):
        assert _derive_responses_function_call_id("call_xyz123") == "fc_xyz123"

    def test_fc_prefix_is_passed_through(self):
        assert _derive_responses_function_call_id("fc_abc") == "fc_abc"

    def test_response_item_id_preferred_when_fc_prefixed(self):
        assert _derive_responses_function_call_id("call_xyz", response_item_id="fc_zzz") == "fc_zzz"

    def test_unprefixed_id_falls_back_to_hash(self):
        out = _derive_responses_function_call_id("opaque-id-without-prefix")
        assert out.startswith("fc_")
        assert len(out) > 3

    def test_deterministic_call_id_is_stable(self):
        a = _deterministic_call_id("search", '{"q":"x"}', 0)
        b = _deterministic_call_id("search", '{"q":"x"}', 0)
        assert a == b
        assert a.startswith("call_")

    def test_deterministic_call_id_varies_by_index(self):
        a = _deterministic_call_id("search", '{"q":"x"}', 0)
        b = _deterministic_call_id("search", '{"q":"x"}', 1)
        assert a != b


# ---------------------------------------------------------------------------
# _chat_messages_to_responses_input
# ---------------------------------------------------------------------------

class TestMessageConversion:
    def test_system_messages_dropped(self):
        out = _chat_messages_to_responses_input([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ])
        assert all(m.get("role") != "system" for m in out)
        # User message should still be present.
        assert any(m.get("role") == "user" for m in out)

    def test_user_message_uses_input_text(self):
        out = _chat_messages_to_responses_input([
            {"role": "user", "content": "hello"},
        ])
        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert out[0]["content"][0] == {"type": "input_text", "text": "hello"}

    def test_assistant_message_uses_output_text(self):
        out = _chat_messages_to_responses_input([
            {"role": "assistant", "content": "world"},
        ])
        assert out[0]["role"] == "assistant"
        assert out[0]["content"][0] == {"type": "output_text", "text": "world"}

    def test_assistant_tool_calls_become_function_call_items(self):
        out = _chat_messages_to_responses_input([
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_xyz",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q":"AAPL"}'},
                    }
                ],
            }
        ])
        # First item is the empty assistant message, second is the function_call.
        fc = [i for i in out if i.get("type") == "function_call"]
        assert len(fc) == 1
        assert fc[0]["call_id"] == "call_xyz"
        assert fc[0]["name"] == "search"
        assert fc[0]["arguments"] == '{"q":"AAPL"}'

    def test_assistant_tool_call_arguments_dict_is_jsonified(self):
        out = _chat_messages_to_responses_input([
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "f", "arguments": {"key": "val"}},
                }],
            }
        ])
        fc = [i for i in out if i.get("type") == "function_call"][0]
        assert fc["arguments"] == '{"key": "val"}'

    def test_tool_role_becomes_function_call_output(self):
        out = _chat_messages_to_responses_input([
            {"role": "tool", "tool_call_id": "call_xyz", "content": "result-text"},
        ])
        assert len(out) == 1
        assert out[0] == {
            "type": "function_call_output",
            "call_id": "call_xyz",
            "output": "result-text",
        }

    def test_tool_message_without_call_id_is_dropped(self):
        # Without a call_id, the Responses API would reject the item; skipping
        # silently keeps the rest of the conversation valid.
        out = _chat_messages_to_responses_input([
            {"role": "tool", "content": "result"},
        ])
        assert out == []

    def test_multimodal_text_parts_preserved(self):
        out = _chat_messages_to_responses_input([
            {"role": "user", "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:..."}},  # dropped
            ]},
        ])
        # Only the text part survives (multimodal-image is out of scope here).
        parts = out[0]["content"]
        assert parts == [{"type": "input_text", "text": "hi"}]

    def test_non_dict_messages_ignored(self):
        out = _chat_messages_to_responses_input([
            "garbage",  # type: ignore[list-item]
            {"role": "user", "content": "ok"},
        ])
        assert len(out) == 1


# ---------------------------------------------------------------------------
# _responses_tools_from_chat
# ---------------------------------------------------------------------------

class TestToolsConversion:
    def test_none_returns_none(self):
        assert _responses_tools_from_chat(None) is None
        assert _responses_tools_from_chat([]) is None

    def test_flattens_chat_function_schema(self):
        out = _responses_tools_from_chat([
            {
                "type": "function",
                "function": {
                    "name": "fetch",
                    "description": "do a thing",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ])
        assert out == [{
            "type": "function",
            "name": "fetch",
            "description": "do a thing",
            "parameters": {"type": "object", "properties": {}},
        }]

    def test_drops_tools_without_function_name(self):
        out = _responses_tools_from_chat([
            {"type": "function", "function": {"description": "no name"}},
        ])
        assert out is None


# ---------------------------------------------------------------------------
# _CodexCompletionsAdapter.create (with mocked stream)
# ---------------------------------------------------------------------------

class _MockStreamContext:
    """A minimal stand-in for ``client.responses.stream(**kwargs)``."""

    def __init__(self, events, final):
        self._events = events
        self._final = final
        self.received_kwargs: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_response(self):
        return self._final


def _make_responses_client(events, final):
    """Build a fake ``openai.OpenAI`` client that returns the given stream."""
    stream_ctx = _MockStreamContext(events, final)
    fake = MagicMock()
    fake.responses.stream = MagicMock(return_value=stream_ctx)
    return fake, stream_ctx


class TestAdapterCreate:
    def test_text_response_returns_chat_completions_shape(self):
        final = SimpleNamespace(
            id="resp_abc",
            output=[
                SimpleNamespace(
                    type="message", role="assistant", status="completed",
                    content=[SimpleNamespace(type="output_text", text="Hello world")],
                ),
            ],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        client, _ = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        resp = adapter.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": "you are helpful"},
                {"role": "user", "content": "hi"},
            ],
        )

        assert resp.id == "resp_abc"
        assert resp.choices[0].message.role == "assistant"
        assert resp.choices[0].message.content == "Hello world"
        assert resp.choices[0].message.tool_calls is None
        assert resp.choices[0].finish_reason == "stop"
        # Usage was reshaped from input_tokens/output_tokens.
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.completion_tokens == 5
        assert resp.usage.total_tokens == 15

    def test_response_supports_model_dump_for_langchain_openai(self):
        final = SimpleNamespace(
            id="resp_dump",
            output=[
                SimpleNamespace(
                    type="message", role="assistant", status="completed",
                    content=[SimpleNamespace(type="output_text", text="Hello dump")],
                ),
            ],
            usage=SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5),
        )
        client, _ = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        resp = adapter.create(messages=[{"role": "user", "content": "hi"}])
        dumped = resp.model_dump()

        assert dumped["id"] == "resp_dump"
        assert dumped["choices"][0]["message"]["role"] == "assistant"
        assert dumped["choices"][0]["message"]["content"] == "Hello dump"
        assert dumped["usage"]["prompt_tokens"] == 3

    def test_system_routed_to_instructions(self):
        final = SimpleNamespace(id="resp_x", output=[], usage=None)
        client, ctx = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        adapter.create(messages=[
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "hi"},
        ])

        # Inspect the kwargs that were passed to responses.stream(...).
        sent_kwargs = client.responses.stream.call_args.kwargs
        assert sent_kwargs["instructions"] == "Be terse."
        # The user message survives as input; the system message does not.
        assert all(m.get("role") != "system" for m in sent_kwargs["input"])
        assert sent_kwargs["store"] is False

    def test_temperature_and_max_tokens_are_stripped(self):
        # Codex backend rejects these — the adapter must not forward them.
        final = SimpleNamespace(id="r", output=[], usage=None)
        client, _ = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        adapter.create(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=2000,
        )
        sent = client.responses.stream.call_args.kwargs
        assert "temperature" not in sent
        assert "max_tokens" not in sent
        assert "max_output_tokens" not in sent

    def test_tool_calls_in_response_become_chat_tool_calls(self):
        final = SimpleNamespace(
            id="r_tool",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_xyz",
                    name="search",
                    arguments='{"q":"AAPL"}',
                ),
            ],
            usage=None,
        )
        client, _ = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        resp = adapter.create(messages=[{"role": "user", "content": "search aapl"}])

        tcs = resp.choices[0].message.tool_calls
        assert tcs is not None and len(tcs) == 1
        assert tcs[0].id == "call_xyz"
        assert tcs[0].function.name == "search"
        assert tcs[0].function.arguments == '{"q":"AAPL"}'
        assert resp.choices[0].finish_reason == "tool_calls"

    def test_empty_output_backfilled_from_text_deltas(self):
        # Stream emits deltas but final.output is empty — adapter must synthesize.
        delta_events = [
            SimpleNamespace(type="response.output_text.delta", delta="Hello "),
            SimpleNamespace(type="response.output_text.delta", delta="world"),
        ]
        final = SimpleNamespace(id="r", output=[], usage=None)
        client, _ = _make_responses_client(events=delta_events, final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        resp = adapter.create(messages=[{"role": "user", "content": "hi"}])
        assert resp.choices[0].message.content == "Hello world"

    def test_tools_kwarg_converted_to_responses_schema(self):
        final = SimpleNamespace(id="r", output=[], usage=None)
        client, _ = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        adapter.create(
            messages=[{"role": "user", "content": "go"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "fetch_price",
                    "description": "Fetch a price",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        )
        sent = client.responses.stream.call_args.kwargs
        assert sent["tools"] == [{
            "type": "function",
            "name": "fetch_price",
            "description": "Fetch a price",
            "parameters": {"type": "object", "properties": {}},
        }]
        assert sent["tool_choice"] == "auto"

    def test_reasoning_passthrough_via_extra_body(self):
        final = SimpleNamespace(id="r", output=[], usage=None)
        client, _ = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        adapter.create(
            messages=[{"role": "user", "content": "think"}],
            extra_body={"reasoning": {"effort": "high"}},
        )
        sent = client.responses.stream.call_args.kwargs
        assert sent["reasoning"] == {"effort": "high", "summary": "auto"}
        assert "reasoning.encrypted_content" in sent["include"]

    def test_reasoning_minimal_clamped_to_low(self):
        final = SimpleNamespace(id="r", output=[], usage=None)
        client, _ = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        adapter.create(
            messages=[{"role": "user", "content": "x"}],
            extra_body={"reasoning": {"effort": "minimal"}},
        )
        sent = client.responses.stream.call_args.kwargs
        assert sent["reasoning"]["effort"] == "low"

    def test_reasoning_disabled_skips_passthrough(self):
        final = SimpleNamespace(id="r", output=[], usage=None)
        client, _ = _make_responses_client(events=[], final=final)
        adapter = _CodexCompletionsAdapter(client, "gpt-5")

        adapter.create(
            messages=[{"role": "user", "content": "x"}],
            extra_body={"reasoning": {"enabled": False}},
        )
        sent = client.responses.stream.call_args.kwargs
        assert "reasoning" not in sent


# ---------------------------------------------------------------------------
# Async adapter
# ---------------------------------------------------------------------------

class TestAsyncAdapter:
    @pytest.mark.asyncio
    async def test_async_adapter_delegates_to_sync(self):
        final = SimpleNamespace(
            id="r_async",
            output=[
                SimpleNamespace(
                    type="message", role="assistant", status="completed",
                    content=[SimpleNamespace(type="output_text", text="async-ok")],
                ),
            ],
            usage=None,
        )
        client, _ = _make_responses_client(events=[], final=final)
        sync_adapter = _CodexCompletionsAdapter(client, "gpt-5")
        async_adapter = _AsyncCodexCompletionsAdapter(sync_adapter)

        resp = await async_adapter.create(messages=[{"role": "user", "content": "hi"}])
        assert resp.choices[0].message.content == "async-ok"
