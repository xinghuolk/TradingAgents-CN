"""Deterministic Turtle v0.15 calculation layer."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from .facts import FormulaResult, MoneyAmount, TurtleComputedSignals, TurtleFactValue, TurtleFacts, TurtleMarketFacts, TurtleReportFacts, TurtleStatus, normalize_currency

# FX 相关 money 字段：计算层实际会做跨币换算的字段集（_money_hm / _money_target_currency 的入参）。
# turtle_analysis_tool._collect_currencies 据此收集需要解析 FX 的币种——新增/删除可换币字段时必须同步此集合。
FX_RELEVANT_MONEY_FIELDS: frozenset[str] = frozenset({
    "net_profit",
    "market_cap",
    "buyback_amount",
    "operating_cash_flow",
    "capex",
    "cash",
    "interest_bearing_debt",
})

CURRENT_YEAR_PAYOUT_FIELD = "dividend_payout_ratio_current_year"
COMMITMENT_CONTEXT_CAVEAT = (
    "commitment payout ratio not extracted; payout_M uses max(payout_3y_avg, latest_signal) "
    "without commitment cap"
)
BUYBACK_CANCELLATION_CONTEXT_CAVEAT = (
    "repurchase_of_stock used as buyback_amount input; cancellation progress is not verified"
)
MARKET_BUYBACK_EXCLUDED_CAVEAT = (
    "market buyback_amount present but excluded from R/GG; report-side buyback_amount_3y_avg is required"
)
PAYOUT_PROXY_CONTEXT_CAVEAT = "single-year report payout proxy; not a 3-year average"
CONTEXT_ONLY_CAVEATS = {
    "dividend data missing",
    "avg_payout_ratio_3y missing",
    "dividend records missing",
    "buyback data missing",
    "buyback_amount missing",
    "buyback_amount invalid",
    COMMITMENT_CONTEXT_CAVEAT,
    BUYBACK_CANCELLATION_CONTEXT_CAVEAT,
    MARKET_BUYBACK_EXCLUDED_CAVEAT,
    PAYOUT_PROXY_CONTEXT_CAVEAT,
}
CORE_RESULT_KEYS = {"payout_M", "R", "GG", "HH", "net_cash_ratio", "ev_switch", "cash_protection", "owner_earnings"}
DECISION_BLOCKING_KEYS = {"payout_M", "R", "GG"}


@dataclass(frozen=True)
class PayoutInputs:
    payout_3y_avg: float | None
    latest_signal: float | None
    commitment_ratio: float | None
    applied_value: float | None
    sources: list[str]
    missing_inputs: list[str]
    caveats: list[str]
    status: TurtleStatus


@dataclass(frozen=True)
class BuybackInputs:
    sources: list[str]
    missing_inputs: list[str]
    caveats: list[str]
    status: TurtleStatus


def _field(facts: TurtleFacts, name: str) -> TurtleFactValue | None:
    return facts.report.fields.get(name) or facts.market.fields.get(name)


def _field_candidates(facts: TurtleFacts, name: str) -> list[TurtleFactValue]:
    candidates: list[TurtleFactValue] = []
    report_fact = facts.report.fields.get(name)
    market_fact = facts.market.fields.get(name)
    if report_fact is not None:
        candidates.append(report_fact)
    if market_fact is not None and market_fact is not report_fact:
        candidates.append(market_fact)
    return candidates


def _combined_caveats(facts: TurtleFacts) -> list[str]:
    return [*facts.caveats, *facts.report.caveats, *facts.market.caveats]


def _append_caveat(caveats: list[str], caveat: str) -> None:
    if caveat not in caveats:
        caveats.append(caveat)


def _is_context_only_caveat(caveat: str) -> bool:
    return caveat in CONTEXT_ONLY_CAVEATS


def _material_caveats(caveats: Iterable[str]) -> list[str]:
    return [caveat for caveat in caveats if not _is_context_only_caveat(caveat)]


def _new_caveats(before: set[str], caveats: list[str]) -> list[str]:
    return [caveat for caveat in caveats if caveat not in before]


def _is_reliable_fact(fact: TurtleFactValue, name: str, caveats: list[str]) -> bool:
    if fact.reliability != "reliable":
        _append_caveat(caveats, f"{name} unreliable: {fact.reliability}")
        return False
    return True


def _fx_rates(facts: TurtleFacts) -> dict[str, float]:
    raw_rates = facts.report.metadata.get("fx_rates")
    if not isinstance(raw_rates, dict):
        return {}

    rates: dict[str, float] = {}
    for pair, rate in raw_rates.items():
        if not isinstance(pair, str) or isinstance(rate, bool) or not isinstance(rate, (int, float)):
            continue
        numeric_rate = float(rate)
        if math.isfinite(numeric_rate) and numeric_rate > 0:
            rates[pair.upper()] = numeric_rate
    return rates


def _usable_money_currency(fact: TurtleFactValue | None) -> str | None:
    """fact 若为 reliable、有限数值且可换算的 MoneyAmount，返回其归一化币种；否则 None。
    与 _money_hm / _money_fact_currencies 的可用性口径一致。"""
    if fact is None or not isinstance(fact.value, MoneyAmount):
        return None
    if fact.reliability != "reliable" or fact.value.reliability != "reliable":
        return None
    inner = fact.value.value
    if isinstance(inner, bool) or not isinstance(inner, (int, float)) or not math.isfinite(float(inner)):
        return None
    try:
        amount = fact.value.to_hundred_million(target_currency=fact.value.currency, fx_rates={})
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(amount.value):
        return None
    return normalize_currency(fact.value.currency)


def _money_fact_currencies(facts: TurtleFacts, names: Iterable[str]) -> set[str]:
    currencies: set[str] = set()
    for name in names:
        for fact in _field_candidates(facts, name):
            cur = _usable_money_currency(fact)
            if cur is not None:
                currencies.add(cur)
                break
    return currencies


def collect_fx_currencies(report: TurtleReportFacts, market: TurtleMarketFacts) -> set[str]:
    """payload 层据此决定需解析 FX 的币种。对每个 FX_RELEVANT_MONEY_FIELDS：
    当前期按 report 优先取第一个可用候选（与计算层 _field_candidates + break 一致，
    同名 market fallback 仅在 report 不可用时才采用）；并纳入 historical 各期（report-only）。
    无关字段 / 同名 market fallback 的币种不会触发无用 FX。"""
    currencies: set[str] = set()
    for name in FX_RELEVANT_MONEY_FIELDS:
        cur = _usable_money_currency(report.fields.get(name))
        if cur is None and name != "buyback_amount":
            cur = _usable_money_currency(market.fields.get(name))
        if cur is not None:
            currencies.add(cur)
        for hist in report.historical.values():
            hist_cur = _usable_money_currency(hist.fields.get(name))
            if hist_cur is not None:
                currencies.add(hist_cur)
    return currencies


def _money_target_currency(facts: TurtleFacts, names: Iterable[str]) -> str:
    currencies = _money_fact_currencies(facts, names)
    if len(currencies) == 1:
        return next(iter(currencies))
    return "CNY"


def _money_hm(
    facts: TurtleFacts,
    name: str,
    caveats: list[str],
    target_currency: str,
) -> tuple[float | None, list[str], list[str]]:
    failed_sources: list[str] = []
    for fact in _field_candidates(facts, name):
        if not isinstance(fact.value, MoneyAmount):
            failed_sources.append(fact.source_reference)
            continue
        if not _is_reliable_fact(fact, name, caveats):
            failed_sources.append(fact.source_reference)
            continue
        if fact.value.reliability != "reliable":
            _append_caveat(caveats, f"{name} money unreliable: {fact.value.reliability}")
            failed_sources.append(fact.source_reference)
            continue
        if isinstance(fact.value.value, bool) or not isinstance(fact.value.value, (int, float)):
            _append_caveat(caveats, f"{name} invalid money value")
            failed_sources.append(fact.source_reference)
            continue

        try:
            amount = fact.value.to_hundred_million(target_currency=target_currency, fx_rates=_fx_rates(facts))
        except (TypeError, ValueError, OverflowError) as exc:
            _append_caveat(caveats, str(exc))
            failed_sources.append(fact.source_reference)
            continue

        if not math.isfinite(amount.value):
            _append_caveat(caveats, f"{name} invalid money value")
            failed_sources.append(fact.source_reference)
            continue
        return float(amount.value), [amount.source_reference], []

    return None, failed_sources, [name]


def _money_hm_report_3y_avg(
    facts: TurtleFacts,
    name: str,
    caveats: list[str],
    target_currency: str,
) -> tuple[float | None, list[str], list[str]]:
    """Average a report-side money field across [latest + historical periods].

    Reads facts.report and facts.report.historical (report-side only).
    Threshold: >= 2 periods reliable -> mean; else None.
    """
    period_facts_list = [facts.report, *facts.report.historical.values()]
    available_values: list[float] = []
    sources: list[str] = []
    failed_sources: list[str] = []

    for period in period_facts_list:
        fact = period.fields.get(name)
        if fact is None:
            continue
        if not isinstance(fact.value, MoneyAmount):
            failed_sources.append(fact.source_reference)
            continue
        if fact.reliability != "reliable" or fact.value.reliability != "reliable":
            failed_sources.append(fact.source_reference)
            continue
        if isinstance(fact.value.value, bool) or not isinstance(fact.value.value, (int, float)):
            failed_sources.append(fact.source_reference)
            continue
        try:
            amount = fact.value.to_hundred_million(
                target_currency=target_currency, fx_rates=_fx_rates(facts),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            _append_caveat(caveats, str(exc))
            failed_sources.append(fact.source_reference)
            continue
        if not math.isfinite(amount.value):
            failed_sources.append(fact.source_reference)
            continue

        value = float(amount.value)
        if name == "buyback_amount":
            value = abs(value)
        available_values.append(value)
        sources.append(amount.source_reference)

    if len(available_values) < 2:
        return None, _merge_sources(sources, failed_sources), [f"{name}_3y_avg"]

    if len(available_values) < 3:
        _append_caveat(caveats, f"{name}_3y_avg computed from {len(available_values)}/3 periods")

    return sum(available_values) / len(available_values), sources, []


def _number_report_3y_avg(
    facts: TurtleFacts,
    name: str,
    caveats: list[str],
) -> tuple[float | None, list[str], list[str]]:
    """Average a report-side numeric (non-money) field across periods.

    Used for derived payout ratios (Spec 2 M algorithm). Same >=2 threshold.
    """
    period_facts_list = [facts.report, *facts.report.historical.values()]
    available_values: list[float] = []
    sources: list[str] = []
    failed_sources: list[str] = []

    for period in period_facts_list:
        fact = period.fields.get(name)
        if fact is None:
            continue
        if isinstance(fact.value, bool) or not isinstance(fact.value, (int, float)):
            failed_sources.append(fact.source_reference)
            continue
        if fact.reliability != "reliable":
            failed_sources.append(fact.source_reference)
            continue
        value = float(fact.value)
        if not math.isfinite(value):
            failed_sources.append(fact.source_reference)
            continue

        available_values.append(value)
        sources.append(fact.source_reference)

    if len(available_values) < 2:
        return None, _merge_sources(sources, failed_sources), [f"{name}_3y_avg"]

    if len(available_values) < 3:
        _append_caveat(caveats, f"{name}_3y_avg computed from {len(available_values)}/3 periods")

    return sum(available_values) / len(available_values), sources, []


def _number(facts: TurtleFacts, name: str, caveats: list[str]) -> tuple[float | None, list[str], list[str]]:
    failed_sources: list[str] = []
    for fact in _field_candidates(facts, name):
        if isinstance(fact.value, bool) or not isinstance(fact.value, (int, float)):
            failed_sources.append(fact.source_reference)
            continue
        if not _is_reliable_fact(fact, name, caveats):
            failed_sources.append(fact.source_reference)
            continue

        value = float(fact.value)
        if not math.isfinite(value):
            _append_caveat(caveats, f"{name} invalid numeric value")
            failed_sources.append(fact.source_reference)
            continue
        return value, [fact.source_reference], []

    return None, failed_sources, [name]


def _number_report_latest(
    facts: TurtleFacts,
    name: str,
    caveats: list[str],
) -> tuple[float | None, list[str], list[str]]:
    fact = facts.report.fields.get(name)
    if fact is None:
        return None, [], [name]
    if isinstance(fact.value, bool) or not isinstance(fact.value, (int, float)):
        _append_caveat(caveats, f"{name} invalid numeric value")
        return None, [fact.source_reference], [name]
    if not _is_reliable_fact(fact, name, caveats):
        return None, [fact.source_reference], [name]

    value = float(fact.value)
    if not math.isfinite(value):
        _append_caveat(caveats, f"{name} invalid numeric value")
        return None, [fact.source_reference], [name]
    return value, [fact.source_reference], []


def _number_alias(facts: TurtleFacts, caveats: list[str], *names: str) -> tuple[float | None, list[str], list[str]]:
    failed_sources: list[str] = []
    for name in names:
        if _field_candidates(facts, name):
            value, sources, missing = _number(facts, name, caveats)
            if not missing:
                return value, sources, missing
            failed_sources = _merge_sources(failed_sources, sources)
    return None, failed_sources, [names[0]]


def _merge_sources(*source_groups: Iterable[str]) -> list[str]:
    sources: list[str] = []
    for group in source_groups:
        for source in group:
            if source and source not in sources:
                sources.append(source)
    return sources


def _merge_missing(*missing_groups: Iterable[str]) -> list[str]:
    missing: list[str] = []
    for group in missing_groups:
        for name in group:
            if name not in missing:
                missing.append(name)
    return missing


def _fmt(value: float) -> str:
    return f"{value:g}"


def _result(
    *,
    name: str,
    formula: str,
    substitution: str,
    value: float | None,
    unit: str,
    sources: list[str],
    missing_inputs: list[str] | None = None,
    status: TurtleStatus = "complete",
) -> FormulaResult:
    return FormulaResult(
        name=name,
        formula=formula,
        substitution=substitution,
        value=value,
        unit=unit,
        sources=sources,
        missing_inputs=missing_inputs or [],
        status=status,
    )


def _target_cash_protection(net_cash_ratio: float) -> float:
    if net_cash_ratio < 20:
        return 30.0
    if net_cash_ratio < 40:
        return 25.0
    if net_cash_ratio < 60:
        return 20.0
    return 15.0


def _validate_positive_market_cap(
    market_cap: float | None,
    missing_market_cap: list[str],
    caveats: list[str],
) -> tuple[float | None, list[str]]:
    if missing_market_cap:
        return market_cap, missing_market_cap
    if market_cap is None or market_cap <= 0:
        _append_caveat(caveats, "market_cap must be positive")
        return None, ["market_cap"]
    return market_cap, []


def _buyback_3y_input(
    buyback: float | None,
    missing_buyback: list[str],
    caveats: list[str],
) -> tuple[float, list[str], bool]:
    if missing_buyback:
        _append_caveat(caveats, "buyback_amount_3y_avg missing; treated as 0 for degraded calculation")
        return 0.0, missing_buyback, True
    if buyback is None:
        _append_caveat(caveats, "buyback_amount_3y_avg missing; treated as 0 for degraded calculation")
        return 0.0, ["buyback_amount_3y_avg"], True
    return buyback, [], False


def _resolve_payout_inputs(facts: TurtleFacts, caveats: list[str]) -> PayoutInputs:
    before_avg = set(caveats)
    payout_3y_avg, avg_sources, missing_avg = _number_report_3y_avg(facts, CURRENT_YEAR_PAYOUT_FIELD, caveats)
    avg_caveats = _new_caveats(before_avg, caveats)

    before_latest = set(caveats)
    latest_signal, latest_sources, missing_latest = _number_report_latest(facts, CURRENT_YEAR_PAYOUT_FIELD, caveats)
    latest_caveats = _new_caveats(before_latest, caveats)

    _append_caveat(caveats, COMMITMENT_CONTEXT_CAVEAT)

    values = [value for value in (payout_3y_avg, latest_signal) if value is not None and math.isfinite(value)]
    applied_value = max(values) if values else None
    missing_inputs = _merge_missing(missing_avg, missing_latest)
    material_avg_caveats = _material_caveats(avg_caveats)
    if applied_value is None:
        status: TurtleStatus = "non_decisionable"
    elif missing_avg or missing_latest or material_avg_caveats:
        status = "degraded"
    else:
        status = "complete"

    return PayoutInputs(
        payout_3y_avg=payout_3y_avg,
        latest_signal=latest_signal,
        commitment_ratio=None,
        applied_value=applied_value,
        sources=_merge_sources(avg_sources, latest_sources),
        missing_inputs=missing_inputs,
        caveats=_merge_sources(avg_caveats, latest_caveats, [COMMITMENT_CONTEXT_CAVEAT]),
        status=status,
    )


def _resolve_buyback_inputs(facts: TurtleFacts, caveats: list[str]) -> BuybackInputs:
    if "buyback_amount" in facts.market.fields and "buyback_amount" not in facts.report.fields:
        _append_caveat(caveats, MARKET_BUYBACK_EXCLUDED_CAVEAT)
    if "buyback_amount" in facts.report.fields or any(
        "buyback_amount" in period.fields for period in facts.report.historical.values()
    ):
        _append_caveat(caveats, BUYBACK_CANCELLATION_CONTEXT_CAVEAT)
    return BuybackInputs(sources=[], missing_inputs=[], caveats=[], status="complete")


def _fmt_nullable(value: float | None) -> str:
    return "null" if value is None else _fmt(value)


def compute_turtle_signals(facts: TurtleFacts) -> TurtleComputedSignals:
    results: dict[str, FormulaResult] = {}
    caveats = _combined_caveats(facts)
    return_target_currency = _money_target_currency(facts, ("net_profit", "operating_cash_flow", "capex", "market_cap"))
    r_target_currency = return_target_currency
    owner_target_currency = return_target_currency
    net_cash_target_currency = _money_target_currency(facts, ("cash", "interest_bearing_debt", "market_cap"))
    owner_money_unit = f"hundred_million {owner_target_currency}"

    if facts.status == "unsupported":
        return TurtleComputedSignals(status="unsupported", results=results, caveats=caveats)

    net_profit, net_profit_sources, missing_net_profit = _money_hm(facts, "net_profit", caveats, r_target_currency)
    r_market_cap, r_market_cap_sources, missing_r_market_cap = _money_hm(facts, "market_cap", caveats, r_target_currency)
    r_market_cap, missing_r_market_cap = _validate_positive_market_cap(r_market_cap, missing_r_market_cap, caveats)
    ocf, ocf_sources, missing_ocf = _money_hm(facts, "operating_cash_flow", caveats, owner_target_currency)
    capex, capex_sources, missing_capex = _money_hm(facts, "capex", caveats, owner_target_currency)
    gg_market_cap, gg_market_cap_sources, missing_gg_market_cap = _money_hm(
        facts,
        "market_cap",
        caveats,
        owner_target_currency,
    )
    gg_market_cap, missing_gg_market_cap = _validate_positive_market_cap(
        gg_market_cap,
        missing_gg_market_cap,
        caveats,
    )
    _resolve_buyback_inputs(facts, caveats)
    r_buyback, r_buyback_sources, missing_r_buyback = _money_hm_report_3y_avg(
        facts,
        "buyback_amount",
        caveats,
        r_target_currency,
    )
    r_buyback_for_formula, r_degraded_buyback_missing, r_buyback_degraded = _buyback_3y_input(
        r_buyback,
        missing_r_buyback,
        caveats,
    )
    gg_buyback_for_formula = r_buyback_for_formula
    gg_degraded_buyback_missing = list(r_degraded_buyback_missing)
    gg_buyback_degraded = r_buyback_degraded
    gg_buyback_sources = list(r_buyback_sources)
    cash, cash_sources, missing_cash = _money_hm(facts, "cash", caveats, net_cash_target_currency)
    debt, debt_sources, missing_debt = _money_hm(facts, "interest_bearing_debt", caveats, net_cash_target_currency)
    net_cash_market_cap, net_cash_market_cap_sources, missing_net_cash_market_cap = _money_hm(
        facts,
        "market_cap",
        caveats,
        net_cash_target_currency,
    )
    net_cash_market_cap, missing_net_cash_market_cap = _validate_positive_market_cap(
        net_cash_market_cap,
        missing_net_cash_market_cap,
        caveats,
    )
    payout_inputs = _resolve_payout_inputs(facts, caveats)
    payout = payout_inputs.applied_value
    payout_sources = payout_inputs.sources
    missing_payout = payout_inputs.missing_inputs if payout is None else []
    tax_rate, tax_sources, missing_tax = _number(facts, "tax_rate", caveats)

    payout_substitution = (
        "payout_M = max(payout_3y_avg, latest_signal); "
        "commitment_ratio=null, commitment_constraint_applied=false"
    )
    if payout is not None:
        payout_substitution = (
            f"payout_M = max(payout_3y_avg={_fmt_nullable(payout_inputs.payout_3y_avg)}, "
            f"latest_signal={_fmt_nullable(payout_inputs.latest_signal)}); "
            "commitment_ratio=null, commitment_constraint_applied=false"
        )
    results["payout_M"] = _result(
        name="payout_M",
        formula="payout_M = max(payout_3y_avg, latest_signal); commitment cap not applied",
        substitution=payout_substitution,
        value=payout,
        unit="ratio",
        sources=payout_sources,
        missing_inputs=payout_inputs.missing_inputs,
        status=payout_inputs.status,
    )

    owner_missing = _merge_missing(missing_ocf, missing_capex)
    owner_sources = _merge_sources(ocf_sources, capex_sources)
    owner_earnings = None if owner_missing else ocf - abs(capex)  # type: ignore[operator]
    results["owner_earnings"] = _result(
        name="owner_earnings",
        formula="owner_earnings = operating_cash_flow - abs(capex)",
        substitution=(
            "owner_earnings = operating_cash_flow - abs(capex)"
            if owner_earnings is None
            else f"{_fmt(ocf)} - abs({_fmt(capex)})"
        ),
        value=owner_earnings,
        unit=owner_money_unit,
        sources=owner_sources,
        missing_inputs=owner_missing,
        status="non_decisionable" if owner_missing else "complete",
    )

    r_critical_missing = _merge_missing(missing_net_profit, missing_payout, missing_tax, missing_r_market_cap)
    r_missing = _merge_missing(r_critical_missing, r_degraded_buyback_missing)
    r_sources = _merge_sources(net_profit_sources, payout_sources, tax_sources, r_buyback_sources, r_market_cap_sources)
    r_value = None
    r_substitution = "(net_profit * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100"
    if not r_critical_missing:
        r_value = (net_profit * payout * (1 - tax_rate) + r_buyback_for_formula) / r_market_cap * 100  # type: ignore[operator]
        r_substitution = (
            f"({_fmt(net_profit)} * {_fmt(payout)} * (1 - {_fmt(tax_rate)}) "
            f"+ {_fmt(r_buyback_for_formula)}) / {_fmt(r_market_cap)} * 100"
        )
    r_status: TurtleStatus = "non_decisionable" if r_critical_missing else "degraded" if r_buyback_degraded else "complete"
    results["R"] = _result(
        name="R",
        formula="R = (net_profit * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100",
        substitution=r_substitution,
        value=r_value,
        unit="percent",
        sources=r_sources,
        missing_inputs=r_missing,
        status=r_status,
    )

    gg_critical_missing = _merge_missing(owner_missing, missing_payout, missing_tax, missing_gg_market_cap)
    gg_missing = _merge_missing(gg_critical_missing, gg_degraded_buyback_missing)
    gg_sources = _merge_sources(owner_sources, payout_sources, tax_sources, gg_buyback_sources, gg_market_cap_sources)
    gg_value = None
    gg_substitution = "(owner_earnings * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100"
    if not gg_critical_missing:
        gg_value = (owner_earnings * payout * (1 - tax_rate) + gg_buyback_for_formula) / gg_market_cap * 100  # type: ignore[operator]
        gg_substitution = (
            f"({_fmt(owner_earnings)} * {_fmt(payout)} * (1 - {_fmt(tax_rate)}) "
            f"+ {_fmt(gg_buyback_for_formula)}) / {_fmt(gg_market_cap)} * 100"
        )
    gg_status: TurtleStatus = "non_decisionable" if gg_critical_missing else "degraded" if gg_buyback_degraded else "complete"
    results["GG"] = _result(
        name="GG",
        formula="GG = (owner_earnings * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100",
        substitution=gg_substitution,
        value=gg_value,
        unit="percent",
        sources=gg_sources,
        missing_inputs=gg_missing,
        status=gg_status,
    )

    hh_critical_missing = _merge_missing(r_critical_missing, gg_critical_missing)
    hh_missing = list(hh_critical_missing)
    hh_value = None if r_value is None or gg_value is None else r_value - gg_value
    hh_status: TurtleStatus = (
        "non_decisionable"
        if hh_critical_missing
        else "complete"
    )
    results["HH"] = _result(
        name="HH",
        formula="HH = R - GG",
        substitution="R - GG" if hh_value is None else f"{_fmt(r_value)} - {_fmt(gg_value)}",
        value=hh_value,
        unit="percentage_points",
        sources=_merge_sources(results["R"].sources, results["GG"].sources),
        missing_inputs=hh_missing,
        status=hh_status,
    )

    net_cash_missing = _merge_missing(missing_cash, missing_debt, missing_net_cash_market_cap)
    net_cash_sources = _merge_sources(cash_sources, debt_sources, net_cash_market_cap_sources)
    net_cash_ratio = None
    net_cash_substitution = "(cash - interest_bearing_debt) / market_cap * 100"
    if not net_cash_missing:
        net_cash_ratio = (cash - debt) / net_cash_market_cap * 100  # type: ignore[operator]
        net_cash_substitution = f"({_fmt(cash)} - {_fmt(debt)}) / {_fmt(net_cash_market_cap)} * 100"
    net_cash_status: TurtleStatus = "non_decisionable" if net_cash_missing else "complete"
    results["net_cash_ratio"] = _result(
        name="net_cash_ratio",
        formula="net_cash_ratio = (cash - interest_bearing_debt) / market_cap * 100",
        substitution=net_cash_substitution,
        value=net_cash_ratio,
        unit="percent",
        sources=net_cash_sources,
        missing_inputs=net_cash_missing,
        status=net_cash_status,
    )

    ev_missing = list(results["net_cash_ratio"].missing_inputs)
    ev_value = None if ev_missing else (1.0 if net_cash_ratio > 40 else 0.0)  # type: ignore[operator]
    ev_status: TurtleStatus = "non_decisionable" if ev_missing else "complete"
    results["ev_switch"] = _result(
        name="ev_switch",
        formula="ev_switch = 1.0 if net_cash_ratio > 40 else 0.0",
        substitution="net_cash_ratio > 40" if ev_value is None else f"{_fmt(net_cash_ratio)} > 40",
        value=ev_value,
        unit="flag",
        sources=results["net_cash_ratio"].sources,
        missing_inputs=ev_missing,
        status=ev_status,
    )

    protection_missing = list(results["net_cash_ratio"].missing_inputs)
    protection_value = None if protection_missing else _target_cash_protection(net_cash_ratio)  # type: ignore[arg-type]
    protection_status: TurtleStatus = "non_decisionable" if protection_missing else "complete"
    results["cash_protection"] = _result(
        name="cash_protection",
        formula="cash_protection = target discount from net_cash_ratio bands",
        substitution="net_cash_ratio band" if protection_value is None else f"net_cash_ratio={_fmt(net_cash_ratio)}",
        value=protection_value,
        unit="percent",
        sources=results["net_cash_ratio"].sources,
        missing_inputs=protection_missing,
        status=protection_status,
    )

    core_results = {key: result for key, result in results.items() if key in CORE_RESULT_KEYS}
    blocking_non_decisionable = any(
        core_results[key].status == "non_decisionable"
        for key in DECISION_BLOCKING_KEYS
        if key in core_results
    )
    core_not_complete = any(
        result.status in {"degraded", "non_decisionable"} for result in core_results.values()
    )
    material_input_caveats = _material_caveats(caveats)

    if blocking_non_decisionable:
        status: TurtleStatus = "non_decisionable"
    elif core_not_complete or material_input_caveats:
        status = "degraded"
    else:
        status = "complete"

    return TurtleComputedSignals(status=status, results=results, caveats=caveats)
