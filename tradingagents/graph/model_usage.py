from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from threading import RLock
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
