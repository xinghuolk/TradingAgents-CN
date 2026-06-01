from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from threading import RLock
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any


CANONICAL_NODE_KEYS = {
    "Market Analyst": "market_analyst",
    "tools_market": "market_analyst",
    "Msg Clear Market": "market_analyst",
    "Fundamentals Analyst": "fundamentals_analyst",
    "tools_fundamentals": "fundamentals_analyst",
    "Msg Clear Fundamentals": "fundamentals_analyst",
    "News Analyst": "news_analyst",
    "tools_news": "news_analyst",
    "Msg Clear News": "news_analyst",
    "Social Analyst": "social_analyst",
    "tools_social": "social_analyst",
    "Msg Clear Social": "social_analyst",
    "Value Analyst": "value_analyst",
    "tools_value": "value_analyst",
    "Msg Clear Value": "value_analyst",
    "Bull Researcher": "bull_researcher",
    "Bear Researcher": "bear_researcher",
    "Research Manager": "research_manager",
    "Trader": "trader",
    "Risky Analyst": "risky_analyst",
    "Safe Analyst": "safe_analyst",
    "Neutral Analyst": "neutral_analyst",
    "Risk Judge": "risk_judge",
    "SignalProcessor": "signal_processor",
    "signal_processor": "signal_processor",
}

DISPLAY_NAMES = {
    "market_analyst": "市场技术分析",
    "fundamentals_analyst": "基本面分析",
    "news_analyst": "新闻事件分析",
    "social_analyst": "市场情绪分析",
    "value_analyst": "价值投资分析",
    "bull_researcher": "多头研究员",
    "bear_researcher": "空头研究员",
    "research_manager": "研究经理决策",
    "trader": "交易员计划",
    "risky_analyst": "激进分析师",
    "safe_analyst": "保守分析师",
    "neutral_analyst": "中性分析师",
    "risk_judge": "投资组合经理",
    "signal_processor": "信号处理",
}

_current_task_id: ContextVar[str | None] = ContextVar(
    "model_usage_task_id", default=None
)
_current_node_name: ContextVar[str | None] = ContextVar(
    "model_usage_node_name", default=None
)

_usage_lock = RLock()
# The graph runner is expected to snapshot task usage and clear it in a finally
# block so completed or failed runs do not leave stale task aggregates behind.
_usage_by_task: dict[str, dict[str, dict[str, Any]]] = {}


def canonical_node_key(node_name: str) -> str:
    return CANONICAL_NODE_KEYS.get(node_name, node_name)


@contextmanager
def model_usage_context(task_id: str | None = None, node_name: str | None = None):
    task_token: Token[str | None] | None = None
    node_token: Token[str | None] | None = None
    if task_id is not None:
        task_token = _current_task_id.set(task_id)
    if node_name is not None:
        node_token = _current_node_name.set(node_name)
    try:
        yield
    finally:
        if node_token is not None:
            _current_node_name.reset(node_token)
        if task_token is not None:
            _current_task_id.reset(task_token)


def record_llm_call(
    *,
    provider: str | None = None,
    model: str | None = None,
    duration_seconds: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost: float | None = None,
    currency: str | None = None,
) -> None:
    task_id = _current_task_id.get()
    node_name = _current_node_name.get()
    if not task_id or not node_name:
        return

    normalized_currency = _normalize_currency(currency)
    node_key = canonical_node_key(node_name)
    with _usage_lock:
        task_usage = _usage_by_task.setdefault(task_id, {})
        node_usage = task_usage.setdefault(node_key, _empty_node_aggregate())
        node_usage["calls"] += 1
        node_usage["input_tokens"] += input_tokens if input_tokens is not None else 0
        node_usage["output_tokens"] += (
            output_tokens if output_tokens is not None else 0
        )
        node_usage["duration_seconds"] += (
            duration_seconds if duration_seconds is not None else 0
        )
        if provider:
            node_usage["providers"].add(provider)
        if model:
            node_usage["models"].add(model)
        if cost is not None and normalized_currency is not None:
            node_usage["costs_by_currency"][normalized_currency] = (
                node_usage["costs_by_currency"].get(normalized_currency, 0) + cost
            )
        if cost is None or normalized_currency is None:
            node_usage["missing_cost_metadata"] = True
        if (
            input_tokens is None
            or output_tokens is None
            or cost is None
            or normalized_currency is None
        ):
            node_usage["partial"] = True


def get_model_usage_snapshot(task_id: str) -> dict[str, Any]:
    with _usage_lock:
        task_usage = _usage_by_task.get(task_id)
        if not task_usage:
            return _empty_snapshot()

        nodes = {
            node_key: _node_snapshot(node_key, node_usage)
            for node_key, node_usage in sorted(task_usage.items())
        }
        summary = _summary_snapshot(nodes)
        return {"summary": summary, "nodes": nodes}


def clear_model_usage(task_id: str) -> None:
    with _usage_lock:
        _usage_by_task.pop(task_id, None)


def describe_llm(llm: Any) -> str:
    model_name = _get_value(llm, "model_name")
    if model_name is None:
        model_name = _get_value(llm, "model")
    if model_name:
        return f"{llm.__class__.__name__}:{model_name}"
    return llm.__class__.__name__


def instrument_llm_for_model_usage(
    llm: Any,
    *,
    provider: str,
    model: str,
    currency: str | None = None,
) -> Any:
    if llm is None:
        return llm

    _set_private_attr(llm, "_model_usage_provider", provider)
    _set_private_attr(llm, "_model_usage_model", model)
    _set_private_attr(llm, "_model_usage_currency", currency)

    _instrument_call_method(llm, "invoke", provider, model, currency)
    _instrument_call_method(llm, "ainvoke", provider, model, currency)
    _instrument_stream_method(llm, "stream", provider, model, currency)
    _instrument_stream_method(llm, "astream", provider, model, currency)
    _instrument_bind_tools(llm, provider, model, currency)
    return llm


def _empty_node_aggregate() -> dict[str, Any]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "duration_seconds": 0.0,
        "providers": set(),
        "models": set(),
        "costs_by_currency": {},
        "partial": False,
        "missing_cost_metadata": False,
    }


def _empty_snapshot() -> dict[str, Any]:
    return {
        "summary": {
            "total_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration_seconds": 0,
            "costs_by_currency": {},
        },
        "nodes": {},
    }


def _node_snapshot(node_key: str, node_usage: dict[str, Any]) -> dict[str, Any]:
    providers = sorted(node_usage["providers"])
    models = sorted(node_usage["models"])
    costs_by_currency = dict(sorted(node_usage["costs_by_currency"].items()))
    complete_cost = (
        not node_usage["missing_cost_metadata"] and len(costs_by_currency) == 1
    )
    return {
        "display_name": DISPLAY_NAMES.get(node_key, node_key),
        "provider": _single_or_mixed(providers),
        "model": _single_or_mixed(models),
        "providers": providers,
        "models": models,
        "calls": node_usage["calls"],
        "input_tokens": node_usage["input_tokens"],
        "output_tokens": node_usage["output_tokens"],
        "duration_seconds": node_usage["duration_seconds"],
        "cost": next(iter(costs_by_currency.values())) if complete_cost else None,
        "currency": next(iter(costs_by_currency.keys())) if complete_cost else None,
        "costs_by_currency": costs_by_currency,
        "partial": node_usage["partial"],
    }


def _summary_snapshot(nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summary = _empty_snapshot()["summary"]
    for node in nodes.values():
        summary["total_calls"] += node["calls"]
        summary["input_tokens"] += node["input_tokens"]
        summary["output_tokens"] += node["output_tokens"]
        summary["duration_seconds"] += node["duration_seconds"]
        for currency, cost in node["costs_by_currency"].items():
            summary["costs_by_currency"][currency] = (
                summary["costs_by_currency"].get(currency, 0) + cost
            )
    summary["costs_by_currency"] = dict(sorted(summary["costs_by_currency"].items()))
    return summary


def _single_or_mixed(values: list[str]) -> str | None:
    if len(values) == 1:
        return values[0]
    if len(values) > 1:
        return "mixed"
    return None


def _normalize_currency(currency: str | None) -> str | None:
    if currency is None:
        return None
    normalized = currency.strip()
    return normalized or None


def _instrument_call_method(
    llm: Any,
    method_name: str,
    provider: str,
    model: str,
    currency: str | None,
) -> None:
    original_attr = f"_model_usage_original_{method_name}"
    if _has_private_attr(llm, original_attr) or not hasattr(llm, method_name):
        return

    original = getattr(llm, method_name)

    if method_name.startswith("a"):

        async def async_wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = await original(*args, **kwargs)
            except Exception:
                _record_from_result(
                    None, provider, model, currency, time.perf_counter() - start
                )
                raise
            _record_from_result(
                result, provider, model, currency, time.perf_counter() - start
            )
            return result

        wrapped = async_wrapped
    else:

        def wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = original(*args, **kwargs)
            except Exception:
                _record_from_result(
                    None, provider, model, currency, time.perf_counter() - start
                )
                raise
            _record_from_result(
                result, provider, model, currency, time.perf_counter() - start
            )
            return result

    _set_private_attr(llm, original_attr, original)
    _set_private_attr(llm, method_name, wrapped)


def _instrument_stream_method(
    llm: Any,
    method_name: str,
    provider: str,
    model: str,
    currency: str | None,
) -> None:
    original_attr = f"_model_usage_original_{method_name}"
    if _has_private_attr(llm, original_attr) or not hasattr(llm, method_name):
        return

    original = getattr(llm, method_name)

    if method_name.startswith("a"):

        def async_stream_wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = original(*args, **kwargs)
            except Exception:
                _record_from_result(
                    None, provider, model, currency, time.perf_counter() - start
                )
                raise
            if hasattr(result, "__aiter__"):
                return _recording_async_iterator(
                    result, provider, model, currency, start
                )
            _record_from_result(
                result, provider, model, currency, time.perf_counter() - start
            )
            return result

        wrapped = async_stream_wrapped
    else:

        def stream_wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = original(*args, **kwargs)
            except Exception:
                _record_from_result(
                    None, provider, model, currency, time.perf_counter() - start
                )
                raise
            if hasattr(result, "__iter__") and not isinstance(
                result, (str, bytes, Mapping)
            ):
                return _recording_iterator(result, provider, model, currency, start)
            _record_from_result(
                result, provider, model, currency, time.perf_counter() - start
            )
            return result

        wrapped = stream_wrapped

    _set_private_attr(llm, original_attr, original)
    _set_private_attr(llm, method_name, wrapped)


def _instrument_bind_tools(
    llm: Any,
    provider: str,
    model: str,
    currency: str | None,
) -> None:
    original_attr = "_model_usage_original_bind_tools"
    if _has_private_attr(llm, original_attr) or not hasattr(llm, "bind_tools"):
        return

    original = getattr(llm, "bind_tools")

    def bind_tools_wrapped(*args, **kwargs):
        bound = original(*args, **kwargs)
        return instrument_llm_for_model_usage(
            bound, provider=provider, model=model, currency=currency
        )

    _set_private_attr(llm, original_attr, original)
    _set_private_attr(llm, "bind_tools", bind_tools_wrapped)


def _recording_iterator(
    iterator: Iterator[Any],
    provider: str,
    model: str,
    currency: str | None,
    start: float,
):
    last_chunk = None
    try:
        for chunk in iterator:
            last_chunk = chunk
            yield chunk
    finally:
        _record_from_result(
            last_chunk, provider, model, currency, time.perf_counter() - start
        )


async def _recording_async_iterator(
    iterator: AsyncIterator[Any],
    provider: str,
    model: str,
    currency: str | None,
    start: float,
):
    last_chunk = None
    try:
        async for chunk in iterator:
            last_chunk = chunk
            yield chunk
    finally:
        _record_from_result(
            last_chunk, provider, model, currency, time.perf_counter() - start
        )


def _record_from_result(
    result: Any,
    provider: str,
    model: str,
    currency: str | None,
    duration_seconds: float,
) -> None:
    usage = _extract_usage_tokens(result)
    record_llm_call(
        provider=provider,
        model=model,
        duration_seconds=duration_seconds,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        currency=currency,
    )


def _extract_usage_tokens(result: Any) -> dict[str, int | None]:
    usage_sources = [
        _get_value(result, "usage_metadata"),
        _get_nested_value(result, ("response_metadata", "token_usage")),
        _get_nested_value(result, ("llm_output", "token_usage")),
        _get_value(result, "usage"),
        result,
    ]
    for usage in usage_sources:
        tokens = _tokens_from_usage(usage)
        if tokens["input_tokens"] is not None or tokens["output_tokens"] is not None:
            return tokens
    return {"input_tokens": None, "output_tokens": None}


def _tokens_from_usage(usage: Any) -> dict[str, int | None]:
    if usage is None:
        return {"input_tokens": None, "output_tokens": None}
    return {
        "input_tokens": _first_int(
            usage,
            (
                "input_tokens",
                "prompt_tokens",
                "input_token_count",
                "prompt_token_count",
            ),
        ),
        "output_tokens": _first_int(
            usage,
            (
                "output_tokens",
                "completion_tokens",
                "generated_tokens",
                "completion_token_count",
            ),
        ),
    }


def _first_int(source: Any, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _get_value(source, key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _get_nested_value(source: Any, path: tuple[str, ...]) -> Any:
    value = source
    for key in path:
        value = _get_value(value, key)
        if value is None:
            return None
    return value


def _get_value(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _has_private_attr(obj: Any, name: str) -> bool:
    try:
        object.__getattribute__(obj, name)
        return True
    except AttributeError:
        return False


def _set_private_attr(obj: Any, name: str, value: Any) -> None:
    try:
        setattr(obj, name, value)
    except Exception:
        object.__setattr__(obj, name, value)
