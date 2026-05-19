"""Adapt FinancialReportClient public results into Turtle report facts."""

from __future__ import annotations

from math import isfinite
from typing import Any

from tradingagents.dataflows.financial_reports.adapter import create_financial_report_adapter
from tradingagents.dataflows.financial_reports.config import get_financial_report_client_config
from tradingagents.dataflows.financial_reports.policy import FinancialReportPolicy

from .facts import (
    MoneyAmount,
    MoneyUnit,
    TurtleFactValue,
    TurtleReportFacts,
    infer_turtle_period_end,
)


def _normalize_market(market: str) -> str:
    normalized = market.strip().upper()
    return "CN" if normalized == "A" else normalized


def _field_reference(field: Any) -> str:
    field_id = str(getattr(field, "field_id", "unknown") or "unknown")
    page = getattr(field, "evidence_page", None)
    return f"{field_id} p.{page}" if page is not None else field_id


def _field_unit(field: Any) -> MoneyUnit | None:
    raw = str(getattr(field, "unit", "") or "").strip().lower()
    if raw in {"yuan", "rmb", "cny"}:
        return "yuan"
    if raw in {"thousand", "rmb'000", "000", "千元"}:
        return "thousand"
    if raw in {"ten_thousand", "ten thousand", "万元"}:
        return "ten_thousand"
    if raw in {"million", "百万", "百万元"}:
        return "million"
    if raw in {"hundred_million", "hundred million", "亿元"}:
        return "hundred_million"
    return None


def _field_currency(field: Any) -> str | None:
    raw = str(getattr(field, "currency", "") or "").strip()
    return raw.upper() if raw else None


def _is_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _numeric_display_only_caveat(field_id: str, field: Any, value: float) -> str | None:
    if not isfinite(value):
        return f"{field_id} is display-only: non-finite numeric value"

    if _field_currency(field) is None:
        return f"{field_id} is display-only: missing currency"

    raw_unit = str(getattr(field, "unit", "") or "").strip()
    if not raw_unit:
        return f"{field_id} is display-only: missing unit"
    if _field_unit(field) is None:
        return f"{field_id} is display-only: unsupported unit {raw_unit}"

    return None


def _adapt_value(
    *,
    field_id: str,
    field: Any,
    source_label: str,
    reliability: str,
    caveat: str | None,
) -> tuple[TurtleFactValue, str | None]:
    raw_value = getattr(field, "value", None)
    source_reference = _field_reference(field)
    degradation_caveat = None
    if isinstance(raw_value, bool):
        value = raw_value
        reliability = "display_only"
        degradation_caveat = f"{field_id} is display-only: boolean value"
        caveat = _merge_caveats(caveat, degradation_caveat)
    elif _is_numeric(raw_value):
        numeric_value = float(raw_value)
        unsafe_caveat = _numeric_display_only_caveat(field_id, field, numeric_value)
        if unsafe_caveat is None:
            currency = _field_currency(field)
            unit = _field_unit(field)
            assert currency is not None
            assert unit is not None
            value: Any = MoneyAmount(
                value=numeric_value,
                currency=currency,
                unit=unit,
                source_label=source_label,
                source_reference=source_reference,
                reliability=reliability,
            )
        else:
            value = raw_value
            reliability = "display_only"
            degradation_caveat = unsafe_caveat
            caveat = _merge_caveats(caveat, unsafe_caveat)
    else:
        value = raw_value

    return (
        TurtleFactValue(
            name=field_id,
            value=value,
            source_label=source_label,
            source_reference=source_reference,
            reliability=reliability,
            caveat=caveat,
        ),
        degradation_caveat,
    )


def _merge_caveats(*caveats: str | None) -> str | None:
    present = [caveat for caveat in caveats if caveat]
    return "; ".join(present) if present else None


def _append_caveat(caveats: list[str], caveat: str | None) -> None:
    if caveat and caveat not in caveats:
        caveats.append(caveat)


def build_report_facts_from_extraction(
    *,
    extraction: Any | None,
    allow_llm_models: tuple[str, ...],
    adapter_caveats: list[str],
) -> TurtleReportFacts:
    """Convert a public FinancialReportClient extraction into Turtle facts."""
    if extraction is None:
        return TurtleReportFacts(fields={}, metadata={}, caveats=list(adapter_caveats))

    policy = FinancialReportPolicy(allow_llm_models=allow_llm_models)
    raw_fields = getattr(extraction, "fields", None)
    source_fields = raw_fields if isinstance(raw_fields, dict) else {}
    adapted: dict[str, TurtleFactValue] = {}
    caveats = list(adapter_caveats)
    staleness = getattr(extraction, "staleness", None)
    stale_extraction = bool(getattr(staleness, "is_stale", False))
    if stale_extraction:
        _append_caveat(caveats, "stale extraction is display-only by policy")

    for field_id, field in source_fields.items():
        decision = policy.decide(field=field, result=extraction)
        _append_caveat(caveats, decision.caveat)
        if not decision.can_compute and not decision.can_display:
            continue

        reliability = "reliable" if decision.can_compute and not stale_extraction else "display_only"
        caveat = decision.caveat
        if stale_extraction:
            caveat = _merge_caveats(caveat, "stale extraction is display-only by policy")
        adapted[field_id], degradation_caveat = _adapt_value(
            field_id=field_id,
            field=field,
            source_label=decision.source_label,
            reliability=reliability,
            caveat=caveat,
        )
        _append_caveat(caveats, degradation_caveat)

    metadata = {
        "company": getattr(extraction, "company", None),
        "market": getattr(extraction, "market", None),
        "period_end": getattr(extraction, "period_end", None),
        "catalog_version": getattr(extraction, "catalog_version", None),
    }
    return TurtleReportFacts(fields=adapted, metadata=metadata, caveats=caveats)


def get_turtle_report_facts(
    *,
    ticker: str,
    market: str,
    trade_date: str,
    adapter: Any | None = None,
    allow_llm_models: tuple[str, ...] | None = None,
) -> TurtleReportFacts:
    """Fetch annual report data and adapt it to Turtle report facts."""
    config = None
    active_adapter = adapter
    if active_adapter is None:
        config = get_financial_report_client_config()
        active_adapter = create_financial_report_adapter(config)

    allowed_models = allow_llm_models
    if allowed_models is None:
        config = config or get_financial_report_client_config()
        allowed_models = config.allow_llm_models

    period_end = infer_turtle_period_end(trade_date)
    result = active_adapter.get_annual_report_data(
        ticker=ticker,
        market=_normalize_market(market),
        period_end=period_end,
        reference_date=trade_date,
    )
    facts = build_report_facts_from_extraction(
        extraction=result.extraction,
        allow_llm_models=allowed_models,
        adapter_caveats=list(result.warnings) + list(result.errors),
    )
    if facts.metadata.get("period_end") is not None:
        return facts

    metadata = dict(facts.metadata)
    metadata["period_end"] = period_end
    return TurtleReportFacts(fields=facts.fields, metadata=metadata, caveats=facts.caveats)
