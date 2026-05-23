"""Deterministic Turtle v0.15 calculation layer."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .facts import FormulaResult, MoneyAmount, TurtleComputedSignals, TurtleFactValue, TurtleFacts, TurtleStatus, normalize_currency


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


def _money_fact_currencies(facts: TurtleFacts, names: Iterable[str]) -> set[str]:
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
            try:
                amount = fact.value.to_hundred_million(target_currency=fact.value.currency, fx_rates={})
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(amount.value):
                continue
            currencies.add(normalize_currency(fact.value.currency))
            break
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

        available_values.append(float(amount.value))
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
    has_input_caveats = bool(caveats)
    r_target_currency = _money_target_currency(facts, ("net_profit", "market_cap"))
    owner_target_currency = _money_target_currency(facts, ("operating_cash_flow", "capex", "market_cap"))
    net_cash_target_currency = _money_target_currency(facts, ("cash", "interest_bearing_debt", "market_cap"))
    owner_money_unit = f"hundred_million {owner_target_currency}"

    if facts.status == "unsupported":
        return TurtleComputedSignals(status="unsupported", results=results, caveats=caveats)

    net_profit, net_profit_sources, missing_net_profit = _money_hm(facts, "net_profit", caveats, r_target_currency)
    r_market_cap, r_market_cap_sources, missing_r_market_cap = _money_hm(facts, "market_cap", caveats, r_target_currency)
    r_market_cap, missing_r_market_cap = _validate_positive_market_cap(r_market_cap, missing_r_market_cap, caveats)
    r_buyback, r_buyback_sources, missing_r_buyback = _money_hm(facts, "buyback_amount", caveats, r_target_currency)
    r_buyback_for_formula, r_degraded_buyback_missing, r_buyback_degraded = _buyback_input(
        r_buyback,
        missing_r_buyback,
        caveats,
    )
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
    gg_buyback, gg_buyback_sources, missing_gg_buyback = _money_hm(facts, "buyback_amount", caveats, owner_target_currency)
    gg_buyback_for_formula, gg_degraded_buyback_missing, gg_buyback_degraded = _buyback_input(
        gg_buyback,
        missing_gg_buyback,
        caveats,
    )
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
        unit=owner_money_unit,
        sources=owner_sources,
        missing_inputs=owner_missing,
        status="non_decisionable" if owner_missing else "complete",
    )

    r_critical_missing = _merge_missing(missing_net_profit, missing_payout, missing_tax, missing_r_market_cap)
    r_missing = _merge_missing(r_critical_missing, r_degraded_buyback_missing)
    r_sources = _merge_sources(net_profit_sources, payout_sources, tax_sources, r_buyback_sources, r_market_cap_sources)
    r_value = None
    r_substitution = "(net_profit * M * (1 - Q) + buyback) / market_cap * 100"
    if not r_critical_missing:
        r_value = (net_profit * payout * (1 - tax_rate) + r_buyback_for_formula) / r_market_cap * 100  # type: ignore[operator]
        r_substitution = (
            f"({_fmt(net_profit)} * {_fmt(payout)} * (1 - {_fmt(tax_rate)}) "
            f"+ {_fmt(r_buyback_for_formula)}) / {_fmt(r_market_cap)} * 100"
        )
    r_status: TurtleStatus = "non_decisionable" if r_critical_missing else "degraded" if r_buyback_degraded else "complete"
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

    gg_critical_missing = _merge_missing(owner_missing, missing_payout, missing_tax, missing_gg_market_cap)
    gg_missing = _merge_missing(gg_critical_missing, gg_degraded_buyback_missing)
    gg_sources = _merge_sources(owner_sources, payout_sources, tax_sources, gg_buyback_sources, gg_market_cap_sources)
    gg_value = None
    gg_substitution = "(owner_earnings * M * (1 - Q) + buyback) / market_cap * 100"
    if not gg_critical_missing:
        gg_value = (owner_earnings * payout * (1 - tax_rate) + gg_buyback_for_formula) / gg_market_cap * 100  # type: ignore[operator]
        gg_substitution = (
            f"({_fmt(owner_earnings)} * {_fmt(payout)} * (1 - {_fmt(tax_rate)}) "
            f"+ {_fmt(gg_buyback_for_formula)}) / {_fmt(gg_market_cap)} * 100"
        )
    gg_status: TurtleStatus = "non_decisionable" if gg_critical_missing else "degraded" if gg_buyback_degraded else "complete"
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

    critical_non_decisionable = results["R"].status == "non_decisionable" or results["GG"].status == "non_decisionable"
    if critical_non_decisionable or facts.status == "non_decisionable":
        status: TurtleStatus = "non_decisionable"
    elif (
        facts.status == "degraded"
        or has_input_caveats
        or any(result.status in {"degraded", "non_decisionable"} for result in results.values())
    ):
        status = "degraded"
    else:
        status = "complete"

    return TurtleComputedSignals(status=status, results=results, caveats=caveats)
