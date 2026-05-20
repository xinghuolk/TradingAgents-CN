"""Markdown formatting helpers for Turtle v0.15 decision prompts."""

from __future__ import annotations

import json
from typing import Any

from .facts import TurtleComputedSignals, TurtleFacts


def _escape_markdown_fences(body: str) -> str:
    return body.replace("```", "\\u0060\\u0060\\u0060")


def _to_json_markdown(title: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    body = _escape_markdown_fences(body)
    return f"## {title}\n\n```json\n{body}\n```"


def facts_to_markdown(facts: TurtleFacts) -> str:
    """Render Turtle facts as deterministic prompt-ready markdown."""
    return _to_json_markdown("TurtleFacts", facts.to_dict())


def signals_to_markdown(signals: TurtleComputedSignals) -> str:
    """Render computed Turtle signals as deterministic prompt-ready markdown."""
    return _to_json_markdown("TurtleComputedSignals", signals.to_dict())
