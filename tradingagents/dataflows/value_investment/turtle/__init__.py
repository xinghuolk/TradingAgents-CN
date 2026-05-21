"""Turtle v0.15 value-investment flow helpers."""

from .calculations import compute_turtle_signals
from .decision import build_non_decisionable_report, build_turtle_decision_prompt
from .facts import (
    FormulaResult,
    MoneyAmount,
    TurtleComputedSignals,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
    default_holding_channel,
    infer_turtle_period_end,
    merge_status,
)
from .formatting import facts_to_markdown, signals_to_markdown
from .market_adapter import build_market_facts, default_tax_rate, get_turtle_market_facts
from .report_adapter import build_report_facts_from_extraction, get_turtle_report_facts

__all__ = [
    "FormulaResult",
    "MoneyAmount",
    "TurtleComputedSignals",
    "TurtleFactValue",
    "TurtleFacts",
    "TurtleMarketFacts",
    "TurtleReportFacts",
    "TurtleRunContext",
    "build_market_facts",
    "build_non_decisionable_report",
    "build_report_facts_from_extraction",
    "build_turtle_decision_prompt",
    "compute_turtle_signals",
    "default_holding_channel",
    "default_tax_rate",
    "facts_to_markdown",
    "get_turtle_market_facts",
    "get_turtle_report_facts",
    "infer_turtle_period_end",
    "merge_status",
    "signals_to_markdown",
]
