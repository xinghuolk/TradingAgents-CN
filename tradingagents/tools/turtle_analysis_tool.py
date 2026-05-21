"""LangChain tool entry point for Turtle v0.15 value analysis preparation."""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import tool

from tradingagents.dataflows.value_investment.turtle import (
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
    compute_turtle_signals,
    get_turtle_market_facts,
    get_turtle_report_facts,
    merge_status,
)


def _report_facts(value: Any) -> TurtleReportFacts:
    if isinstance(value, TurtleReportFacts):
        return value
    return TurtleReportFacts(
        fields=getattr(value, "fields", {}) or {},
        metadata=getattr(value, "metadata", {}) or {},
        caveats=getattr(value, "caveats", []) or [],
        status=getattr(value, "status", "complete"),
    )


def _market_facts(value: Any) -> TurtleMarketFacts:
    if isinstance(value, TurtleMarketFacts):
        return value
    return TurtleMarketFacts(
        fields=getattr(value, "fields", {}) or {},
        caveats=getattr(value, "caveats", []) or [],
        status=getattr(value, "status", "complete"),
    )


def prepare_turtle_analysis_payload(
    ticker: str,
    market: str,
    trade_date: str,
    company_name: str,
    holding_channel: str | None = None,
) -> str:
    """Return serialized Turtle facts and deterministic computed signals."""
    context = TurtleRunContext.for_ticker(
        ticker=ticker,
        market=market,
        trade_date=trade_date,
        company_name=company_name,
        holding_channel=holding_channel,
    )
    report = _report_facts(
        get_turtle_report_facts(ticker=ticker, market=market, trade_date=trade_date)
    )
    market_facts = _market_facts(
        get_turtle_market_facts(
            ticker=ticker,
            market=market,
            holding_channel=holding_channel,
        )
    )
    facts = TurtleFacts(
        context=context,
        report=report,
        market=market_facts,
        status=merge_status(report.status, market_facts.status),
        caveats=[*report.caveats, *market_facts.caveats],
    )
    signals = compute_turtle_signals(facts)
    return json.dumps(
        {"facts": facts.to_dict(), "signals": signals.to_dict()},
        ensure_ascii=False,
    )


@tool
def prepare_turtle_analysis(
    ticker: Annotated[str, "股票代码（支持A股、港股）"],
    market: Annotated[str, "市场类型：A=A股, HK=港股"],
    trade_date: Annotated[str, "交易日期，格式 yyyy-mm-dd"],
    company_name: Annotated[str, "公司名称"] = "",
    holding_channel: Annotated[str | None, "持仓渠道，可选"] = None,
) -> str:
    """Prepare Turtle facts and computed signals for final value analysis."""
    return prepare_turtle_analysis_payload(
        ticker=ticker,
        market=market,
        trade_date=trade_date,
        company_name=company_name or ticker,
        holding_channel=holding_channel,
    )


__all__ = ["prepare_turtle_analysis", "prepare_turtle_analysis_payload"]
