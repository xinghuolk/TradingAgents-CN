import re
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from app.services.real_portfolio.models import (
    DecodedPortfolioFile,
    ParsedPortfolioFile,
    ParseWarning,
    SecurityId,
    SnapshotPosition,
    SourceRow,
)

_DECIMAL_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_MARKETS = {
    "A": "A",
    "上海A股": "A",
    "深圳A股": "A",
    "HK": "HK",
    "沪HK": "HK",
    "深HK": "HK",
}


def _decimal(value: str, field: str) -> Decimal:
    text = value.strip()
    if _DECIMAL_TEXT.fullmatch(text) is None:
        raise ValueError(f"invalid decimal in {field}")
    try:
        parsed = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"invalid decimal in {field}") from error
    if not parsed.is_finite():
        raise ValueError(f"invalid decimal in {field}")
    return parsed


def _row_sha256(row: tuple[str, ...]) -> str:
    return sha256("\0".join(row).encode("utf-8")).hexdigest()


def _warning(line_number: int | None, as_of: date, message: str) -> ParseWarning:
    return ParseWarning(
        warning_type="invalid_snapshot_row",
        import_id=None,
        line_number=line_number,
        impact_from=as_of,
        impact_through=as_of,
        message=message,
        affects_quantity=True,
    )


def _parse_security(row: tuple[str, ...]) -> tuple[str, SecurityId]:
    if len(row) != 20:
        raise ValueError("snapshot row must contain 20 cells")
    market = _MARKETS.get(row[17].strip())
    if market is None:
        raise ValueError("invalid snapshot market")
    try:
        security = SecurityId(market, row[2].strip())
    except ValueError:
        raise ValueError("invalid snapshot security") from None
    return market, security


def _parse_position(
    row: tuple[str, ...], market: str, security: SecurityId
) -> SnapshotPosition:
    if row[-1] != "":
        raise ValueError("snapshot trailing cell must be empty")
    if not row[3].strip():
        raise ValueError("snapshot broker name must not be empty")

    values = {
        "总盈亏": _decimal(row[4], "总盈亏"),
        "盈亏比例(%)": _decimal(row[5], "盈亏比例(%)"),
        "股票余额": _decimal(row[6], "股票余额"),
        "可用余额": _decimal(row[7], "可用余额"),
        "冻结数量": _decimal(row[8], "冻结数量"),
        "参考成本": _decimal(row[9], "参考成本"),
        "市价": _decimal(row[10], "市价"),
        "当日盈亏": _decimal(row[11], "当日盈亏"),
        "当日盈亏比(%)": _decimal(row[12], "当日盈亏比(%)"),
        "市值": _decimal(row[13], "市值"),
        "仓位占比(%)": _decimal(row[14], "仓位占比(%)"),
        "当日买入": _decimal(row[15], "当日买入"),
        "当日卖出": _decimal(row[16], "当日卖出"),
        "盈亏价格": _decimal(row[18], "盈亏价格"),
    }
    return SnapshotPosition(
        security=security,
        broker_name=row[3].strip(),
        route=market,
        total_profit_loss=values["总盈亏"],
        profit_loss_percent=values["盈亏比例(%)"],
        quantity=values["股票余额"],
        available_quantity=values["可用余额"],
        frozen_quantity=values["冻结数量"],
        reference_cost=values["参考成本"],
        reference_cost_currency="CNY",
        market_price=values["市价"],
        market_price_currency="HKD" if market == "HK" else "CNY",
        daily_profit_loss=values["当日盈亏"],
        daily_profit_loss_percent=values["当日盈亏比(%)"],
        market_value=values["市值"],
        market_value_currency="CNY",
        position_weight_percent=values["仓位占比(%)"],
        same_day_buy=values["当日买入"],
        same_day_sell=values["当日卖出"],
        profit_loss_price=values["盈亏价格"],
        profit_loss_currency="CNY",
    )


def parse_snapshot_v1(
    decoded: DecodedPortfolioFile, as_of: date
) -> ParsedPortfolioFile:
    if decoded.format_id != "guotai-snapshot-v1":
        raise ValueError("decoded file is not a snapshot")

    rows: list[SourceRow] = []
    warnings: list[ParseWarning] = []
    positions: dict[SecurityId, SnapshotPosition] = {}
    seen_securities: set[SecurityId] = set()
    duplicate_securities: set[SecurityId] = set()
    nonblank_rows = 0

    for line_number, row in enumerate(decoded.rows, start=2):
        if not any(cell.strip() for cell in row):
            continue
        nonblank_rows += 1
        row_sha256 = _row_sha256(row)
        try:
            market, security = _parse_security(row)
        except ValueError as error:
            rows.append(
                SourceRow(
                    line_number,
                    row_sha256,
                    row_sha256,
                    row,
                    None,
                    None,
                    False,
                )
            )
            warnings.append(_warning(line_number, as_of, str(error)))
            continue

        if security in seen_securities:
            duplicate_securities.add(security)
            positions.pop(security, None)
        else:
            seen_securities.add(security)

        try:
            position = _parse_position(row, market, security)
        except ValueError as error:
            rows.append(
                SourceRow(
                    line_number,
                    row_sha256,
                    row_sha256,
                    row,
                    None,
                    None,
                    False,
                )
            )
            warnings.append(_warning(line_number, as_of, str(error)))
            continue

        rows.append(
            SourceRow(
                line_number,
                row_sha256,
                row_sha256,
                row,
                None,
                None,
                True,
            )
        )
        if position.security not in duplicate_securities:
            positions[position.security] = position

    for security in sorted(duplicate_securities, key=str):
        warnings.append(
            _warning(None, as_of, f"duplicate snapshot security: {security}")
        )

    return ParsedPortfolioFile(
        source_type="snapshot",
        format_id=decoded.format_id,
        parser_version=decoded.parser_version,
        file_sha256=decoded.file_sha256,
        rows=tuple(rows),
        snapshot_positions=tuple(positions.values()),
        delivery_observations=(),
        warnings=tuple(warnings),
        observed_on=as_of,
        full_snapshot=nonblank_rows == len(positions) and not warnings,
        coverage_from=None,
        coverage_through=None,
    )
