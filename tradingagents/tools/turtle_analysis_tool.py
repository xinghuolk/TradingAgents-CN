"""LangChain tool entry point for Turtle v0.15 value analysis preparation."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Annotated, Any

from langchain_core.tools import tool

from tradingagents.dataflows.value_investment.turtle import (
    FX_RELEVANT_MONEY_FIELDS,
    MoneyAmount,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
    compute_turtle_signals,
    get_turtle_market_facts,
    get_turtle_report_facts,
    merge_status,
    normalize_currency,
    resolve_fx_rates,
)


def _report_facts(value: Any) -> TurtleReportFacts:
    if isinstance(value, TurtleReportFacts):
        return value
    return TurtleReportFacts(
        fields=getattr(value, "fields", {}) or {},
        metadata=getattr(value, "metadata", {}) or {},
        caveats=getattr(value, "caveats", []) or [],
        status=getattr(value, "status", "complete"),
        historical=getattr(value, "historical", {}) or {},
    )


def _market_facts(value: Any) -> TurtleMarketFacts:
    if isinstance(value, TurtleMarketFacts):
        return value
    return TurtleMarketFacts(
        fields=getattr(value, "fields", {}) or {},
        caveats=getattr(value, "caveats", []) or [],
        status=getattr(value, "status", "complete"),
        metadata=getattr(value, "metadata", {}) or {},
    )


def _collect_currencies(report: TurtleReportFacts, market_facts: TurtleMarketFacts) -> set[str]:
    """收集计算层实际会做跨币换算的 money 字段的归一化去重币种集合。
    仅扫描 FX_RELEVANT_MONEY_FIELDS 中、reliable 且有限数值的 MoneyAmount（含 historical 各期），
    与 calculations._money_fact_currencies 的过滤口径一致——无关/不可计算字段不应触发 FX。
    """
    currencies: set[str] = set()

    def _scan(fields: dict[str, TurtleFactValue]) -> None:
        for name, fact in fields.items():
            if name not in FX_RELEVANT_MONEY_FIELDS:
                continue
            value = getattr(fact, "value", None)
            if not isinstance(value, MoneyAmount):
                continue
            if getattr(fact, "reliability", "reliable") != "reliable" or value.reliability != "reliable":
                continue
            inner = value.value
            if isinstance(inner, bool) or not isinstance(inner, (int, float)) or not math.isfinite(float(inner)):
                continue
            currencies.add(normalize_currency(value.currency))

    _scan(report.fields)
    _scan(market_facts.fields)
    for hist in report.historical.values():
        _scan(hist.fields)
    return currencies


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
        get_turtle_report_facts(
            ticker=ticker, market=market, trade_date=trade_date,
            history_periods=2,
        )
    )
    market_facts = _market_facts(
        get_turtle_market_facts(
            ticker=ticker,
            market=market,
            holding_channel=holding_channel,
        )
    )

    # --- Spec 3: FX 注入（锚定 market_as_of，绝不 fallback trade_date）---
    fx_caveats: list[str] = []
    fx_failed = False
    currencies = _collect_currencies(report, market_facts)
    if len(currencies) >= 2:
        market_as_of = market_facts.metadata.get("market_as_of")
        if not market_as_of:
            market_as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            fx_caveats.append("market_as_of 缺失，FX 已对齐拉取日")

        fx_rates, fx_rates_meta, resolve_caveats = resolve_fx_rates(currencies, "CNY", market_as_of)
        fx_caveats.extend(resolve_caveats)
        fx_failed = bool(resolve_caveats)

        if market_as_of != trade_date[:10]:
            fx_caveats.append(
                f"market_cap 为当前快照（as_of={market_as_of}），非 trade_date={trade_date} 当日历史值；FX 已对齐快照日期"
            )

        if report.historical and fx_rates:
            fx_caveats.append(
                f"历史各期 money 统一使用快照日 FX（as_of={market_as_of}）换算"
            )
    else:
        fx_rates, fx_rates_meta = {}, {}

    report = TurtleReportFacts(
        fields=report.fields,
        metadata={**report.metadata, "fx_rates": fx_rates, "fx_rates_meta": fx_rates_meta},
        caveats=[*report.caveats, *fx_caveats],
        status=merge_status(report.status, "degraded") if fx_failed else report.status,
        historical=report.historical,
    )
    # --- end Spec 3 ---

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
