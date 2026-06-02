"""Adapt FinancialReportClient public results into Turtle report facts."""

from __future__ import annotations

import logging
from math import isfinite
from typing import Any, Callable

logger = logging.getLogger(__name__)

from tradingagents.dataflows.financial_reports.adapter import create_financial_report_adapter
from tradingagents.dataflows.financial_reports.config import get_financial_report_client_config
from tradingagents.dataflows.financial_reports.policy import FinancialReportPolicy

from .facts import (
    MoneyAmount,
    MoneyUnit,
    TurtleFactValue,
    TurtleReportFacts,
    TurtleStatus,
    infer_turtle_period_end,
    normalize_currency,
)


TURTLE_FIELD_ALIASES = {
    "capital_expenditures": "capex",
    "cash_and_equivalents": "cash",
    "money_cap": "cash",
    "dividends_paid": "dividends_paid",
    "repurchase_of_stock": "buyback_amount",
}

CURRENT_YEAR_PAYOUT_FIELD = "dividend_payout_ratio_current_year"
CURRENT_YEAR_PAYOUT_CAVEAT = (
    "current-year payout ratio derived from report dividends_paid/net_profit; "
    "not a commitment payout ratio"
)
PAYOUT_PROXY_FIELD = "dividend_payout_ratio_proxy_single_year"
PAYOUT_PROXY_CAVEAT = "single-year report payout proxy; not a 3-year average"


def _normalize_market(market: str) -> str:
    normalized = market.strip().upper()
    return "CN" if normalized == "A" else normalized


def _field_reference(field: Any) -> str:
    field_id = str(getattr(field, "field_id", "unknown") or "unknown")
    page = getattr(field, "evidence_page", None)
    return f"{field_id} p.{page}" if page is not None else field_id


def _turtle_field_name(field_id: str) -> str:
    return TURTLE_FIELD_ALIASES.get(field_id, field_id)


def _field_unit(field: Any) -> MoneyUnit | None:
    raw = str(getattr(field, "unit", "") or "").strip()
    lowered = raw.lower()
    compact = lowered.replace(" ", "")

    if lowered in {"raw", "yuan", "rmb", "cny", "hkd", "hk$", "usd", "us$"} or raw in {
        "港元",
        "港币",
        "美元",
        "人民币",
    }:
        return "yuan"
    if lowered in {"thousand", "rmb'000", "hkd'000", "hk$'000", "usd'000", "us$'000", "000", "千元"}:
        return "thousand"
    if compact in {"rmb000", "hkd000", "hk$000", "usd000", "us$000"}:
        return "thousand"
    if lowered in {"hundred_million", "hundred million"} or raw in {"亿元"}:
        return "hundred_million"
    if lowered in {
        "million",
        "hk$ million",
        "hkd million",
        "rmb million",
        "cny million",
        "us$ million",
        "usd million",
    } or raw in {"百万", "百万元"}:
        return "million"
    if raw in {"万元"}:
        return "ten_thousand"
    if lowered in {"ten_thousand", "ten thousand"}:
        return "ten_thousand"
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
            if field_id == "buyback_amount":
                numeric_value = abs(numeric_value)
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


def _reliable_money_field(
    fields: dict[str, TurtleFactValue],
    name: str,
) -> TurtleFactValue | None:
    field = fields.get(name)
    if field is None or field.reliability != "reliable":
        return None
    if not isinstance(field.value, MoneyAmount) or field.value.reliability != "reliable":
        return None
    return field


def _is_reliable_numeric_field(field: TurtleFactValue | None) -> bool:
    if field is None or field.reliability != "reliable":
        return False
    if isinstance(field.value, bool) or not isinstance(field.value, (int, float)):
        return False
    return isfinite(float(field.value))


def _money_to_absolute_base_units(value: MoneyAmount) -> float:
    amount = value.to_hundred_million(target_currency=value.currency)
    return float(amount.value) * 100_000_000


def _derive_interest_bearing_debt(
    fields: dict[str, TurtleFactValue],
    caveats: list[str],
) -> None:
    if "interest_bearing_debt" in fields:
        return

    component_names = ("st_borr", "lt_borr", "bond_payable")
    components = [fields.get(name) for name in component_names]
    if any(component is None for component in components):
        return

    money_components = [_reliable_money_field(fields, name) for name in component_names]
    if any(component is None for component in money_components):
        return

    first_money = money_components[0].value  # type: ignore[union-attr]
    target_currency = normalize_currency(first_money.currency)
    if any(
        normalize_currency(component.value.currency) != target_currency  # type: ignore[union-attr]
        for component in money_components
    ):
        _append_caveat(caveats, "interest_bearing_debt derivation skipped: currency mismatch")
        return

    try:
        total = sum(_money_to_absolute_base_units(component.value) for component in money_components)  # type: ignore[union-attr]
    except (TypeError, ValueError, OverflowError) as exc:
        _append_caveat(caveats, f"interest_bearing_debt derivation skipped: {exc}")
        return

    source_reference = "; ".join(component.source_reference for component in money_components)  # type: ignore[union-attr]
    fields["interest_bearing_debt"] = TurtleFactValue(
        name="interest_bearing_debt",
        value=MoneyAmount(
            value=total,
            currency=first_money.currency,
            unit="yuan",
            source_label="financial-report-client:derived",
            source_reference=source_reference,
        ),
        source_label="financial-report-client:derived",
        source_reference=source_reference,
        reliability="reliable",
    )


def _derive_report_payout_fields(
    fields: dict[str, TurtleFactValue],
    caveats: list[str],
) -> None:
    dividend = _reliable_money_field(fields, "dividends_paid")
    profit = _reliable_money_field(fields, "net_profit")
    if dividend is None or profit is None:
        return

    dividend_money = dividend.value
    profit_money = profit.value
    if normalize_currency(dividend_money.currency) != normalize_currency(profit_money.currency):
        _append_caveat(caveats, "report payout ratio skipped: currency mismatch")
        return

    try:
        dividend_amount = abs(float(
            dividend_money.to_hundred_million(
                target_currency=dividend_money.currency,
            ).value
        ))
        profit_amount = float(
            profit_money.to_hundred_million(
                target_currency=profit_money.currency,
            ).value
        )
    except (TypeError, ValueError, OverflowError):
        _append_caveat(caveats, "report payout ratio skipped: invalid money value")
        return

    if profit_amount <= 0:
        _append_caveat(caveats, "report payout ratio skipped: non-positive net_profit")
        return

    ratio = dividend_amount / profit_amount
    if not isfinite(ratio):
        _append_caveat(caveats, "report payout ratio skipped: invalid payout ratio")
        return

    source_reference = f"{dividend.source_reference}; {profit.source_reference}"
    rounded_ratio = round(ratio, 12)

    if not _is_reliable_numeric_field(fields.get(CURRENT_YEAR_PAYOUT_FIELD)):
        fields[CURRENT_YEAR_PAYOUT_FIELD] = TurtleFactValue(
            name=CURRENT_YEAR_PAYOUT_FIELD,
            value=rounded_ratio,
            source_label="financial-report-client",
            source_reference=source_reference,
            reliability="reliable",
            caveat=CURRENT_YEAR_PAYOUT_CAVEAT,
        )

    if not isinstance(fields.get(PAYOUT_PROXY_FIELD), TurtleFactValue):
        fields[PAYOUT_PROXY_FIELD] = TurtleFactValue(
            name=PAYOUT_PROXY_FIELD,
            value=rounded_ratio,
            source_label="financial-report-client",
            source_reference=source_reference,
            reliability="display_only",
            caveat=PAYOUT_PROXY_CAVEAT,
        )
        _append_caveat(caveats, PAYOUT_PROXY_CAVEAT)


def build_report_facts_from_extraction(
    *,
    extraction: Any | None,
    allow_llm_models: tuple[str, ...],
    adapter_caveats: list[str],
) -> TurtleReportFacts:
    """Convert a public FinancialReportClient extraction into Turtle facts."""
    if extraction is None:
        return TurtleReportFacts(
            fields={}, metadata={},
            caveats=list(adapter_caveats),
            status="non_decisionable",
        )

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
        turtle_field_id = _turtle_field_name(field_id)
        adapted[turtle_field_id], degradation_caveat = _adapt_value(
            field_id=turtle_field_id,
            field=field,
            source_label=decision.source_label,
            reliability=reliability,
            caveat=caveat,
        )
        _append_caveat(caveats, degradation_caveat)

    _derive_report_payout_fields(adapted, caveats)
    _derive_interest_bearing_debt(adapted, caveats)

    metadata = {
        "company": getattr(extraction, "company", None),
        "market": getattr(extraction, "market", None),
        "period_end": getattr(extraction, "period_end", None),
        "catalog_version": getattr(extraction, "catalog_version", None),
    }

    if not adapted:
        status: TurtleStatus = "non_decisionable"
    elif caveats or any(
        f.reliability != "reliable"
        or (isinstance(f.value, MoneyAmount) and f.value.reliability != "reliable")
        or f.caveat
        for f in adapted.values()
    ):
        status = "degraded"
    else:
        status = "complete"

    return TurtleReportFacts(fields=adapted, metadata=metadata, caveats=caveats, status=status)


def _derive_historical_period_ends(latest_period_end: str, history_periods: int) -> list[str]:
    """Compute historical period_ends from latest period_end.

    Example: latest='2024-12-31', history_periods=2 -> ['2023-12-31', '2022-12-31']
    Only supports YYYY-12-31 (annual reports).
    """
    if history_periods <= 0:
        return []
    latest_year = int(latest_period_end[:4])
    return [f"{latest_year - n}-12-31" for n in range(1, history_periods + 1)]


def _fetch_single_period_facts(
    *,
    adapter: Any,
    ticker: str,
    market: str,
    period_end: str,
    reference_date: str,
    allow_llm_models: tuple[str, ...],
) -> TurtleReportFacts:
    """Fetch + adapt one period's annual report. market is already normalized."""
    result = adapter.get_annual_report_data(
        ticker=ticker,
        market=market,
        period_end=period_end,
        reference_date=reference_date,
    )
    facts = build_report_facts_from_extraction(
        extraction=result.extraction,
        allow_llm_models=allow_llm_models,
        adapter_caveats=list(result.warnings) + list(result.errors),
    )
    if facts.metadata.get("period_end") is not None:
        return facts
    metadata = dict(facts.metadata)
    metadata["period_end"] = period_end
    return TurtleReportFacts(
        fields=facts.fields,
        metadata=metadata,
        caveats=facts.caveats,
        status=facts.status,
        historical=facts.historical,
    )


def _fetch_periods_concurrently(
    *,
    adapter_factory: Callable[[], Any],
    ticker: str,
    market: str,
    period_ends: list[str],
    reference_date: str,
    allow_llm_models: tuple[str, ...],
) -> dict[str, TurtleReportFacts]:
    """Fetch multiple periods in parallel, keyed by period_end.

    Each worker creates its OWN adapter via adapter_factory() to avoid sharing a
    requests.Session (ReportCollectorClient) across threads. Failed periods are
    silently omitted (caller applies the >=2 threshold).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, TurtleReportFacts] = {}
    max_workers = min(len(period_ends), 3)
    if max_workers <= 0:
        return results

    def _fetch_one(period_end: str) -> TurtleReportFacts:
        return _fetch_single_period_facts(
            adapter=adapter_factory(),
            ticker=ticker, market=market,
            period_end=period_end, reference_date=reference_date,
            allow_llm_models=allow_llm_models,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_period = {pool.submit(_fetch_one, pe): pe for pe in period_ends}
        for future in as_completed(future_to_period):
            pe = future_to_period[future]
            try:
                results[pe] = future.result()
            except Exception as exc:
                logger.warning(
                    "Failed to fetch annual report for %s %s period_end=%s: %s",
                    ticker, market, pe, exc,
                )
    return results


def get_turtle_report_facts(
    *,
    ticker: str,
    market: str,
    trade_date: str,
    adapter: Any | None = None,
    allow_llm_models: tuple[str, ...] | None = None,
    history_periods: int = 0,
) -> TurtleReportFacts:
    """Fetch annual report data and adapt it to Turtle report facts.

    history_periods=0 -> latest period only (Spec 1 behavior)
    history_periods=2 -> latest + 2 prior periods (populated as facts.historical)
    """
    config = (
        get_financial_report_client_config()
        if (adapter is None or allow_llm_models is None)
        else None
    )
    allowed_models = (
        allow_llm_models
        if allow_llm_models is not None
        else (config.allow_llm_models if config is not None else ())
    )

    def _make_adapter() -> Any:
        if adapter is not None:
            return adapter
        cfg = config if config is not None else get_financial_report_client_config()
        return create_financial_report_adapter(cfg)

    latest_period_end = infer_turtle_period_end(trade_date)
    market_normalized = _normalize_market(market)

    if history_periods <= 0:
        return _fetch_single_period_facts(
            adapter=_make_adapter(), ticker=ticker, market=market_normalized,
            period_end=latest_period_end, reference_date=trade_date,
            allow_llm_models=allowed_models,
        )

    adapter_factory = _make_adapter

    historical_period_ends = _derive_historical_period_ends(latest_period_end, history_periods)
    all_periods = [latest_period_end] + historical_period_ends

    period_facts_results = _fetch_periods_concurrently(
        adapter_factory=adapter_factory, ticker=ticker, market=market_normalized,
        period_ends=all_periods, reference_date=trade_date,
        allow_llm_models=allowed_models,
    )

    latest_facts = period_facts_results.get(latest_period_end)

    # Case 1: latest raised an exception during concurrent fetch (not in results)
    if latest_facts is None:
        return TurtleReportFacts(
            fields={},
            metadata={"period_end": latest_period_end},
            caveats=[
                f"latest period {latest_period_end} extraction raised exception during concurrent fetch"
            ],
            status="non_decisionable",
            historical={},
        )

    # Case 2: latest fetched but unusable (adapter graceful failure / empty extraction).
    # Latest drives the decision, so drop historical too — multi-period analysis can't proceed.
    if latest_facts.status == "non_decisionable":
        return TurtleReportFacts(
            fields=latest_facts.fields,
            metadata=latest_facts.metadata,
            caveats=latest_facts.caveats,
            status=latest_facts.status,
            historical={},
        )

    # Case 3: latest usable -> keep, and drop non_decisionable (failed/empty) historical periods
    historical_facts = {
        pe: period_facts_results[pe]
        for pe in historical_period_ends
        if pe in period_facts_results
        and period_facts_results[pe].status != "non_decisionable"
    }
    return TurtleReportFacts(
        fields=latest_facts.fields,
        metadata=latest_facts.metadata,
        caveats=latest_facts.caveats,
        status=latest_facts.status,
        historical=historical_facts,
    )
