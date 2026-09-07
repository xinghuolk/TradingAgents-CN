import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from hashlib import sha256

from app.services.real_portfolio.delivery import delivery_observation_to_document
from app.services.real_portfolio.models import (
    DeliveryObservation,
    EventBuildResult,
    EvidenceRef,
    ParseWarning,
    PortfolioEvent,
    Posting,
    SecurityId,
    canonical_decimal_string,
)

_SIDES = {"证券买入": "buy", "证券卖出": "sell"}

# Time and gross amount distinguish fills. Fee and currency differences conflict.
_HK_FILL_FIELDS = (
    "contract_fingerprint",
    "route",
    "security",
    "operation",
    "quantity",
    "price",
    "gross_amount",
    "trade_time",
)
_HK_MATCH_FIELDS = _HK_FILL_FIELDS + (
    "commission",
    "stamp_tax",
    "misc_fee",
    "transfer_fee",
    "hk_trading_fee",
    "trade_currency",
    "settlement_currency",
    "currency",
)


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _scalar(value: object) -> object:
    if isinstance(value, Decimal):
        return canonical_decimal_string(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, SecurityId):
        return str(value)
    return value


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _group_key(
    observation: DeliveryObservation, names: tuple[str, ...]
) -> tuple[object, ...]:
    return tuple(_scalar(getattr(observation, name)) for name in names)


def _observation_key(observation: DeliveryObservation) -> str:
    document = delivery_observation_to_document(observation)
    document.pop("evidence")
    return _json(document)


def _deduplication_key(observation: DeliveryObservation) -> str:
    return _json((observation.evidence.fact_key, _observation_key(observation)))


def _evidence_key(evidence: EvidenceRef) -> tuple[bool, str, int, str, str]:
    return (
        evidence.import_id is not None,
        evidence.import_id or "",
        evidence.line_number,
        evidence.fact_key,
        evidence.role,
    )


def _deduplicate_observations(
    observations: Sequence[DeliveryObservation],
) -> tuple[
    list[DeliveryObservation],
    dict[str, list[DeliveryObservation]],
    dict[tuple[str | None, int, str], str],
]:
    versions_by_body: dict[str, list[DeliveryObservation]] = defaultdict(list)
    for observation in observations:
        versions_by_body[_deduplication_key(observation)].append(observation)

    current: list[DeliveryObservation] = []
    body_by_evidence: dict[tuple[str | None, int, str], str] = {}
    for body_key, versions in versions_by_body.items():
        representative = max(versions, key=lambda item: _evidence_key(item.evidence))
        current.append(representative)
        body_by_evidence[
            (
                representative.evidence.import_id,
                representative.evidence.line_number,
                representative.evidence.fact_key,
            )
        ] = body_key
    current.sort(
        key=lambda item: (_observation_key(item), _evidence_key(item.evidence))
    )
    return current, versions_by_body, body_by_evidence


def _expand_event_evidence(
    event: PortfolioEvent,
    versions_by_body: dict[str, list[DeliveryObservation]],
    body_by_evidence: dict[tuple[str | None, int, str], str],
) -> PortfolioEvent:
    evidence: set[EvidenceRef] = set()
    for ref in event.evidence:
        body_key = body_by_evidence[(ref.import_id, ref.line_number, ref.fact_key)]
        evidence.update(
            replace(observation.evidence, role=ref.role)
            for observation in versions_by_body[body_key]
        )
    return replace(event, evidence=tuple(sorted(evidence, key=_evidence_key)))


def _warning(observation: DeliveryObservation, kind: str) -> ParseWarning:
    return ParseWarning(
        warning_type=kind,
        import_id=observation.evidence.import_id,
        line_number=observation.evidence.line_number,
        impact_from=None if kind == "missing_execution" else observation.trade_date,
        impact_through=observation.trade_date,
        message=kind.replace("_", " "),
        affects_quantity=kind != "missing_settlement",
    )


def _cash(observation: DeliveryObservation) -> Posting:
    return Posting(
        role="cash",
        effective_date=observation.trade_date,
        amount=observation.cash_movement,
        unit="currency",
        currency="CNY",
        source_field="发生金额",
    )


def _security(observation: DeliveryObservation) -> Posting:
    quantity = observation.quantity.copy_abs()
    if _SIDES[observation.operation] == "sell":
        quantity = quantity.copy_negate()
    return Posting(
        role="security",
        effective_date=observation.trade_date,
        amount=quantity,
        unit="shares",
        currency=None,
        source_field="成交数量",
    )


def _positive_security(observation: DeliveryObservation) -> Posting:
    return Posting(
        role="security",
        effective_date=observation.trade_date,
        amount=observation.quantity.copy_abs(),
        unit="shares",
        currency=None,
        source_field="成交数量",
    )


def _event(
    observation: DeliveryObservation,
    event_type: str,
    completeness: str,
    postings: tuple[Posting, ...] = (),
    *,
    event_id: str | None = None,
    trade_date: date | None = None,
    settlement_date: date | None = None,
    evidence: tuple[EvidenceRef, ...] | None = None,
    warnings: tuple[ParseWarning, ...] = (),
) -> PortfolioEvent:
    return PortfolioEvent(
        event_id=event_id or observation.evidence.fact_key,
        event_type=event_type,
        security=observation.security,
        trade_date=trade_date,
        settlement_date=settlement_date,
        completeness=completeness,
        postings=postings,
        evidence=evidence or (observation.evidence,),
        warnings=warnings,
        details=tuple(
            sorted(
                (
                    ("broker_name", observation.broker_name or ""),
                    ("operation", observation.operation),
                    ("route", observation.route or ""),
                    ("trade_currency", observation.trade_currency or ""),
                    *(("warning", warning.warning_type) for warning in warnings),
                )
            )
        ),
    )


def _single_trade_event(observation: DeliveryObservation) -> PortfolioEvent:
    code = observation.security.code if observation.security else ""
    repo_like = code.startswith(("204", "1318")) or (
        observation.broker_name or ""
    ).startswith("GC")
    if (
        code == "204001"
        and observation.broker_name == "GC001"
        and observation.operation == "证券卖出"
        and observation.cash_movement
    ):
        phase = "repo_open" if observation.cash_movement < 0 else "repo_close"
        event_id = (
            _hash(
                (
                    "repo",
                    observation.contract_fingerprint,
                    observation.trade_date.isoformat(),
                    phase,
                )
            )
            if observation.contract_fingerprint
            else observation.evidence.fact_key
        )
        return _event(
            observation,
            phase,
            "complete",
            (_cash(observation),),
            event_id=event_id,
            trade_date=observation.trade_date,
            settlement_date=observation.trade_date,
        )
    if (
        not repo_like
        and observation.operation == "证券买入"
        and not any(
            (
                observation.quantity,
                observation.price,
                observation.gross_amount,
                observation.cash_movement,
            )
        )
    ):
        return _event(
            observation,
            "subscription_notice",
            "informational",
            trade_date=observation.trade_date,
        )
    if not repo_like and observation.quantity and observation.security:
        return _event(
            observation,
            "trade",
            "complete",
            (_security(observation), _cash(observation)),
            trade_date=observation.trade_date,
            settlement_date=observation.trade_date,
        )
    return _event(
        observation,
        "unclassified",
        "unclassified",
        trade_date=observation.trade_date,
        warnings=(_warning(observation, "unclassified"),),
    )


def _hk_event(
    group: list[DeliveryObservation],
    ambiguous: bool,
    candidates: list[DeliveryObservation],
) -> PortfolioEvent:
    observation = group[0]
    event_id = (
        _hash(("hk_trade", _group_key(observation, _HK_MATCH_FIELDS)))
        if observation.contract_fingerprint
        else observation.evidence.fact_key
    )
    executions = [item for item in group if not item.cash_movement]
    settlements = [item for item in group if item.cash_movement]
    evidence = tuple(
        replace(
            item.evidence,
            role="execution" if not item.cash_movement else "settlement",
        )
        for item in group
    )
    if (
        not ambiguous
        and len(executions) == len(settlements) == 1
        and executions[0].trade_date < settlements[0].trade_date
    ):
        execution, settlement = executions[0], settlements[0]
        return _event(
            observation,
            "trade",
            "complete",
            (_security(execution), _cash(settlement)),
            event_id=event_id,
            trade_date=execution.trade_date,
            settlement_date=settlement.trade_date,
            evidence=evidence,
        )
    if ambiguous or len(group) > 1:
        candidate_dates = [item.trade_date for item in candidates]
        impact_from = (
            None
            if any(item.cash_movement for item in candidates)
            else min(candidate_dates)
        )
        return _event(
            observation,
            "trade",
            "partial",
            tuple(_cash(item) for item in settlements),
            event_id=event_id,
            evidence=evidence,
            warnings=tuple(
                replace(
                    _warning(item, "ambiguous_pair"),
                    impact_from=impact_from,
                    impact_through=max(candidate_dates),
                )
                for item in group
            ),
        )

    execution_only = not observation.cash_movement
    return _event(
        observation,
        "trade",
        "partial",
        (_security(observation) if execution_only else _cash(observation),),
        event_id=event_id,
        trade_date=observation.trade_date if execution_only else None,
        settlement_date=None if execution_only else observation.trade_date,
        evidence=evidence,
        warnings=(
            _warning(
                observation,
                "missing_settlement" if execution_only else "missing_execution",
            ),
        ),
    )


def build_trade_events(
    observations: Sequence[DeliveryObservation],
) -> EventBuildResult:
    trade_observations = tuple(
        observation
        for observation in observations
        if observation.operation in _SIDES
    )
    current, versions_by_body, body_by_evidence = _deduplicate_observations(
        trade_observations
    )
    hk_groups: dict[str, list[DeliveryObservation]] = defaultdict(list)
    fill_groups: dict[tuple[object, ...], set[str]] = defaultdict(set)
    events: list[PortfolioEvent] = []
    repo_groups: dict[
        str, list[tuple[DeliveryObservation, PortfolioEvent]]
    ] = defaultdict(list)

    for observation in current:
        if (
            observation.security
            and observation.security.market == "HK"
            and observation.quantity
        ):
            key = (
                _hash(_group_key(observation, _HK_MATCH_FIELDS))
                if observation.contract_fingerprint
                else observation.evidence.fact_key
            )
            hk_groups[key].append(observation)
            if observation.contract_fingerprint:
                fill_groups[_group_key(observation, _HK_FILL_FIELDS)].add(key)
        else:
            event = _single_trade_event(observation)
            if event.event_type in ("repo_open", "repo_close"):
                repo_groups[event.event_id].append((observation, event))
            else:
                events.append(event)

    for group in repo_groups.values():
        # A phase is one economic event. Conflicting observations cannot be summed.
        observation, winner = max(
            group,
            key=lambda item: (
                _observation_key(item[0]),
                _evidence_key(item[0].evidence),
            ),
        )
        bodies = []
        for candidate, _ in group:
            body = delivery_observation_to_document(candidate)
            body.pop("evidence")
            body.pop("transaction_fingerprint")
            bodies.append(_json(body))
        warnings = ()
        if len(set(bodies)) > 1:
            warnings = (
                replace(
                    _warning(observation, "repo_phase_conflict"),
                    affects_quantity=False,
                    message="conflicting reverse repo observations; one deterministic value selected",
                ),
            )
        events.append(
            replace(
                winner,
                evidence=tuple(item.evidence for item, _ in group),
                warnings=warnings,
            )
        )

    for _, group in sorted(hk_groups.items()):
        group.sort(
            key=lambda item: (_observation_key(item), _evidence_key(item.evidence))
        )
        related_keys = fill_groups.get(_group_key(group[0], _HK_FILL_FIELDS), ())
        ambiguous = len(related_keys) > 1
        candidates = (
            [item for key in sorted(related_keys) for item in hk_groups[key]]
            if ambiguous
            else group
        )
        events.append(_hk_event(group, ambiguous, candidates))

    rebuilt = tuple(
        sorted(
            (
                _expand_event_evidence(
                    event,
                    versions_by_body,
                    body_by_evidence,
                )
                for event in events
            ),
            key=lambda item: item.event_id,
        )
    )
    return EventBuildResult(
        events=rebuilt,
        warnings=tuple(warning for event in rebuilt for warning in event.warnings),
    )


def _cash_operation(
    observation: DeliveryObservation, event_type: str
) -> PortfolioEvent:
    return _event(
        observation,
        event_type,
        "complete",
        (_cash(observation),),
        trade_date=observation.trade_date,
        settlement_date=observation.trade_date,
    )


def _security_operation(
    observation: DeliveryObservation, event_type: str
) -> PortfolioEvent:
    return _event(
        observation,
        event_type,
        "complete",
        (_positive_security(observation),),
        trade_date=observation.trade_date,
        settlement_date=observation.trade_date,
    )


def _informational_operation(
    observation: DeliveryObservation, event_type: str
) -> PortfolioEvent:
    return _event(
        observation,
        event_type,
        "informational",
        trade_date=observation.trade_date,
    )


def _bond_subscription_payment(
    observation: DeliveryObservation, event_type: str
) -> PortfolioEvent:
    return _event(
        observation,
        event_type,
        "complete",
        (_positive_security(observation), _cash(observation)),
        trade_date=observation.trade_date,
        settlement_date=observation.trade_date,
    )


_DELIVERY_OPERATION_HANDLERS = {
    "沪港通港股证券组合费": (_cash_operation, "portfolio_fee"),
    "深港通港股证券组合费": (_cash_operation, "portfolio_fee"),
    "银行转证券": (_cash_operation, "bank_transfer"),
    "上海A股红利税补缴": (_cash_operation, "dividend_tax"),
    "上海A股红利入账": (_cash_operation, "cash_dividend"),
    "沪港通港股红利入账": (_cash_operation, "cash_dividend"),
    "深港通港股红利入账": (_cash_operation, "cash_dividend"),
    "利息归本": (_cash_operation, "cash_interest"),
    "上海A股红股上市入账": (_security_operation, "stock_dividend"),
    "深圳A股股份特殊调账调入": (
        _security_operation,
        "security_adjustment_in",
    ),
    "上海网上新债中签": (_informational_operation, "bond_award_notice"),
    "上海网上新债缴款": (
        _bond_subscription_payment,
        "bond_subscription_payment",
    ),
    "上海网上新债缴款确认待上市股份入": (
        _informational_operation,
        "bond_pending_listing",
    ),
    "上海新债上市": (_informational_operation, "bond_listing"),
}


def _delivery_operation_event(
    observation: DeliveryObservation,
) -> PortfolioEvent:
    handler = _DELIVERY_OPERATION_HANDLERS.get(observation.operation)
    if handler is None:
        return _event(
            observation,
            "unclassified",
            "unclassified",
            trade_date=observation.trade_date,
            warnings=(_warning(observation, "unclassified"),),
        )
    build, event_type = handler
    return build(observation, event_type)


def build_operation_events(
    observations: Sequence[DeliveryObservation],
) -> EventBuildResult:
    current, versions_by_body, body_by_evidence = _deduplicate_observations(
        observations
    )
    events = tuple(
        sorted(
            (
                _expand_event_evidence(
                    _delivery_operation_event(observation),
                    versions_by_body,
                    body_by_evidence,
                )
                for observation in current
            ),
            key=lambda item: item.event_id,
        )
    )
    return EventBuildResult(
        events=events,
        warnings=tuple(warning for event in events for warning in event.warnings),
    )


def build_delivery_events(
    observations: Sequence[DeliveryObservation],
) -> EventBuildResult:
    trade_result = build_trade_events(observations)
    trade_evidence = {
        (ref.import_id, ref.line_number, ref.fact_key)
        for event in trade_result.events
        for ref in event.evidence
    }
    remaining = tuple(
        observation
        for observation in observations
        if (
            observation.evidence.import_id,
            observation.evidence.line_number,
            observation.evidence.fact_key,
        )
        not in trade_evidence
    )
    operation_result = build_operation_events(remaining)
    events = tuple(
        sorted(
            trade_result.events + operation_result.events,
            key=lambda item: item.event_id,
        )
    )
    warnings = tuple(warning for event in events for warning in event.warnings)
    return EventBuildResult(events=events, warnings=warnings)
