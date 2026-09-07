from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.formats import decode_portfolio_file, parse_portfolio_file
from app.services.real_portfolio.snapshot import parse_snapshot_v1
from tests.unit.real_portfolio.fixtures import (
    SNAPSHOT_HEADER,
    SNAPSHOT_ROW,
    portfolio_bytes,
    snapshot_bytes,
    snapshot_row,
)


AS_OF = date(2026, 9, 6)


def test_snapshot_parses_every_numeric_column_without_precision_loss():
    row = snapshot_row(
        **{
            "证券名称": "  匿名证券  ",
            "总盈亏": "12.34",
            "盈亏比例(%)": "1.23",
            "股票余额": "600",
            "可用余额": "590",
            "冻结数量": "10",
            "参考成本": "9.87",
            "市价": "10.12",
            "当日盈亏": "0.10",
            "当日盈亏比(%)": "0.99",
            "市值": "6072.00",
            "仓位占比(%)": "99.83",
            "当日买入": "20",
            "当日卖出": "10",
            "盈亏价格": "9.90",
        }
    )

    parsed = parse_portfolio_file(snapshot_bytes(row), as_of=AS_OF)
    position = parsed.snapshot_positions[0]

    assert str(position.security) == "A:000001"
    assert position.broker_name == "匿名证券"
    assert position.route == "A"
    assert position.total_profit_loss == Decimal("12.34")
    assert position.profit_loss_percent == Decimal("1.23")
    assert position.quantity == Decimal("600")
    assert position.available_quantity == Decimal("590")
    assert position.frozen_quantity == Decimal("10")
    assert position.reference_cost == Decimal("9.87")
    assert position.reference_cost_currency == "CNY"
    assert position.market_price == Decimal("10.12")
    assert position.market_price_currency == "CNY"
    assert position.daily_profit_loss == Decimal("0.10")
    assert position.daily_profit_loss_percent == Decimal("0.99")
    assert position.market_value == Decimal("6072.00")
    assert position.market_value_currency == "CNY"
    assert position.position_weight_percent == Decimal("99.83")
    assert position.same_day_buy == Decimal("20")
    assert position.same_day_sell == Decimal("10")
    assert position.profit_loss_price == Decimal("9.90")
    assert position.profit_loss_currency == "CNY"
    assert parsed.source_type == "snapshot"
    assert parsed.observed_on == AS_OF
    assert parsed.full_snapshot is True
    assert parsed.coverage_from is None
    assert parsed.coverage_through is None
    assert parsed.delivery_observations == ()
    assert parsed.warnings == ()
    assert parsed.rows[0].usable is True
    assert parsed.rows[0].line_number == 2
    assert parsed.rows[0].transaction_fingerprint is None
    assert parsed.rows[0].contract_fingerprint is None


@pytest.mark.parametrize("market", ("A", "上海A股", "深圳A股"))
def test_snapshot_maps_mainland_market_names_to_a_with_cny_quote(market):
    parsed = parse_snapshot_v1(
        decode_portfolio_file(snapshot_bytes(snapshot_row(**{"交易市场": market}))),
        AS_OF,
    )

    position = parsed.snapshot_positions[0]
    assert str(position.security) == "A:000001"
    assert position.route == "A"
    assert position.market_price_currency == "CNY"


@pytest.mark.parametrize("market", ("沪HK", "深HK"))
def test_snapshot_maps_hong_kong_market_names_to_hk(market):
    row = snapshot_row(**{"证券代码": "02476", "交易市场": market})

    position = parse_snapshot_v1(
        decode_portfolio_file(snapshot_bytes(row)), AS_OF
    ).snapshot_positions[0]

    assert str(position.security) == "HK:02476"
    assert position.route == "HK"


def test_hk_snapshot_keeps_mixed_currency_semantics():
    row = snapshot_row(**{"证券代码": "02476", "交易市场": "沪HK"})

    position = parse_portfolio_file(
        snapshot_bytes(row), as_of=AS_OF
    ).snapshot_positions[0]

    assert str(position.security) == "HK:02476"
    assert position.market_price_currency == "HKD"
    assert position.reference_cost_currency == "CNY"
    assert position.market_value_currency == "CNY"
    assert position.profit_loss_currency == "CNY"


@pytest.mark.parametrize(
    "invalid_cell",
    (
        {"市价": "1e3"},
        {"市价": "NaN"},
        {"市价": ""},
        {"证券代码": "invalid"},
        {"交易市场": "unknown"},
        {"": "unexpected"},
    ),
)
def test_malformed_snapshot_row_warns_without_hiding_a_valid_row(invalid_cell):
    malformed = snapshot_row(**invalid_cell)

    parsed = parse_portfolio_file(
        snapshot_bytes(SNAPSHOT_ROW, malformed), as_of=AS_OF
    )

    assert tuple(str(position.security) for position in parsed.snapshot_positions) == (
        "A:000001",
    )
    assert parsed.full_snapshot is False
    assert len(parsed.warnings) == 1
    assert parsed.warnings[0].line_number == 3
    assert parsed.warnings[0].affects_quantity is True
    assert parsed.rows[0].usable is True
    assert parsed.rows[1].usable is False


@pytest.mark.parametrize(
    "malformed",
    (SNAPSHOT_ROW[:-1], SNAPSHOT_ROW + ("extra",)),
    ids=("missing-cell", "extra-cell"),
)
def test_wrong_width_snapshot_row_warns_without_shifting_columns(malformed):
    parsed = parse_portfolio_file(
        snapshot_bytes(SNAPSHOT_ROW, malformed), as_of=AS_OF
    )

    assert tuple(str(position.security) for position in parsed.snapshot_positions) == (
        "A:000001",
    )
    assert len(parsed.rows) == 2
    assert parsed.rows[1].usable is False
    assert parsed.warnings[0].line_number == 3


def test_blank_rows_are_ignored_when_a_usable_snapshot_row_exists():
    content = portfolio_bytes(
        SNAPSHOT_HEADER,
        ((), ("",) * len(SNAPSHOT_HEADER), SNAPSHOT_ROW),
    )

    parsed = parse_portfolio_file(content, as_of=AS_OF)

    assert len(parsed.rows) == 1
    assert len(parsed.snapshot_positions) == 1
    assert parsed.warnings == ()
    assert parsed.full_snapshot is True
    assert parsed.rows[0].line_number == 4


def test_balance_columns_are_observations_not_an_arithmetic_invariant():
    row = snapshot_row(**{"股票余额": "100", "可用余额": "2", "冻结数量": "3"})

    parsed = parse_portfolio_file(snapshot_bytes(row), as_of=AS_OF)

    assert parsed.full_snapshot is True
    assert parsed.snapshot_positions[0].quantity == Decimal("100")
    assert parsed.snapshot_positions[0].available_quantity == Decimal("2")
    assert parsed.snapshot_positions[0].frozen_quantity == Decimal("3")


def test_duplicate_security_makes_snapshot_partial_and_excludes_both_rows():
    duplicate = snapshot_row(**{"股票余额": "101"})

    parsed = parse_portfolio_file(
        snapshot_bytes(SNAPSHOT_ROW, duplicate), as_of=AS_OF
    )

    assert parsed.snapshot_positions == ()
    assert parsed.full_snapshot is False
    assert len(parsed.warnings) == 1
    assert parsed.warnings[0].line_number is None
    assert parsed.warnings[0].affects_quantity is True


def test_snapshot_requires_an_explicit_as_of_date():
    with pytest.raises(PortfolioError) as captured:
        parse_portfolio_file(snapshot_bytes())

    assert captured.value.code == "SNAPSHOT_DATE_REQUIRED"
    assert captured.value.context == {"source_type": "snapshot"}


def test_snapshot_rejects_non_snapshot_decoded_input():
    decoded = decode_portfolio_file(snapshot_bytes())

    with pytest.raises(ValueError, match="snapshot"):
        parse_snapshot_v1(replace(decoded, format_id="guotai-delivery-v1"), AS_OF)
