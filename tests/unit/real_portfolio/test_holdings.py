from dataclasses import fields, replace
from datetime import date
from decimal import Decimal

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.formats import parse_portfolio_file
from app.services.real_portfolio.holdings import build_portfolio_view
from app.services.real_portfolio.models import (
    Holding,
    ImportedFacts,
    ParseWarning,
    ReconciledPortfolio,
)
from app.services.real_portfolio.reconciliation import reconcile_imports
from tests.unit.real_portfolio.fixtures import (
    SNAPSHOT_ROW,
    delivery_bytes,
    delivery_row,
    snapshot_bytes,
    snapshot_row,
)

D = date.fromisoformat


def portfolio_with_snapshot_and_trade(
    *, snapshot_date: str, snapshot_quantity: str, trade_date: str, trade_quantity: str
) -> ReconciledPortfolio:
    snapshot = parse_portfolio_file(
        snapshot_bytes(snapshot_row(**{"股票余额": snapshot_quantity})),
        as_of=D(snapshot_date),
    )
    trade = parse_portfolio_file(
        delivery_bytes(
            delivery_row(
                **{
                    "成交日期": trade_date,
                    "成交数量": trade_quantity,
                }
            )
        )
    )
    return reconcile_imports(
        user_id="user-1",
        account_alias="main",
        imports=(
            ImportedFacts("snapshot-1", 1, snapshot),
            ImportedFacts("delivery-2", 2, trade),
        ),
    )


def portfolio_with_partial_snapshot(snapshot_date: str) -> ReconciledPortfolio:
    duplicate = snapshot_row(**{"股票余额": "101"})
    parsed = parse_portfolio_file(
        snapshot_bytes(SNAPSHOT_ROW, duplicate),
        as_of=D(snapshot_date),
    )
    return reconcile(ImportedFacts("partial-1", 1, parsed))


def reconcile(*imports):
    return reconcile_imports(user_id="user-1", account_alias="main", imports=imports)


def snapshot(sequence, day, quantity="100", *, partial=False):
    rows = [snapshot_row(**{"股票余额": quantity})]
    if partial:
        rows.append(snapshot_row(**{"证券代码": "invalid"}))
    return ImportedFacts(
        f"snapshot-{sequence}",
        sequence,
        parse_portfolio_file(snapshot_bytes(*rows), as_of=D(day)),
    )


def delivery(sequence, *rows):
    return ImportedFacts(
        f"delivery-{sequence}",
        sequence,
        parse_portfolio_file(delivery_bytes(*rows)),
    )


def cash_coverage(sequence, start, end):
    return delivery(
        sequence,
        *(
            delivery_row(
                **{
                    "成交日期": day,
                    "成交编号": f"cash-{sequence}-{index}",
                    "操作": "银行转证券",
                }
            )
            for index, day in enumerate((start, end))
        ),
    )


def quantities(view):
    return {str(holding.security): holding.quantity for holding in view.holdings}


def test_forward_reconstruction_uses_open_closed_posting_interval():
    portfolio = portfolio_with_snapshot_and_trade(
        snapshot_date="2026-09-01",
        snapshot_quantity="100",
        trade_date="2026-09-02",
        trade_quantity="20",
    )
    view = build_portfolio_view(portfolio, D("2026-09-02"))
    assert view.direction == "forward"
    assert view.holdings[0].quantity == Decimal("120")
    assert view.holdings[0].available_quantity is None
    assert view.holdings[0].reference_cost is None


def test_exact_full_anchor_preserves_broker_values_and_excludes_unlisted_securities():
    portfolio = reconcile(
        snapshot(1, "2026-09-06", "1200"),
        delivery(
            2,
            delivery_row(**{"成交日期": "2026-09-06", "成交数量": "9000"}),
            delivery_row(
                **{"成交日期": "2026-09-05", "成交编号": "other", "证券代码": "600000"}
            ),
        ),
    )
    view = build_portfolio_view(portfolio, D("2026-09-06"))
    assert view.as_of == view.anchor_date == D("2026-09-06")
    assert (view.direction, view.completeness) == ("exact", "authoritative")
    assert quantities(view) == {"A:000001": Decimal("1200")}
    holding = view.holdings[0]
    assert holding.available_quantity == Decimal("100")
    assert holding.reference_cost == Decimal("10")
    assert holding.market_price == Decimal("10.12")
    assert holding.market_value == Decimal("1012")
    assert holding.total_profit_loss == Decimal("12.34")
    assert holding.reference_cost_currency == holding.market_value_currency == "CNY"
    assert holding.latest_quote_price is None


@pytest.mark.parametrize(
    "anchor,as_of,direction,quantity",
    [
        ("2026-09-03", "2026-09-05", "forward", "1170"),
        ("2026-09-05", "2026-09-03", "reverse", "1230"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_posting_boundaries_ignore_cash_and_clear_all_snapshot_fields(
    anchor,
    as_of,
    direction,
    quantity,
    reverse,
):
    trades = delivery(
        2,
        *(
            delivery_row(
                **{
                    "成交日期": day,
                    "成交数量": amount,
                    "操作": operation,
                    "成交编号": day,
                }
            )
            for day, amount, operation in (
                ("2026-09-03", "999", "证券买入"),
                ("2026-09-04", "20", "证券买入"),
                ("2026-09-05", "50", "证券卖出"),
                ("2026-09-06", "888", "证券买入"),
            )
        ),
    )
    inputs = (
        snapshot(1, anchor, "1200"),
        trades,
        cash_coverage(3, "2026-09-04", "2026-09-04"),
    )
    portfolio = reconcile(*(inputs[::-1] if reverse else inputs))
    view = build_portfolio_view(portfolio, D(as_of))
    assert view.direction == direction and view.anchor_date == D(anchor)
    assert quantities(view) == {"A:000001": Decimal(quantity)}
    assert portfolio.snapshots[0].positions[0].quantity == Decimal("1200")
    holding = view.holdings[0]
    assert holding.broker_name == "匿名证券" and holding.route == "A"
    known = {"security", "broker_name", "route", "quantity"}
    assert all(
        getattr(holding, field.name) is None
        for field in fields(Holding)
        if field.name not in known
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "as_of,anchor,direction,quantity",
    [
        ("2026-09-04", "2026-09-01", "forward", "200"),
        ("2026-08-31", "2026-09-01", "reverse", "200"),
        ("2026-09-01", "2026-09-01", "exact", "200"),
        ("2026-09-06", "2026-09-05", "forward", "500"),
    ],
)
def test_full_anchor_selection_prefers_prior_then_sequence_over_partial(
    reverse,
    as_of,
    anchor,
    direction,
    quantity,
):
    inputs = (
        snapshot(1, "2026-09-05", "500"),
        replace(snapshot(2, "2026-09-01", "100"), import_id="z-old"),
        replace(snapshot(3, "2026-09-01", "200"), import_id="a-new"),
        snapshot(4, "2026-09-01", "999", partial=True),
        snapshot(5, "2026-09-04", "999", partial=True),
    )
    view = build_portfolio_view(
        reconcile(*(inputs[::-1] if reverse else inputs)), D(as_of)
    )
    assert (view.anchor_date, view.direction) == (D(anchor), direction)
    assert quantities(view) == {"A:000001": Decimal(quantity)}


def test_exact_partial_snapshot_never_implies_absent_security_is_zero():
    portfolio = portfolio_with_partial_snapshot("2026-09-01")
    exact = build_portfolio_view(portfolio, D("2026-09-01"))
    assert exact.direction == "partial_snapshot" and exact.completeness == "incomplete"
    assert exact.holdings == ()
    assert any(w.warning_type == "partial_snapshot" for w in exact.warnings)
    for day in ("2026-08-31", "2026-09-02"):
        with pytest.raises(PortfolioError, match="no full portfolio snapshot"):
            build_portfolio_view(portfolio, D(day))


def test_latest_exact_partial_snapshot_keeps_only_known_positions():
    portfolio = reconcile(
        snapshot(1, "2026-09-01", "100", partial=True),
        snapshot(2, "2026-09-01", "200", partial=True),
        delivery(3, delivery_row(**{"证券代码": "600000"})),
    )
    view = build_portfolio_view(portfolio, D("2026-09-01"))
    assert quantities(view) == {"A:000001": Decimal("200")}
    assert view.direction == "partial_snapshot" and view.completeness == "incomplete"
    assert view.holdings[0].reference_cost == Decimal("10")


def test_new_securities_take_event_metadata_and_zero_holdings_are_hidden():
    portfolio = reconcile(
        snapshot(1, "2026-08-31"),
        delivery(
            2,
            delivery_row(**{"成交编号": "sell", "操作": "证券卖出"}),
            delivery_row(
                **{
                    "成交编号": "hk",
                    "证券代码": "00700",
                    "市场名称": "沪HK",
                    "交易币种": "HKD",
                    "发生金额": "0",
                    "成交数量": "7",
                }
            ),
            delivery_row(
                **{
                    "成交编号": "new",
                    "证券代码": "600000",
                    "成交数量": "8",
                    "证券名称": "new-security",
                }
            ),
        ),
    )
    view = build_portfolio_view(portfolio, D("2026-09-01"))
    assert list(quantities(view).items()) == [
        ("A:600000", Decimal("8")),
        ("HK:00700", Decimal("7")),
    ]
    assert view.holdings[0].broker_name == "new-security"
    assert view.holdings[1].route == "沪HK"
    assert view.completeness == "reported_coverage"
    assert any(
        w.warning_type == "missing_settlement" and not w.affects_quantity
        for w in view.warnings
    )


@pytest.mark.parametrize(
    "anchor,as_of", [("2026-01-04", "2026-01-06"), ("2026-01-06", "2026-01-04")]
)
@pytest.mark.parametrize("reverse", [False, True])
def test_adjacent_coverage_needs_only_posting_dates(anchor, as_of, reverse):
    inputs = (
        snapshot(1, anchor),
        cash_coverage(2, "2026-01-05", "2026-01-05"),
        cash_coverage(3, "2026-01-06", "2026-01-06"),
    )
    view = build_portfolio_view(
        reconcile(*(inputs[::-1] if reverse else inputs)), D(as_of)
    )
    assert quantities(view) == {"A:000001": Decimal("100")}
    assert view.completeness == "reported_coverage"
    assert view.reported_coverage == ((D("2026-01-05"), D("2026-01-06")),)
    assert any("unfiltered" in w.message for w in view.warnings)


@pytest.mark.parametrize(
    "anchor,as_of,expected",
    [
        ("2026-01-02", "2026-01-05", "reported_coverage"),
        ("2026-01-05", "2026-01-02", "reported_coverage"),
        ("2026-01-02", "2025-12-31", "incomplete"),
        ("2026-01-02", "2026-01-06", "incomplete"),
        ("2026-01-02", "2026-01-09", "incomplete"),
        ("2026-01-08", "2026-01-11", "incomplete"),
    ],
)
def test_overlapping_coverage_merges_but_disjoint_gaps_remain(anchor, as_of, expected):
    portfolio = reconcile(
        snapshot(1, anchor),
        cash_coverage(2, "2026-01-02", "2026-01-04"),
        cash_coverage(3, "2026-01-03", "2026-01-05"),
        cash_coverage(4, "2026-01-08", "2026-01-10"),
    )
    view = build_portfolio_view(portfolio, D(as_of))
    assert view.reported_coverage == (
        (D("2026-01-02"), D("2026-01-05")),
        (D("2026-01-08"), D("2026-01-10")),
    )
    assert view.completeness == expected
    assert any(w.warning_type == "coverage_gap" for w in view.warnings) == (
        expected == "incomplete"
    )


@pytest.mark.parametrize(
    "anchor,as_of", [("2026-09-01", "2026-09-03"), ("2026-09-03", "2026-09-01")]
)
@pytest.mark.parametrize(
    "start,end,affects,expected",
    [
        (None, None, True, "incomplete"),
        (None, "2026-09-01", True, "reported_coverage"),
        (None, "2026-09-02", True, "incomplete"),
        ("2026-09-03", None, True, "incomplete"),
        ("2026-09-04", None, True, "reported_coverage"),
        ("2026-09-01", "2026-09-01", True, "reported_coverage"),
        ("2026-09-03", "2026-09-03", True, "incomplete"),
        (None, None, False, "reported_coverage"),
    ],
)
def test_warning_intersection_uses_open_closed_unbounded_interval(
    anchor, as_of, start, end, affects, expected
):
    imported = cash_coverage(2, "2026-09-01", "2026-09-03")
    warning = ParseWarning(
        "source_issue",
        None,
        2,
        D(start) if start else None,
        D(end) if end else None,
        "source issue",
        affects,
    )
    imported = replace(imported, parsed=replace(imported.parsed, warnings=(warning,)))
    view = build_portfolio_view(reconcile(snapshot(1, anchor), imported), D(as_of))
    assert view.completeness == expected
    assert quantities(view) == {"A:000001": Decimal("100")}
    selected = [w for w in view.warnings if w.warning_type == "source_issue"]
    assert bool(selected) == (expected == "incomplete" or not affects)
    assert all(w.import_id == "delivery-2" for w in selected)


def test_empty_portfolio_raises_clear_error():
    with pytest.raises(PortfolioError, match="no full portfolio snapshot"):
        build_portfolio_view(reconcile(), D("2026-09-01"))
