from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.services.real_portfolio.delivery import (
    delivery_observation_from_document,
    delivery_observation_to_document,
)
from app.services.real_portfolio.events import build_delivery_events
from app.services.real_portfolio.formats import parse_portfolio_file
from tests.unit.real_portfolio.fixtures import (
    DELIVERY_HEADER,
    DELIVERY_ROW,
    delivery_bytes,
    delivery_row,
    hk_pair,
)


def parse_delivery_rows(*rows: tuple[str, ...]):
    return parse_portfolio_file(delivery_bytes(*rows))


def unknown_row() -> tuple[str, ...]:
    return delivery_row(**{"操作": "未识别操作", "成交数量": "3"})


def postings(event, role: str):
    return tuple(posting for posting in event.postings if posting.role == role)


@pytest.mark.parametrize(
    ("operation", "event_type", "completeness", "expected_postings"),
    [
        ("沪港通港股证券组合费", "portfolio_fee", "complete", (("cash", "12.34"),)),
        ("深港通港股证券组合费", "portfolio_fee", "complete", (("cash", "12.34"),)),
        ("银行转证券", "bank_transfer", "complete", (("cash", "12.34"),)),
        ("上海A股红利税补缴", "dividend_tax", "complete", (("cash", "12.34"),)),
        ("上海A股红利入账", "cash_dividend", "complete", (("cash", "12.34"),)),
        ("沪港通港股红利入账", "cash_dividend", "complete", (("cash", "12.34"),)),
        ("深港通港股红利入账", "cash_dividend", "complete", (("cash", "12.34"),)),
        ("利息归本", "cash_interest", "complete", (("cash", "12.34"),)),
        ("上海A股红股上市入账", "stock_dividend", "complete", (("security", "10"),)),
        ("深圳A股股份特殊调账调入", "security_adjustment_in", "complete", (("security", "10"),)),
        ("上海网上新债中签", "bond_award_notice", "informational", ()),
        (
            "上海网上新债缴款",
            "bond_subscription_payment",
            "complete",
            (("security", "10"), ("cash", "12.34")),
        ),
        ("上海网上新债缴款确认待上市股份入", "bond_pending_listing", "informational", ()),
        ("上海新债上市", "bond_listing", "informational", ()),
    ],
)
def test_exact_delivery_operation_matrix_preserves_posting_semantics(
    operation, event_type, completeness, expected_postings
):
    row = delivery_row(
        **{
            "操作": operation,
            "成交数量": "10",
            "发生金额": "12.34",
        }
    )
    result = build_delivery_events(parse_delivery_rows(row).delivery_observations)
    (event,) = result.events

    assert event.event_type == event_type
    assert event.completeness == completeness
    assert tuple(
        (posting.role, str(posting.amount)) for posting in event.postings
    ) == expected_postings
    assert all(
        posting.currency == "CNY"
        for posting in event.postings
        if posting.role == "cash"
    )


@pytest.mark.parametrize(
    ("operation", "quantity", "cash"),
    [
        ("证券买入", "100", "-1001.23"),
        ("证券卖出", "-100", "998.77"),
    ],
)
def test_mainland_trade_posts_signed_shares_and_cny_cash(
    operation, quantity, cash
):
    row = delivery_row(
        **{
            "操作": operation,
            "发生金额": cash,
            "交易币种": "HKD",
        }
    )
    result = build_delivery_events(parse_delivery_rows(row).delivery_observations)
    (event,) = result.events

    assert event.event_type == "trade"
    assert event.completeness == "complete"
    assert event.event_id == (
        "ed0a952326f3a6de7d55dfbca0329c96522dc24aa40594cb1e72b1d6b399625d"
    )
    assert event.trade_date == date(2026, 9, 1)
    assert event.settlement_date == date(2026, 9, 1)
    assert postings(event, "security")[0].amount == Decimal(quantity)
    assert postings(event, "cash")[0].amount == Decimal(cash)
    assert postings(event, "cash")[0].currency == "CNY"
    assert ("trade_currency", "HKD") in event.details


def test_hk_execution_and_settlement_become_one_complete_event():
    parsed = parse_delivery_rows(*hk_pair())
    result = build_delivery_events(parsed.delivery_observations)

    assert len(result.events) == 1
    event = result.events[0]
    assert event.event_id == (
        "5005b33e802f4e3a3d1a87c58a57e8b37e1075ee701912c8dd7fd7c95f958dfb"
    )
    assert event.completeness == "complete"
    assert event.trade_date == date(2026, 9, 1)
    assert event.settlement_date == date(2026, 9, 3)
    assert [(posting.role, posting.amount) for posting in event.postings] == [
        ("security", Decimal("100")),
        ("cash", Decimal("-901.23")),
    ]
    assert [
        posting.currency for posting in event.postings if posting.role == "cash"
    ] == ["CNY"]
    assert [ref.role for ref in event.evidence] == ["execution", "settlement"]
    assert result.warnings == ()


@pytest.mark.parametrize(
    ("index", "warning_type", "role", "affects_quantity"),
    [
        (0, "missing_settlement", "security", False),
        (1, "missing_execution", "cash", True),
    ],
)
def test_hk_partial_leg_retains_stable_identity_and_warning_bounds(
    index, warning_type, role, affects_quantity
):
    observation = parse_delivery_rows(*hk_pair()).delivery_observations[index]
    observation = replace(
        observation,
        evidence=replace(observation.evidence, import_id="delivery-7"),
    )

    result = build_delivery_events((observation,))
    (event,) = result.events
    (warning,) = result.warnings

    assert event.event_id == (
        "5005b33e802f4e3a3d1a87c58a57e8b37e1075ee701912c8dd7fd7c95f958dfb"
    )
    assert event.completeness == "partial"
    assert {posting.role for posting in event.postings} == {role}
    assert warning.warning_type == warning_type
    assert warning.import_id == "delivery-7"
    assert warning.line_number == observation.evidence.line_number
    assert warning.affects_quantity is affects_quantity
    assert warning.impact_through == observation.trade_date
    assert warning.impact_from == (None if index else observation.trade_date)
    assert event.trade_date == (observation.trade_date if index == 0 else None)
    assert event.settlement_date == (observation.trade_date if index == 1 else None)
    assert event.warnings == result.warnings


@pytest.mark.parametrize(
    "changes",
    [
        {"过户费": "1"},
        {"港股交易费": "1"},
        {"交易币种": "CNY"},
        {"结算币种": "HKD"},
        {"币种": "HKD"},
        {"手续费": "2"},
        {"印花税": "2"},
        {"其他杂费": "2"},
    ],
)
def test_conflicting_hk_fee_or_currency_never_guesses_share_posting(changes):
    first, second = hk_pair()
    cells = list(second)
    for name, value in changes.items():
        cells[DELIVERY_HEADER.index(name)] = value

    result = build_delivery_events(
        parse_delivery_rows(first, tuple(cells)).delivery_observations
    )

    assert {warning.warning_type for warning in result.warnings} == {
        "ambiguous_pair"
    }
    assert not any(postings(event, "security") for event in result.events)


@pytest.mark.parametrize("case", ["multiple_execution", "same_date", "reverse_date"])
def test_multiple_or_invalid_hk_date_candidates_are_ambiguous(case):
    first, second = hk_pair()
    rows = [first, second]
    if case == "multiple_execution":
        rows.append(
            delivery_row(
                **{
                    "证券代码": "00700",
                    "市场名称": "沪HK",
                    "交易币种": "HKD",
                    "发生金额": "0",
                    "成交编号": "another-execution",
                }
            )
        )
    else:
        cells = list(second)
        cells[0] = "2026-09-01" if case == "same_date" else "2026-08-31"
        rows[1] = tuple(cells)

    result = build_delivery_events(parse_delivery_rows(*rows).delivery_observations)

    assert {warning.warning_type for warning in result.warnings} == {
        "ambiguous_pair"
    }
    assert not any(postings(event, "security") for event in result.events)


def test_unpairable_hk_rows_without_contract_keep_distinct_fact_ids():
    first, second = hk_pair()
    first = list(first)
    second = list(second)
    first[6], second[6] = "execution-id", "settlement-id"
    first[18] = second[18] = "NULL"
    observations = parse_delivery_rows(
        tuple(first), tuple(second)
    ).delivery_observations

    result = build_delivery_events(observations)

    assert len(result.events) == 2
    assert {event.event_id for event in result.events} == {
        observation.evidence.fact_key for observation in observations
    }
    assert {warning.warning_type for warning in result.warnings} == {
        "missing_execution",
        "missing_settlement",
    }


def test_hk_event_rebuild_is_deterministic_after_bson_round_trip_and_reordering():
    observations = tuple(
        replace(
            observation,
            evidence=replace(
                observation.evidence, import_id=f"delivery-{index}"
            ),
        )
        for index, observation in enumerate(
            parse_delivery_rows(*hk_pair()).delivery_observations, start=1
        )
    )
    rebuilt = tuple(
        delivery_observation_from_document(
            delivery_observation_to_document(observation)
        )
        for observation in reversed(observations)
    )

    assert build_delivery_events(rebuilt) == build_delivery_events(observations)


def test_equal_observations_merge_evidence_without_duplicate_postings():
    first, second = parse_delivery_rows(*hk_pair()).delivery_observations
    repeated = replace(
        first, evidence=replace(first.evidence, import_id="delivery-repeat")
    )

    result = build_delivery_events((repeated, second, first))
    (event,) = result.events

    assert len(event.evidence) == 3
    assert len(postings(event, "security")) == 1
    assert len(postings(event, "cash")) == 1
    assert result.warnings == ()


def test_reverse_repo_open_and_close_keep_cash_sign_phase_identity():
    common = {
        "证券代码": "204001",
        "证券名称": "GC001",
        "操作": "证券卖出",
    }
    opening = delivery_row(
        **(common | {"发生金额": "-100001", "成交编号": "repo-open"})
    )
    close = delivery_row(
        **(
            common
            | {
                "成交日期": "2026-09-02",
                "发生金额": "100011.01",
                "成交编号": "repo-close",
            }
        )
    )

    close_only = build_delivery_events(
        parse_delivery_rows(close).delivery_observations
    ).events[0]
    result = build_delivery_events(
        parse_delivery_rows(close, opening).delivery_observations
    )
    by_type = {event.event_type: event for event in result.events}

    assert close_only == by_type["repo_close"]
    assert by_type["repo_open"].event_id == (
        "3896c6a2f894a594ae48cc11eea0c7f50b62b549fde26d51c80aafb3e5f1fb2e"
    )
    assert by_type["repo_close"].event_id == (
        "bb841342a7c5c4d581e1d9594aa763bb7ec0dedc22079df211cef78aec9bdd7d"
    )
    assert not any(postings(event, "security") for event in result.events)
    assert sum(
        posting.amount
        for event in result.events
        for posting in postings(event, "cash")
    ) == Decimal("10.01")


def test_reverse_repo_without_contract_falls_back_to_fact_id():
    row = delivery_row(
        **{
            "证券代码": "204001",
            "证券名称": "GC001",
            "操作": "证券卖出",
            "合同编号": "NULL",
        }
    )
    observation = parse_delivery_rows(row).delivery_observations[0]

    event = build_delivery_events((observation,)).events[0]

    assert event.event_type == "repo_open"
    assert event.event_id == observation.evidence.fact_key


@pytest.mark.parametrize(
    "changes",
    [
        {"证券代码": "204002", "证券名称": "GC002"},
        {"证券代码": "204001", "证券名称": "GC001", "发生金额": "0"},
    ],
)
def test_unsupported_repo_shape_is_unclassified_without_share_delta(changes):
    event = build_delivery_events(
        parse_delivery_rows(delivery_row(**changes)).delivery_observations
    ).events[0]

    assert event.completeness == "unclassified"
    assert event.postings == ()
    assert event.warnings[0].affects_quantity is True


def test_zero_buy_is_an_informational_subscription_notice():
    row = delivery_row(
        **{
            "成交数量": "0",
            "成交均价": "0",
            "成交金额": "0",
            "发生金额": "0",
        }
    )

    event = build_delivery_events(
        parse_delivery_rows(row).delivery_observations
    ).events[0]

    assert event.event_type == "subscription_notice"
    assert event.completeness == "informational"
    assert event.postings == ()


def test_unknown_operation_is_preserved_without_postings():
    result = build_delivery_events(
        parse_delivery_rows(unknown_row()).delivery_observations
    )

    assert result.events[0].event_type == "unclassified"
    assert result.events[0].completeness == "unclassified"
    assert result.events[0].postings == ()
    assert result.events[0].warnings == result.warnings
    assert result.warnings[0].affects_quantity is True
    assert ("warning", "unclassified") in result.events[0].details


def test_mixed_trade_operations_and_unknown_rows_all_survive_dispatch():
    rows = (
        delivery_row(**{"成交编号": "trade"}),
        delivery_row(
            **{
                "成交编号": "fee",
                "操作": "沪港通港股证券组合费",
                "发生金额": "-1.25",
            }
        ),
        delivery_row(**{"成交编号": "unknown", "操作": "未识别操作"}),
    )

    result = build_delivery_events(parse_delivery_rows(*rows).delivery_observations)

    assert {event.event_type for event in result.events} == {
        "trade",
        "portfolio_fee",
        "unclassified",
    }
    assert len(result.warnings) == 1


@pytest.mark.parametrize("operation", ["买入", "卖出", "not-a-known-operation"])
def test_operation_aliases_are_unclassified_without_share_postings(operation):
    row = delivery_row(**{"操作": operation})

    event = build_delivery_events(
        parse_delivery_rows(row).delivery_observations
    ).events[0]

    assert event.event_type == "unclassified"
    assert event.postings == ()


@pytest.mark.parametrize("operation", ["证券买入", "证券卖出"])
def test_share_signing_does_not_round_to_decimal_context(operation):
    quantity = "123456789012345678901234567890.123456789"
    row = delivery_row(**{"操作": operation, "成交数量": quantity})

    event = build_delivery_events(
        parse_delivery_rows(row).delivery_observations
    ).events[0]

    expected = Decimal(("-" if operation == "证券卖出" else "") + quantity)
    assert postings(event, "security")[0].amount == expected
