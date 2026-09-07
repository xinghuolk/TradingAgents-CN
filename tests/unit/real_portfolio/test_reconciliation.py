from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.services.real_portfolio.formats import parse_portfolio_file
from app.services.real_portfolio.models import ImportedFacts
from app.services.real_portfolio.reconciliation import (
    namespace_event_id,
    reconcile_imports,
)
from tests.unit.real_portfolio.fixtures import (
    DELIVERY_HEADER,
    delivery_bytes,
    delivery_row,
    hk_pair,
    replace_cells,
    snapshot_bytes,
    snapshot_row,
)


def imported_delivery(
    *, import_sequence: int, cash_movement: str = "-1001.00"
) -> ImportedFacts:
    row = delivery_row(**{"发生金额": cash_movement})
    return ImportedFacts(
        import_id=f"delivery-{import_sequence}",
        import_sequence=import_sequence,
        parsed=parse_portfolio_file(delivery_bytes(row)),
    )


def reconcile(*imports: ImportedFacts):
    return reconcile_imports(user_id="user-1", account_alias="main", imports=imports)


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_fact_winner_uses_sequence_not_input_or_import_id(reverse):
    older = replace(imported_delivery(import_sequence=1), import_id="z-older")
    newer = replace(
        imported_delivery(import_sequence=2, cash_movement="-1002"),
        import_id="a-newer",
    )
    inputs = (older, newer)
    result = reconcile(*(inputs[::-1] if reverse else inputs))
    assert result == reconcile(*inputs)
    assert len(result.events) == 1
    assert result.events[0].postings[1].amount == Decimal("-1002")
    warning = next(
        w for w in result.warnings if w.warning_type == "source_fact_replaced"
    )
    assert warning.import_id == "a-newer" and warning.line_number == 2
    assert warning.affects_quantity
    assert warning.impact_from == warning.impact_through == date(2026, 9, 1)
    assert "成交编号" not in warning.message and "合同编号" not in warning.message
    assert "-1002" not in warning.message


def test_later_line_breaks_fact_conflicts_within_one_import():
    parsed = parse_portfolio_file(
        delivery_bytes(
            delivery_row(**{"发生金额": "-1001"}),
            delivery_row(**{"发生金额": "-1003"}),
        )
    )
    result = reconcile(ImportedFacts("same-file", 8, parsed))
    assert len(result.events) == 1
    assert result.events[0].postings[1].amount == Decimal("-1003")
    warning = next(
        w for w in result.warnings if w.warning_type == "source_fact_replaced"
    )
    assert warning.line_number == 3


@pytest.mark.parametrize("reverse", [False, True])
def test_overlapping_normalized_equal_fact_keeps_evidence_once(reverse):
    first = imported_delivery(import_sequence=1, cash_movement="-1001")
    second = imported_delivery(import_sequence=2, cash_movement="-1001.000")
    inputs = (first, second, first)
    result = reconcile(*(inputs[::-1] if reverse else inputs))
    assert len(result.events) == 1
    assert {(ref.import_id, ref.line_number) for ref in result.events[0].evidence} == {
        ("delivery-1", 2),
        ("delivery-2", 2),
    }
    assert len(result.events[0].evidence) == 2
    assert result.import_ids == ("delivery-1", "delivery-2")
    assert not result.warnings


def test_different_fact_keys_with_equal_bodies_remain_distinct():
    parsed = parse_portfolio_file(
        delivery_bytes(
            delivery_row(**{"成交编号": "first-fill"}),
            delivery_row(**{"成交编号": "second-fill"}),
        )
    )
    result = reconcile(ImportedFacts("two-fills", 1, parsed))
    assert len(result.events) == 2
    assert len({event.event_id for event in result.events}) == 2
    assert sum(event.postings[0].amount for event in result.events) == Decimal("200")


@pytest.mark.parametrize("reverse", [False, True])
def test_hk_settlement_completes_across_imports_without_changing_identity(reverse):
    execution, settlement = hk_pair()
    # Execution and settlement are separate source facts sharing one contract.
    settlement = replace_cells(
        DELIVERY_HEADER, settlement, {"成交编号": "settlement-id"}
    )
    first = ImportedFacts(
        "execution", 1, parse_portfolio_file(delivery_bytes(execution))
    )
    later = ImportedFacts(
        "settlement", 2, parse_portfolio_file(delivery_bytes(settlement))
    )
    partial = reconcile(first)
    inputs = (first, later)
    complete = reconcile(*(inputs[::-1] if reverse else inputs))
    assert len(complete.events) == 1
    event = complete.events[0]
    assert event.event_id == partial.events[0].event_id
    assert event.completeness == "complete"
    assert str(event.security) == "HK:00700"
    assert [(p.role, p.effective_date, p.amount) for p in event.postings] == [
        ("security", date(2026, 9, 1), Decimal("100")),
        ("cash", date(2026, 9, 3), Decimal("-901.23")),
    ]
    assert {(ref.import_id, ref.role) for ref in event.evidence} == {
        ("execution", "execution"),
        ("settlement", "settlement"),
    }
    assert not complete.warnings


def test_event_ids_are_namespaced_by_user_and_account():
    inputs = (imported_delivery(import_sequence=1),)
    one = reconcile_imports(user_id="user-1", account_alias="main", imports=inputs)
    two = reconcile_imports(user_id="user-2", account_alias="main", imports=inputs)
    other_account = reconcile_imports(
        user_id="user-1", account_alias="other", imports=inputs
    )
    raw_id = inputs[0].parsed.rows[0].fact_key
    assert one.events[0].event_id != two.events[0].event_id
    assert (
        len(
            {
                one.events[0].event_id,
                two.events[0].event_id,
                other_account.events[0].event_id,
            }
        )
        == 3
    )
    assert one.events[0].event_id == namespace_event_id("user-1", "main", raw_id)
    assert namespace_event_id("user-1", "main", raw_id) != namespace_event_id(
        "user-1", "other", raw_id
    )
    assert namespace_event_id("ab", "c", raw_id) != namespace_event_id(
        "a", "bc", raw_id
    )
    assert len(one.events[0].event_id) == 64
    assert one.events[0].event_id != raw_id


@pytest.mark.parametrize("reverse", [False, True])
def test_same_date_snapshots_all_materialize_and_only_different_full_replaces(reverse):
    day = date(2026, 9, 1)
    first = ImportedFacts("z-old", 1, parse_portfolio_file(snapshot_bytes(), as_of=day))
    second = ImportedFacts(
        "a-new",
        2,
        parse_portfolio_file(
            snapshot_bytes(snapshot_row(**{"股票余额": "200"})), as_of=day
        ),
    )
    partial = ImportedFacts(
        "partial",
        3,
        parse_portfolio_file(snapshot_bytes(snapshot_row(), snapshot_row()), as_of=day),
    )
    inputs = (first, second, partial)
    result = reconcile(*(inputs[::-1] if reverse else inputs))
    assert result == reconcile(*inputs)
    assert len(result.snapshots) == 3
    assert len({anchor.snapshot_id for anchor in result.snapshots}) == 3
    warnings = [w for w in result.warnings if w.warning_type == "snapshot_replaced"]
    assert len(warnings) == 1 and warnings[0].import_id == "a-new"
    assert not warnings[0].affects_quantity
    assert any(w.import_id == "partial" for w in result.warnings)
    equal = reconcile(first, replace(first, import_id="equal", import_sequence=4))
    assert not any(w.warning_type == "snapshot_replaced" for w in equal.warnings)


def test_empty_imports_produce_empty_reconciled_portfolio():
    result = reconcile()
    assert result.snapshots == result.events == result.warnings == ()
    assert result.reported_coverage == result.import_ids == ()
