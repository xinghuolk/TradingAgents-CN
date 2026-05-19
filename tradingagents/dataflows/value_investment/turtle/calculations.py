"""Deterministic Turtle v0.15 calculation layer."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .facts import FormulaResult, MoneyAmount, TurtleComputedSignals, TurtleFactValue, TurtleFacts, TurtleStatus


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


FORMULA_MONEY_FIELDS = (
    "net_profit",
    "operating_cash_flow",
    "capex",
    "cash",
    "interest_bearing_debt",
    "market_cap",
    "buyback_amount",
)


def _money_fact_currencies(facts: TurtleFacts, names: Iterable[str] = FORMULA_MONEY_FIELDS) -> set[str]:
    currencies: set[str] = set()
    for name in names:
        for fact in _field_candidates(facts, name):
            if not isinstance(fact.value, MoneyAmount):
                continue
            if fact.reliability != "reliable" or fact.value.reliability != "reliable":
                continue
            if isinstance(fact.value.value, bool) or not isinstance(fact.value.value, (int, float)):
                continue
            if not math.isfinite(float(fact.value.value)):
                continue
            currencies.add(fact.value.currency.upper())
            break
    return currencies


def _money_target_currency(facts: TurtleFacts) -> str:
    currencies = _money_fact_currencies(facts)
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


def _buyback_input(
    buyback: float | None,
    missing_buyback: list[str],
    caveats: list[str],
) -> tuple[float, list[str], bool]:
    if missing_buyback:
        _append_caveat(caveats, "buyback_amount missing; treated as 0 for degraded calculation")
        return 0.0, missing_buyback, True
    if buyback is None:
        _append_caveat(caveats, "buyback_amount missing; treated as 0 for degraded calculation")
        return 0.0, ["buyback_amount"], True
    return buyback, [], False


def compute_turtle_signals(facts: TurtleFacts) -> TurtleComputedSignals:
    results: dict[str, FormulaResult] = {}
    caveats = _combined_caveats(facts)
    target_currency = _money_target_currency(facts)
    money_unit = f"hundred_million {target_currency}"

    if facts.status == "unsupported":
        return TurtleComputedSignals(status="unsupported", results=results, caveats=caveats)

    net_profit, net_profit_sources, missing_net_profit = _money_hm(facts, "net_profit", caveats, target_currency)
    ocf, ocf_sources, missing_ocf = _money_hm(facts, "operating_cash_flow", caveats, target_currency)
    capex, capex_sources, missing_capex = _money_hm(facts, "capex", caveats, target_currency)
    market_cap, market_cap_sources, missing_market_cap = _money_hm(facts, "market_cap", caveats, target_currency)
    market_cap, missing_market_cap = _validate_positive_market_cap(market_cap, missing_market_cap, caveats)
    buyback, buyback_sources, missing_buyback = _money_hm(facts, "buyback_amount", caveats, target_currency)
    buyback_for_formula, degraded_buyback_missing, buyback_degraded = _buyback_input(buyback, missing_buyback, caveats)
    cash, cash_sources, missing_cash = _money_hm(facts, "cash", caveats, target_currency)
    debt, debt_sources, missing_debt = _money_hm(facts, "interest_bearing_debt", caveats, target_currency)
    payout, payout_sources, missing_payout = _number_alias(
        facts,
        caveats,
        "avg_payout_ratio_3y",
        "dividend_avg_payout_ratio_3y",
    )
    tax_rate, tax_sources, missing_tax = _number(facts, "tax_rate", caveats)

    results["payout_anchor"] = _result(
        name="payout_anchor",
        formula="payout_anchor = avg_payout_ratio_3y",
        substitution=(
            "payout_anchor = avg_payout_ratio_3y"
            if payout is None
            else f"payout_anchor = {_fmt(payout)}"
        ),
        value=payout,
        unit="ratio",
        sources=payout_sources,
        missing_inputs=missing_payout,
        status="non_decisionable" if missing_payout else "complete",
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
        unit=money_unit,
        sources=owner_sources,
        missing_inputs=owner_missing,
        status="non_decisionable" if owner_missing else "complete",
    )

    r_critical_missing = _merge_missing(missing_net_profit, missing_payout, missing_tax, missing_market_cap)
    r_missing = _merge_missing(r_critical_missing, degraded_buyback_missing)
    r_sources = _merge_sources(net_profit_sources, payout_sources, tax_sources, buyback_sources, market_cap_sources)
    r_value = None
    r_substitution = "(net_profit * M * (1 - Q) + buyback) / market_cap * 100"
    if not r_critical_missing and market_cap != 0:
        r_value = (net_profit * payout * (1 - tax_rate) + buyback_for_formula) / market_cap * 100  # type: ignore[operator]
        r_substitution = (
            f"({_fmt(net_profit)} * {_fmt(payout)} * (1 - {_fmt(tax_rate)}) "
            f"+ {_fmt(buyback_for_formula)}) / {_fmt(market_cap)} * 100"
        )
    elif not r_critical_missing:
        r_critical_missing = ["market_cap"]
        r_missing = _merge_missing(r_critical_missing, degraded_buyback_missing)
    r_status: TurtleStatus = "non_decisionable" if r_critical_missing else "degraded" if buyback_degraded else "complete"
    results["R"] = _result(
        name="R",
        formula="R = (net_profit * M * (1 - Q) + buyback) / market_cap * 100",
        substitution=r_substitution,
        value=r_value,
        unit="percent",
        sources=r_sources,
        missing_inputs=r_missing,
        status=r_status,
    )

    gg_critical_missing = _merge_missing(owner_missing, missing_payout, missing_tax, missing_market_cap)
    gg_missing = _merge_missing(gg_critical_missing, degraded_buyback_missing)
    gg_sources = _merge_sources(owner_sources, payout_sources, tax_sources, buyback_sources, market_cap_sources)
    gg_value = None
    gg_substitution = "(owner_earnings * M * (1 - Q) + buyback) / market_cap * 100"
    if not gg_critical_missing and market_cap != 0:
        gg_value = (owner_earnings * payout * (1 - tax_rate) + buyback_for_formula) / market_cap * 100  # type: ignore[operator]
        gg_substitution = (
            f"({_fmt(owner_earnings)} * {_fmt(payout)} * (1 - {_fmt(tax_rate)}) "
            f"+ {_fmt(buyback_for_formula)}) / {_fmt(market_cap)} * 100"
        )
    elif not gg_critical_missing:
        gg_critical_missing = ["market_cap"]
        gg_missing = _merge_missing(gg_critical_missing, degraded_buyback_missing)
    gg_status: TurtleStatus = "non_decisionable" if gg_critical_missing else "degraded" if buyback_degraded else "complete"
    results["GG"] = _result(
        name="GG",
        formula="GG = (owner_earnings * M * (1 - Q) + buyback) / market_cap * 100",
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

    net_cash_missing = _merge_missing(missing_cash, missing_debt, missing_market_cap)
    net_cash_sources = _merge_sources(cash_sources, debt_sources, market_cap_sources)
    net_cash_ratio = None
    net_cash_substitution = "(cash - interest_bearing_debt) / market_cap * 100"
    if not net_cash_missing and market_cap != 0:
        net_cash_ratio = (cash - debt) / market_cap * 100  # type: ignore[operator]
        net_cash_substitution = f"({_fmt(cash)} - {_fmt(debt)}) / {_fmt(market_cap)} * 100"
    elif not net_cash_missing:
        net_cash_missing = ["market_cap"]
    net_cash_status: TurtleStatus = (
        "non_decisionable"
        if "market_cap" in net_cash_missing
        else "degraded"
        if net_cash_missing
        else "complete"
    )
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
    ev_status: TurtleStatus = (
        "non_decisionable"
        if "market_cap" in ev_missing
        else "degraded"
        if ev_missing
        else "complete"
    )
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
    protection_status: TurtleStatus = (
        "non_decisionable"
        if "market_cap" in protection_missing
        else "degraded"
        if protection_missing
        else "complete"
    )
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

    critical_non_decisionable = results["R"].status == "non_decisionable" or results["GG"].status == "non_decisionable"
    if critical_non_decisionable or facts.status == "non_decisionable":
        status: TurtleStatus = "non_decisionable"
    elif facts.status == "degraded" or caveats or any(result.status == "degraded" for result in results.values()):
        status = "degraded"
    else:
        status = "complete"

    return TurtleComputedSignals(status=status, results=results, caveats=caveats)
