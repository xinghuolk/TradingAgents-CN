import json
from dataclasses import replace
from datetime import date, time
from decimal import Decimal

import pytest

from app.services.real_portfolio.delivery import (
    delivery_observation_from_document,
    delivery_observation_to_document,
    parse_delivery_v1,
)
from app.services.real_portfolio.formats import (
    decode_portfolio_file,
    parse_portfolio_file,
)
from app.services.real_portfolio.models import SecurityId
from tests.unit.real_portfolio.fixtures import (
    DELIVERY_ROW,
    delivery_bytes,
    delivery_row,
)


def parse_delivery_rows(*rows: tuple[str, ...]):
    return parse_portfolio_file(delivery_bytes(*rows))


def test_delivery_redacts_identifiers_before_persistence_shape():
    parsed = parse_portfolio_file(delivery_bytes())
    source = parsed.rows[0]
    observation = parsed.delivery_observations[0]

    assert source.fields[6] is None
    assert source.fields[18] is None
    assert source.transaction_fingerprint == (
        "ed0a952326f3a6de7d55dfbca0329c96522dc24aa40594cb1e72b1d6b399625d"
    )
    assert source.contract_fingerprint == (
        "fd629fa9fead6ecead4d72ed7c6032098bfa40a7f2ae460396d9fbc8ed5208bf"
    )
    assert source.transaction_fingerprint == observation.transaction_fingerprint
    assert source.contract_fingerprint == observation.contract_fingerprint
    assert source.fact_key == observation.evidence.fact_key
    assert "成交编号" not in repr(source)
    assert "合同编号" not in repr(source)
    assert observation.cash_movement == Decimal("-1001")


def test_delivery_parses_every_column_without_float_precision_loss():
    parsed = parse_delivery_rows(
        delivery_row(
            **{
                "证券名称": "  匿名证券  ",
                "成交均价": "12345678901234567890.123456789",
            }
        )
    )
    (observation,) = parsed.delivery_observations

    assert observation.evidence.line_number == 2
    assert observation.trade_date == date(2026, 9, 1)
    assert observation.trade_time == time(9, 30)
    assert observation.security == SecurityId("A", "000001")
    assert observation.broker_name == "匿名证券"
    assert observation.operation == "证券买入"
    assert observation.route == "A"
    assert observation.trade_currency == "CNY"
    assert observation.settlement_currency == "CNY"
    assert observation.currency == "CNY"
    assert observation.market_code == "A"
    assert observation.price == Decimal("12345678901234567890.123456789")
    assert observation.quantity == Decimal("100")
    assert observation.gross_amount == Decimal("1000")
    assert observation.current_security_balance == Decimal("100")
    assert observation.security_balance == Decimal("100")
    assert observation.available_balance == Decimal("100")
    assert observation.commission == Decimal("1")
    assert observation.stamp_tax == Decimal("0")
    assert observation.misc_fee == Decimal("0")
    assert observation.cash_balance == Decimal("9999")
    assert observation.current_cash_amount == Decimal("-1001")
    assert observation.transfer_fee == Decimal("0")
    assert observation.settlement_fx == Decimal("1")
    assert observation.hk_trading_fee == Decimal("0")
    assert parsed.source_type == "delivery_statement"
    assert parsed.observed_on is None
    assert parsed.full_snapshot is False


def test_delivery_normalizes_dates_and_covers_all_parseable_source_dates():
    parsed = parse_delivery_rows(
        delivery_row(**{"成交日期": "20260901", "成交编号": "first"}),
        delivery_row(
            **{
                "成交日期": "2026-09-02",
                "成交编号": "malformed",
                "成交数量": "NaN",
            }
        ),
        delivery_row(**{"成交日期": "2026-09-03", "成交编号": "last"}),
    )

    assert parsed.rows[0].fields[0] == "2026-09-01"
    assert parsed.coverage_from == date(2026, 9, 1)
    assert parsed.coverage_through == date(2026, 9, 3)
    assert [row.usable for row in parsed.rows] == [True, False, True]
    assert parsed.warnings[0].impact_from == date(2026, 9, 2)
    assert parsed.warnings[0].impact_through == date(2026, 9, 2)


@pytest.mark.parametrize(
    "changes",
    [
        {"成交日期": "bad-date"},
        {"成交日期": "2026-02-30"},
        {"成交数量": "NaN"},
        {"成交均价": ""},
        {"成交金额": "1e2"},
        {"发生金额": "Infinity"},
        {"市场名称": "unknown"},
        {"证券代码": "bad-code"},
        {"成交时间": "25:00:00"},
        {"成交时间": "09:30:00+08:00"},
        {"过户费": "bad-fee"},
    ],
)
def test_malformed_delivery_row_warns_and_later_rows_still_parse(changes):
    decoded = decode_portfolio_file(delivery_bytes())
    parsed = parse_delivery_v1(
        replace(decoded, rows=(delivery_row(**changes), DELIVERY_ROW))
    )

    assert [row.usable for row in parsed.rows] == [False, True]
    assert parsed.rows[0].fields[6] is None
    assert parsed.rows[0].fields[18] is None
    assert len(parsed.delivery_observations) == 1
    (warning,) = parsed.warnings
    assert warning.warning_type == "invalid_delivery_row"
    assert warning.line_number == 2
    assert warning.affects_quantity is True
    if "成交日期" in changes:
        assert warning.impact_from is None
        assert warning.impact_through is None
    else:
        assert warning.impact_from == date(2026, 9, 1)
        assert warning.impact_through == date(2026, 9, 1)


@pytest.mark.parametrize("bad_row", [DELIVERY_ROW[:-1], DELIVERY_ROW + ("extra",)])
def test_delivery_rejects_wrong_width_without_retaining_sensitive_cells(bad_row):
    decoded = decode_portfolio_file(delivery_bytes())
    parsed = parse_delivery_v1(replace(decoded, rows=(bad_row, DELIVERY_ROW)))

    assert parsed.rows[0].usable is False
    assert set(parsed.rows[0].fields) == {None}
    assert parsed.warnings[0].warning_type == "invalid_delivery_row"
    assert len(parsed.delivery_observations) == 1


def test_delivery_ignores_blank_rows_and_accepts_missing_optional_values():
    parsed = parse_delivery_rows(
        ("",), delivery_row(**{"成交时间": "", "过户费": "NULL"})
    )

    assert len(parsed.rows) == 1
    assert parsed.rows[0].line_number == 3
    (observation,) = parsed.delivery_observations
    assert observation.trade_time is None
    assert observation.transfer_fee is None


@pytest.mark.parametrize(
    "operation",
    ["上海A股红股上市入账", "深圳A股股份特殊调账调入", "上海网上新债缴款"],
)
@pytest.mark.parametrize("code", ["", "NULL", "bad-security"])
def test_quantity_operation_without_valid_security_warns_and_continues(
    operation, code
):
    decoded = decode_portfolio_file(delivery_bytes())
    malformed = delivery_row(
        **{
            "操作": operation,
            "证券代码": code,
            "成交编号": "malformed-action",
        }
    )
    parsed = parse_delivery_v1(replace(decoded, rows=(malformed, DELIVERY_ROW)))

    assert [row.usable for row in parsed.rows] == [False, True]
    assert parsed.warnings[0].affects_quantity is True
    assert parsed.delivery_observations[0].operation == "证券买入"


def test_anonymous_fact_key_uses_normalized_row_content():
    first = delivery_row(
        **{"成交编号": "NULL", "合同编号": "", "成交数量": " 100.00 "}
    )
    second = delivery_row(
        **{"成交编号": "NULL", "合同编号": "", "成交数量": "100"}
    )

    first_parsed = parse_delivery_rows(first)
    second_parsed = parse_delivery_rows(second)

    expected = "032a74cc6fd1b854ada79050dd741912efc5aa96381412e6df2a1c2569fe52be"
    assert first_parsed.rows[0].fact_key == expected
    assert second_parsed.rows[0].fact_key == expected


def test_delivery_observation_document_is_bson_safe_and_round_trips():
    observation = parse_delivery_rows(DELIVERY_ROW).delivery_observations[0]
    observation = replace(
        observation,
        evidence=replace(observation.evidence, import_id="delivery-opaque-1"),
    )

    document = delivery_observation_to_document(observation)

    assert document["trade_date"] == "2026-09-01"
    assert document["trade_time"] == "09:30:00"
    assert document["security"] == "A:000001"
    assert document["quantity"] == "100"
    assert document["cash_movement"] == "-1001"
    assert document["evidence"] == {
        "import_id": "delivery-opaque-1",
        "line_number": 2,
        "fact_key": (
            "ed0a952326f3a6de7d55dfbca0329c96522dc24aa40594cb1e72b1d6b399625d"
        ),
        "role": "source",
    }
    json.dumps(document)
    assert delivery_observation_from_document(document) == observation


def test_delivery_observation_document_rejects_non_string_decimal_storage():
    observation = parse_delivery_rows(DELIVERY_ROW).delivery_observations[0]
    document = delivery_observation_to_document(observation)
    document["quantity"] = 100

    with pytest.raises(ValueError, match="decimals must be strings"):
        delivery_observation_from_document(document)
