"""Codex Responses API → chat.completions shim.

Codex (the ChatGPT-subscription-backed endpoint at
``https://chatgpt.com/backend-api/codex``) does NOT speak the OpenAI Chat
Completions wire format. It uses the OpenAI **Responses API**, which has
a different request shape (``input_text`` / ``output_text`` content blocks,
``instructions`` separate from ``input``), different streaming events, and a
different ``function_call`` schema.

This module ports hermes-agent's ``_CodexCompletionsAdapter`` pattern so the
existing LangChain ``ChatOpenAI`` stack — which always calls
``client.chat.completions.create(...)`` and reads
``response.choices[0].message.{content,tool_calls}`` — can continue to work
unchanged. We accept chat-completions kwargs, route through
``openai.OpenAI().responses.stream(...)``, then reassemble a SimpleNamespace
response that quacks like chat.completions.

Scope (PR-2.5): text-only conversation + function tools + optional reasoning
passthrough. Multimodal, encrypted reasoning replay, and pool/retry logic
are deliberately out of scope; they live in hermes-agent for now and can be
ported in a follow-up if needed.

Reference: ``hermes-agent/agent/auxiliary_client.py:_CodexCompletionsAdapter``
(lines 414–870) and ``hermes-agent/agent/codex_responses_adapter.py``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _model_dump_value(value: Any) -> Any:
    if isinstance(value, SimpleNamespace):
        return {key: _model_dump_value(val) for key, val in vars(value).items()}
    if isinstance(value, list):
        return [_model_dump_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_model_dump_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _model_dump_value(val) for key, val in value.items()}
    return value


class _ChatCompletionResponse(SimpleNamespace):
    """Simple chat.completions-shaped response with pydantic-like dumping."""

    def model_dump(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return _model_dump_value(self)


# ---------------------------------------------------------------------------
# Headers — JWT-decoded ChatGPT-Account-ID + Cloudflare allowlist UA/originator
# ---------------------------------------------------------------------------

def _codex_default_headers(access_token: str) -> Dict[str, str]:
    """Construct mandatory Codex request headers.

    The Cloudflare layer in front of ``chatgpt.com/backend-api/codex`` whitelists
    a small set of first-party originator strings (``codex_cli_rs``,
    ``codex_vscode``, ``codex_sdk_ts``). Non-residential IPs that don't advertise
    one of those receive a 403 with ``cf-mitigated: challenge`` no matter how
    correct the auth is. We pin ``originator: codex_cli_rs`` to match the
    upstream codex-rs CLI.

    The ``ChatGPT-Account-ID`` header (canonical casing per codex-rs ``auth.rs``)
    is extracted from the OAuth JWT's ``chatgpt_account_id`` claim. JWT decoding
    is best-effort: a malformed token yields a request without the account-ID
    header, surfacing as a clean 401 instead of a construction crash.

    Note: JWT signature is intentionally NOT verified — OpenAI is the issuer,
    so any tampering would already invalidate the token at the server.
    """
    headers = {
        "User-Agent": "codex_cli_rs/0.0.0 (TradingAgents-CN)",
        "originator": "codex_cli_rs",
    }
    if not isinstance(access_token, str) or not access_token.strip():
        return headers
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return headers
        # JWT payload is the 2nd of three base64url segments; pad to 4-byte align.
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        acct_id = claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        if isinstance(acct_id, str) and acct_id:
            headers["ChatGPT-Account-ID"] = acct_id
    except Exception:  # noqa: BLE001 — best-effort decode
        # Malformed token: let auth fail naturally with a 401 from the server.
        pass
    return headers


# ---------------------------------------------------------------------------
# Tool-call ID helpers (Chat Completions ↔ Responses)
# ---------------------------------------------------------------------------

def _deterministic_call_id(fn_name: str, arguments: str, index: int = 0) -> str:
    """Stable call_id fallback derived from tool-call content.

    Deterministic IDs (vs random UUIDs) keep prompt caches warm: every retry
    of the same tool call produces the same call_id prefix and the OpenAI
    cache key still matches.
    """
    seed = f"{fn_name}:{arguments}:{index}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"call_{digest}"


def _derive_responses_function_call_id(
    call_id: str,
    response_item_id: Optional[str] = None,
) -> str:
    """Build a valid Responses ``function_call.id`` (must start with ``fc_``).

    Chat Completions uses ``call_xxx`` ids; the Responses API needs ``fc_xxx``.
    For round-trip stability we strip the ``call_`` prefix and prepend ``fc_``
    so the two ids carry the same suffix.
    """
    if isinstance(response_item_id, str):
        candidate = response_item_id.strip()
        if candidate.startswith("fc_"):
            return candidate

    source = (call_id or "").strip()
    if source.startswith("fc_"):
        return source
    if source.startswith("call_") and len(source) > len("call_"):
        return f"fc_{source[len('call_'):]}"

    # Last resort: hash whatever we've got into an fc_-prefixed token.
    import uuid
    seed = source or str(response_item_id or "") or uuid.uuid4().hex
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]
    return f"fc_{digest}"


# ---------------------------------------------------------------------------
# Message format conversion
# ---------------------------------------------------------------------------

def _chat_messages_to_responses_input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert chat.completions messages to Responses ``input`` items.

    Differences from chat.completions:

    * ``system`` messages are not part of ``input``; they're passed as a
      top-level ``instructions`` parameter. Callers handle that — we skip them
      here.
    * User text becomes ``input_text``; assistant text becomes ``output_text``.
    * Assistant ``tool_calls`` are emitted as separate ``function_call`` items
      with ``call_id`` / ``name`` / ``arguments``.
    * ``role=tool`` messages become ``function_call_output`` items with
      ``call_id`` and ``output``.

    Out of scope for PR-2.5: multimodal parts (images), encrypted reasoning
    replay. Non-text content parts are best-effort coerced to text.
    """
    items: List[Dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")

        if role == "system":
            continue  # handled via separate `instructions` parameter

        if role in ("user", "assistant"):
            content = msg.get("content", "")
            text_type = "output_text" if role == "assistant" else "input_text"

            if isinstance(content, list):
                content_parts: List[Dict[str, Any]] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        content_parts.append({"type": text_type, "text": part.get("text", "")})
                    # Non-text parts are dropped — see docstring.
            else:
                content_parts = [{"type": text_type, "text": str(content) if content else ""}]

            items.append({"role": role, "content": content_parts})

            # Assistant tool_calls → function_call items
            if role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                    fn_name = fn.get("name") or ""
                    if not fn_name:
                        continue

                    arguments = fn.get("arguments", "{}")
                    if isinstance(arguments, dict):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    elif not isinstance(arguments, str):
                        arguments = str(arguments)
                    arguments = arguments.strip() or "{}"

                    call_id = tc.get("call_id") or tc.get("id")
                    if not isinstance(call_id, str) or not call_id.strip():
                        call_id = _deterministic_call_id(fn_name, arguments, len(items))
                    call_id = call_id.strip()

                    items.append({
                        "type": "function_call",
                        "call_id": call_id,
                        "name": fn_name,
                        "arguments": arguments,
                    })
            continue

        if role == "tool":
            call_id = msg.get("tool_call_id") or msg.get("call_id") or ""
            if isinstance(call_id, str):
                call_id = call_id.strip()
            else:
                call_id = ""
            if not call_id:
                # The Responses API requires call_id on function_call_output;
                # without one the linkage to the prior function_call is lost
                # and the call would be rejected. Skip silently — the caller
                # produced a malformed tool message.
                continue
            tool_content = msg.get("content", "")
            if not isinstance(tool_content, str):
                # Best-effort flatten to JSON string for non-string tool output.
                try:
                    tool_content = json.dumps(tool_content, ensure_ascii=False)
                except (TypeError, ValueError):
                    tool_content = str(tool_content)
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": tool_content,
            })

    return items


def _responses_tools_from_chat(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Convert chat.completions ``tools`` to Responses ``tools`` schema.

    Chat completions: ``[{"type": "function", "function": {"name": ..., ...}}]``
    Responses API:    ``[{"type": "function", "name": ..., ...}]``
    """
    if not tools:
        return None
    converted: List[Dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        fn = t.get("function", {})
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        converted.append({
            "type": "function",
            "name": name,
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return converted or None


# ---------------------------------------------------------------------------
# Adapter — drop-in replacement for client.chat.completions
# ---------------------------------------------------------------------------

class _CodexRawResponse:
    """Minimal mimic of the OpenAI SDK's raw-response wrapper.

    Newer ``langchain_openai`` releases capture response headers/usage by
    calling ``self.client.with_raw_response.create(**payload)`` instead of the
    plain ``self.client.create(**payload)``. The OpenAI SDK returns a
    ``LegacyAPIResponse``/``APIResponse`` object whose ``.parse()`` yields the
    deserialized ``ChatCompletion`` and whose ``.headers`` exposes the HTTP
    response headers. langchain then does roughly::

        raw = self.client.with_raw_response.create(**payload)
        response = raw.parse()
        generation_info = {"headers": dict(raw.headers)}

    Our Codex shim has no real HTTP response (it streams the Responses API and
    reassembles a chat.completions-shaped object), so we wrap that reassembled
    object and surface empty headers. ``parse()`` returns the SAME object the
    normal ``.create()`` path returns, keeping both code paths identical.
    """

    def __init__(self, completion: Any):
        self._completion = completion
        # langchain reads ``.headers`` (a mapping). We have none, so expose an
        # empty mapping rather than ``None`` to avoid ``dict(None)`` blowing up.
        self.headers: Dict[str, str] = {}

    def parse(self) -> Any:
        return self._completion


class _CodexRawResponseNamespace:
    """The ``with_raw_response`` namespace object.

    Mirrors ``client.chat.completions.with_raw_response`` from the OpenAI SDK:
    a thin holder whose ``.create(**kwargs)`` issues the request and returns a
    raw-response wrapper. We delegate straight back to the adapter's existing
    ``.create()`` (keeping the request logic DRY) and wrap the result.
    """

    def __init__(self, adapter: "_CodexCompletionsAdapter"):
        self._adapter = adapter

    def create(self, **kwargs: Any) -> _CodexRawResponse:
        return _CodexRawResponse(self._adapter.create(**kwargs))


class _CodexCompletionsAdapter:
    """Drop-in shim accepting ``chat.completions.create(**kwargs)`` calls.

    The shim translates kwargs into a Responses streaming call, then reassembles
    a chat.completions-shaped ``SimpleNamespace`` so downstream
    ``ChatOpenAI._generate`` code reading ``response.choices[0].message.*``
    keeps working unchanged.
    """

    def __init__(self, real_client: Any, model: str):
        self._client = real_client
        self._model = model

    @property
    def with_raw_response(self) -> _CodexRawResponseNamespace:
        """Expose the OpenAI-SDK-compatible raw-response surface.

        Some ``langchain_openai`` versions call
        ``self.client.with_raw_response.create(...)`` (to read response headers)
        instead of ``self.client.create(...)``. Without this property those
        versions raise ``AttributeError: '_CodexCompletionsAdapter' object has
        no attribute 'with_raw_response'`` on the first ``invoke()``.
        """
        return _CodexRawResponseNamespace(self)

    def create(self, **kwargs: Any) -> Any:
        messages = kwargs.get("messages") or []
        model = kwargs.get("model", self._model)

        # 1. Split system → instructions; rest → input.
        instructions = "You are a helpful assistant."
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                c = msg.get("content", "")
                instructions = c if isinstance(c, str) else str(c)
                break

        input_items = _chat_messages_to_responses_input(messages)

        # 2. Build Responses API kwargs.
        resp_kwargs: Dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_items or [{"role": "user", "content": ""}],
            "store": False,
        }

        timeout = kwargs.get("timeout")
        if timeout is not None:
            resp_kwargs["timeout"] = timeout

        resp_tools = _responses_tools_from_chat(kwargs.get("tools"))
        if resp_tools:
            resp_kwargs["tools"] = resp_tools
            # Default to "auto" — matches chat.completions default behavior.
            tool_choice = kwargs.get("tool_choice", "auto")
            if tool_choice is not None:
                resp_kwargs["tool_choice"] = tool_choice
            parallel = kwargs.get("parallel_tool_calls")
            if parallel is not None:
                resp_kwargs["parallel_tool_calls"] = parallel

        # extra_body.reasoning passthrough (optional). Codex backend accepts
        # top-level `reasoning` and rejects `temperature` / `max_tokens` — DO
        # NOT forward those even if the caller set them.
        extra_body = kwargs.get("extra_body") or {}
        if isinstance(extra_body, dict):
            reasoning_cfg = extra_body.get("reasoning")
            if isinstance(reasoning_cfg, dict) and reasoning_cfg.get("enabled") is not False:
                effort = reasoning_cfg.get("effort") or "medium"
                if effort == "minimal":
                    # Codex backend rejects "minimal"; clamp to "low".
                    effort = "low"
                resp_kwargs["reasoning"] = {"effort": effort, "summary": "auto"}
                resp_kwargs["include"] = ["reasoning.encrypted_content"]

        # 3. Stream the Responses API call, collecting items + text deltas.
        collected_text: List[str] = []
        collected_items: List[Any] = []
        has_function_calls = False

        with self._client.responses.stream(**resp_kwargs) as stream:
            for event in stream:
                etype = getattr(event, "type", "") or ""
                if "output_text.delta" in etype:
                    delta = getattr(event, "delta", "")
                    if delta:
                        collected_text.append(delta)
                elif etype == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is not None:
                        collected_items.append(item)
                elif "function_call" in etype:
                    has_function_calls = True
            final = stream.get_final_response()

        # 4. Backfill final.output when the SDK leaves it empty after streaming.
        output = getattr(final, "output", None) or []
        if not output:
            if collected_items:
                output = collected_items
            elif collected_text and not has_function_calls:
                output = [SimpleNamespace(
                    type="message", role="assistant", status="completed",
                    content=[SimpleNamespace(type="output_text", text="".join(collected_text))],
                )]

        # 5. Walk the output and rebuild chat.completions text + tool_calls.
        def _item_get(obj: Any, key: str, default: Any = None) -> Any:
            val = getattr(obj, key, None)
            if val is None and isinstance(obj, dict):
                val = obj.get(key, default)
            return val if val is not None else default

        content_text = ""
        tool_calls: List[Any] = []
        for item in output:
            itype = _item_get(item, "type")
            if itype == "message":
                for part in (_item_get(item, "content") or []):
                    ptype = _item_get(part, "type")
                    if ptype in ("output_text", "text"):
                        content_text += _item_get(part, "text", "") or ""
            elif itype == "function_call":
                fn_name = _item_get(item, "name", "") or ""
                arguments = _item_get(item, "arguments", "{}") or "{}"
                call_id = _item_get(item, "call_id") or _deterministic_call_id(
                    fn_name, arguments, len(tool_calls),
                )
                tool_calls.append(SimpleNamespace(
                    id=call_id,
                    type="function",
                    function=SimpleNamespace(
                        name=fn_name,
                        arguments=arguments,
                    ),
                ))

        # 6. Reshape Responses usage (input_tokens/output_tokens) into
        #    chat.completions usage (prompt_tokens/completion_tokens).
        resp_usage = getattr(final, "usage", None)
        if resp_usage is not None and not hasattr(resp_usage, "prompt_tokens"):
            usage = SimpleNamespace(
                prompt_tokens=getattr(resp_usage, "input_tokens", 0) or 0,
                completion_tokens=getattr(resp_usage, "output_tokens", 0) or 0,
                total_tokens=getattr(resp_usage, "total_tokens", 0) or 0,
            )
        else:
            usage = resp_usage

        finish_reason = "tool_calls" if tool_calls else "stop"
        return _ChatCompletionResponse(
            id=getattr(final, "id", "") or "",
            object="chat.completion",
            model=model,
            choices=[SimpleNamespace(
                index=0,
                message=SimpleNamespace(
                    role="assistant",
                    content=content_text or None,
                    tool_calls=tool_calls or None,
                ),
                finish_reason=finish_reason,
            )],
            usage=usage,
        )


class _CodexChatShim:
    """Exposes ``.completions`` so the adapter looks like ``client.chat``."""

    def __init__(self, adapter: _CodexCompletionsAdapter):
        self.completions = adapter


class _AsyncCodexRawResponseNamespace:
    """Async counterpart of ``_CodexRawResponseNamespace``.

    Mirrors ``async_client.chat.completions.with_raw_response`` so async
    ``_agenerate`` paths that call ``await ...with_raw_response.create(...)``
    keep working. Delegates to the async adapter's ``.create`` and wraps the
    awaited result in the same ``_CodexRawResponse`` shape.
    """

    def __init__(self, adapter: "_AsyncCodexCompletionsAdapter"):
        self._adapter = adapter

    async def create(self, **kwargs: Any) -> _CodexRawResponse:
        return _CodexRawResponse(await self._adapter.create(**kwargs))


class _AsyncCodexCompletionsAdapter:
    """Async wrapper that runs the sync adapter via ``asyncio.to_thread``.

    Codex's Responses SDK has both sync and async APIs, but the wrapper logic
    is identical. Routing the async path through the sync adapter keeps the
    code DRY and avoids duplicating the (already non-trivial) streaming
    reassembly logic.
    """

    def __init__(self, sync_adapter: _CodexCompletionsAdapter):
        self._sync = sync_adapter

    @property
    def with_raw_response(self) -> _AsyncCodexRawResponseNamespace:
        """Async raw-response surface; see ``_CodexCompletionsAdapter``."""
        return _AsyncCodexRawResponseNamespace(self)

    async def create(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._sync.create, **kwargs)


class _AsyncCodexChatShim:
    def __init__(self, adapter: _AsyncCodexCompletionsAdapter):
        self.completions = adapter
