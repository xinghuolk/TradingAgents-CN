from collections.abc import Sequence
from dataclasses import fields, replace
from datetime import date
from decimal import Decimal

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import (
    Holding,
    ParseWarning,
    PortfolioView,
    ReconciledPortfolio,
    SecurityId,
    SnapshotAnchor,
    SnapshotPosition,
)


def select_snapshot(snapshots: Sequence[SnapshotAnchor], as_of: date) -> SnapshotAnchor:
    full = [anchor for anchor in snapshots if anchor.full_snapshot]
    earlier = [anchor for anchor in full if anchor.observed_on <= as_of]
    if earlier:
        return max(
            earlier, key=lambda anchor: (anchor.observed_on, anchor.import_sequence)
        )
    if full:
        earliest = min(anchor.observed_on for anchor in full)
        return max(
            (anchor for anchor in full if anchor.observed_on == earliest),
            key=lambda anchor: anchor.import_sequence,
        )
    partial = [anchor for anchor in snapshots if anchor.observed_on == as_of]
    if partial:
        return max(partial, key=lambda anchor: anchor.import_sequence)
    raise PortfolioError("NO_FULL_SNAPSHOT", "no full portfolio snapshot")


def _snapshot_holding(position: SnapshotPosition) -> Holding:
    return Holding(
        **{
            field.name: getattr(position, field.name)
            for field in fields(SnapshotPosition)
        }
    )


def _quantity_holding(
    security: SecurityId, broker_name: str, route: str, quantity: Decimal
) -> Holding:
    values = dict.fromkeys(field.name for field in fields(Holding))
    values.update(
        security=security, broker_name=broker_name, route=route, quantity=quantity
    )
    return Holding(**values)


def build_portfolio_view(portfolio: ReconciledPortfolio, as_of: date) -> PortfolioView:
    if type(as_of) is not date:
        raise TypeError("as_of must be a date")
    anchor = select_snapshot(portfolio.snapshots, as_of)
    holdings = {
        str(position.security): _snapshot_holding(position)
        for position in anchor.positions
    }
    if anchor.observed_on == as_of:
        direction = "exact" if anchor.full_snapshot else "partial_snapshot"
        completeness = "authoritative" if anchor.full_snapshot else "incomplete"
        warnings = tuple(
            warning
            for warning in portfolio.warnings
            if (warning.impact_from is None or warning.impact_from <= as_of)
            and (warning.impact_through is None or warning.impact_through >= as_of)
        )
        if not anchor.full_snapshot:
            warnings += (
                ParseWarning(
                    "partial_snapshot",
                    anchor.import_id,
                    None,
                    as_of,
                    as_of,
                    "partial snapshot supplies only known positions; absent securities are unknown",
                    True,
                ),
            )
    else:
        direction = "forward" if anchor.observed_on < as_of else "reverse"
        sign = 1 if direction == "forward" else -1
        low, high = sorted((anchor.observed_on, as_of))
        holdings = {
            key: _quantity_holding(h.security, h.broker_name, h.route, h.quantity)
            for key, h in holdings.items()
        }
        for event in portfolio.events:
            if event.security is None:
                continue
            delta = sum(
                (
                    posting.amount
                    for posting in event.postings
                    if posting.role == "security"
                    and low < posting.effective_date <= high
                ),
                Decimal(0),
            )
            if not delta:
                continue
            key = str(event.security)
            if key not in holdings:
                details = dict(event.details)
                holdings[key] = _quantity_holding(
                    event.security,
                    details.get("broker_name") or key,
                    details.get("route") or event.security.market,
                    Decimal(0),
                )
            holdings[key] = replace(
                holdings[key], quantity=holdings[key].quantity + sign * delta
            )

        warnings = tuple(
            warning
            for warning in portfolio.warnings
            if (warning.impact_through is None or warning.impact_through > low)
            and (warning.impact_from is None or warning.impact_from <= high)
        )
        # Inclusive coverage must cover only days after the authoritative anchor boundary.
        covered = any(
            start.toordinal() <= low.toordinal() + 1 and end >= high
            for start, end in portfolio.reported_coverage
        )
        completeness = (
            "reported_coverage"
            if covered and not any(warning.affects_quantity for warning in warnings)
            else "incomplete"
        )
        warnings += (
            ParseWarning(
                "reported_coverage",
                None,
                None,
                low,
                high,
                "reported date bounds cannot prove the delivery export was unfiltered",
                False,
            ),
        )
        if not covered:
            warnings += (
                ParseWarning(
                    "coverage_gap",
                    None,
                    None,
                    low,
                    high,
                    "reported delivery intervals do not cover all reconstruction dates",
                    True,
                ),
            )

    return PortfolioView(
        as_of=as_of,
        anchor_date=anchor.observed_on,
        direction=direction,
        holdings=tuple(
            holdings[key] for key in sorted(holdings) if holdings[key].quantity
        ),
        reported_coverage=portfolio.reported_coverage,
        completeness=completeness,
        warnings=warnings,
    )
