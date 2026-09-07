from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from hashlib import sha256

from app.services.real_portfolio.events import build_delivery_events
from app.services.real_portfolio.models import (
    DeliveryObservation,
    ImportedFacts,
    ParseWarning,
    ReconciledPortfolio,
    SnapshotAnchor,
)


def namespace_event_id(user_id: str, account_alias: str, event_id: str) -> str:
    value = f"real-portfolio-event-v1\0{user_id}\0{account_alias}\0{event_id}"
    return sha256(value.encode("utf-8")).hexdigest()


def _warning_key(warning: ParseWarning) -> tuple:
    return (
        warning.warning_type,
        warning.import_id or "",
        warning.line_number if warning.line_number is not None else -1,
        warning.impact_from or date.min,
        warning.impact_through or date.max,
        warning.message,
        warning.affects_quantity,
    )


def _merge_coverage(
    intervals: Sequence[tuple[date, date]],
) -> tuple[tuple[date, date], ...]:
    merged: list[tuple[date, date]] = []
    for start, end in sorted(intervals):
        if merged and start.toordinal() <= merged[-1][1].toordinal() + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def reconcile_imports(
    *, user_id: str, account_alias: str, imports: Sequence[ImportedFacts]
) -> ReconciledPortfolio:
    by_fact: dict[str, list[tuple[int, str, DeliveryObservation]]] = defaultdict(list)
    snapshots: dict[str, SnapshotAnchor] = {}
    warnings: set[ParseWarning] = set()
    coverage: list[tuple[date, date]] = []

    for imported in imports:
        parsed = imported.parsed
        warnings.update(
            replace(warning, import_id=imported.import_id)
            for warning in parsed.warnings
        )
        if parsed.source_type == "snapshot":
            if parsed.observed_on is None:
                raise ValueError("snapshot must have an observed date")
            identity = (
                f"real-portfolio-snapshot-v1\0{user_id}\0{account_alias}"
                f"\0{imported.import_id}\0{parsed.observed_on}\0{parsed.file_sha256}"
            )
            snapshot_id = sha256(identity.encode("utf-8")).hexdigest()
            snapshots[snapshot_id] = SnapshotAnchor(
                snapshot_id=snapshot_id,
                import_id=imported.import_id,
                import_sequence=imported.import_sequence,
                observed_on=parsed.observed_on,
                full_snapshot=parsed.full_snapshot,
                positions=tuple(
                    sorted(parsed.snapshot_positions, key=lambda p: str(p.security))
                ),
            )
            continue

        if parsed.coverage_from is not None and parsed.coverage_through is not None:
            coverage.append((parsed.coverage_from, parsed.coverage_through))
        rows = {row.line_number: row for row in parsed.rows}
        for observation in parsed.delivery_observations:
            observation = replace(
                observation,
                evidence=replace(observation.evidence, import_id=imported.import_id),
            )
            row_hash = rows[observation.evidence.line_number].row_sha256
            by_fact[observation.evidence.fact_key].append(
                (imported.import_sequence, row_hash, observation)
            )

    ordered_snapshots = tuple(
        sorted(
            snapshots.values(),
            key=lambda anchor: (
                anchor.observed_on,
                anchor.import_sequence,
                anchor.snapshot_id,
            ),
        )
    )
    previous_full: dict[date, SnapshotAnchor] = {}
    for anchor in ordered_snapshots:
        if not anchor.full_snapshot:
            continue
        previous = previous_full.get(anchor.observed_on)
        if previous is not None and previous.positions != anchor.positions:
            warnings.add(
                ParseWarning(
                    "snapshot_replaced",
                    anchor.import_id,
                    None,
                    anchor.observed_on,
                    anchor.observed_on,
                    "later full snapshot replaces differing full snapshot on the same date",
                    False,
                )
            )
        previous_full[anchor.observed_on] = anchor

    current: list[DeliveryObservation] = []
    replacements: dict[str, ParseWarning] = {}
    for fact_key, versions in sorted(by_fact.items()):
        _, _, winner = max(
            versions,
            key=lambda item: (item[0], item[2].evidence.line_number),
        )
        # Only the selected content can generate postings; all versions remain evidence.
        current.append(winner)
        if len({row_hash for _, row_hash, _ in versions}) > 1:
            dates = [obs.trade_date for _, _, obs in versions]
            replacements[fact_key] = ParseWarning(
                "source_fact_replaced",
                winner.evidence.import_id,
                winner.evidence.line_number,
                min(dates),
                max(dates),
                "later source fact replaces conflicting earlier evidence",
                True,
            )

    built = build_delivery_events(current)
    events = []
    for event in built.events:
        fact_keys = {ref.fact_key for ref in event.evidence}
        evidence_roles = {(ref.fact_key, ref.role) for ref in event.evidence}
        evidence = {
            replace(obs.evidence, role=role)
            for fact_key, role in evidence_roles
            for _, _, obs in by_fact[fact_key]
        }
        event_warnings = tuple(
            sorted(
                set(event.warnings)
                | {replacements[key] for key in fact_keys if key in replacements},
                key=_warning_key,
            )
        )
        events.append(
            replace(
                event,
                event_id=namespace_event_id(user_id, account_alias, event.event_id),
                evidence=tuple(
                    sorted(
                        evidence,
                        key=lambda ref: (
                            ref.import_id or "",
                            ref.line_number,
                            ref.fact_key,
                            ref.role,
                        ),
                    )
                ),
                warnings=event_warnings,
            )
        )
        warnings.update(event_warnings)

    return ReconciledPortfolio(
        snapshots=ordered_snapshots,
        events=tuple(sorted(events, key=lambda event: event.event_id)),
        warnings=tuple(sorted(warnings, key=_warning_key)),
        reported_coverage=_merge_coverage(coverage),
        import_ids=tuple(sorted({imported.import_id for imported in imports})),
    )
