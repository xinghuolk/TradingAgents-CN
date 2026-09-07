import json
import re
from dataclasses import asdict, fields
from datetime import date, time
from decimal import Decimal
from hashlib import sha256

from app.services.real_portfolio.formats import (
    DELIVERY_HEADER,
    fingerprint_identifier,
)
from app.services.real_portfolio.models import (
    DecodedPortfolioFile,
    DeliveryObservation,
    EvidenceRef,
    ParsedPortfolioFile,
    ParseWarning,
    SecurityId,
    SourceRow,
    canonical_decimal_string,
)

_MARKETS = {
    "A": "A",
    "上海A股": "A",
    "深圳A股": "A",
    "HK": "HK",
    "沪HK": "HK",
    "深HK": "HK",
}
_QUANTITY_OPERATIONS = {
    "证券买入",
    "证券卖出",
    "上海A股红股上市入账",
    "深圳A股股份特殊调账调入",
    "上海网上新债缴款",
}
_DECIMAL_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")
_DECIMAL_COLUMNS = {
    "quantity": 5,
    "price": 7,
    "gross_amount": 8,
    "current_security_balance": 9,
    "security_balance": 10,
    "available_balance": 11,
    "cash_movement": 12,
    "commission": 13,
    "stamp_tax": 14,
    "misc_fee": 15,
    "cash_balance": 16,
    "current_cash_amount": 17,
    "transfer_fee": 20,
    "settlement_fx": 23,
    "hk_trading_fee": 24,
}
_REQUIRED_DECIMALS = {"quantity", "price", "gross_amount", "cash_movement"}
_TEXT_COLUMNS = {
    "broker_name": 3,
    "route": 19,
    "trade_currency": 21,
    "settlement_currency": 22,
    "currency": 25,
    "market_code": 26,
}


def _optional_text(value: str) -> str | None:
    text = value.strip()
    return None if text in {"", "NULL"} else text


def _decimal(value: str, index: int, required: bool) -> Decimal | None:
    text = _optional_text(value)
    if text is None and not required:
        return None
    if text is None or _DECIMAL_TEXT.fullmatch(text) is None:
        raise ValueError(f"invalid decimal in {DELIVERY_HEADER[index]}")
    return Decimal(text)


def _date(value: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}", value) is None:
        raise ValueError("invalid delivery date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid delivery date") from None


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


def _normalized_row(row: tuple[str, ...]) -> tuple[str, ...]:
    normalized = [cell.strip() for cell in row]
    for index in _DECIMAL_COLUMNS.values():
        if index < len(normalized) and _DECIMAL_TEXT.fullmatch(normalized[index]):
            normalized[index] = canonical_decimal_string(Decimal(normalized[index]))
    if normalized:
        try:
            normalized[0] = _date(normalized[0]).isoformat()
        except ValueError:
            pass
    return tuple(normalized)


def _parse_observation(
    row: tuple[str, ...],
    evidence: EvidenceRef,
    transaction: str | None,
    contract: str | None,
) -> DeliveryObservation:
    if len(row) != 28:
        raise ValueError("delivery row must contain 28 cells")
    if row[27]:
        raise ValueError("delivery trailing cell must be empty")

    trade_date = _date(row[0])
    try:
        trade_time = time.fromisoformat(row[1]) if _optional_text(row[1]) else None
        if trade_time is not None and trade_time.tzinfo is not None:
            raise ValueError
    except ValueError:
        raise ValueError("invalid delivery time") from None

    route = _optional_text(row[19])
    if route is not None and route not in _MARKETS:
        raise ValueError("invalid delivery route")
    code = _optional_text(row[2])
    security = None
    if code is not None:
        if route is None:
            raise ValueError("missing delivery route for security")
        try:
            security = SecurityId(_MARKETS[route], code)
        except ValueError:
            raise ValueError("invalid delivery security code") from None
    if row[4] in _QUANTITY_OPERATIONS and security is None:
        raise ValueError("missing delivery security for quantity operation")

    values = {
        name: _decimal(row[index], index, name in _REQUIRED_DECIMALS)
        for name, index in _DECIMAL_COLUMNS.items()
    }
    return DeliveryObservation(
        evidence=evidence,
        trade_date=trade_date,
        trade_time=trade_time,
        security=security,
        operation=row[4],
        transaction_fingerprint=transaction,
        contract_fingerprint=contract,
        **values,
        **{
            name: _optional_text(row[index])
            for name, index in _TEXT_COLUMNS.items()
        },
    )


def parse_delivery_v1(decoded: DecodedPortfolioFile) -> ParsedPortfolioFile:
    if decoded.format_id != "guotai-delivery-v1":
        raise ValueError("decoded file is not a delivery statement")

    rows: list[SourceRow] = []
    observations: list[DeliveryObservation] = []
    warnings: list[ParseWarning] = []
    known_dates: list[date] = []
    for line_number, raw_row in enumerate(decoded.rows, start=2):
        if not any(cell.strip() for cell in raw_row):
            continue
        row = _normalized_row(raw_row)
        row_hash = _hash(row)
        valid_width = len(row) == 28
        transaction = (
            fingerprint_identifier("transaction", row[6]) if valid_width else None
        )
        contract = (
            fingerprint_identifier("contract", row[18]) if valid_width else None
        )
        fact_key = transaction or row_hash
        redacted = tuple(
            None if not valid_width or index in {6, 18} else cell
            for index, cell in enumerate(row)
        )
        try:
            known_date = _date(row[0]) if valid_width else None
        except ValueError:
            known_date = None
        if known_date is not None:
            known_dates.append(known_date)

        try:
            observation = _parse_observation(
                row,
                EvidenceRef(None, line_number, fact_key, "source"),
                transaction,
                contract,
            )
        except ValueError as error:
            warnings.append(
                ParseWarning(
                    warning_type="invalid_delivery_row",
                    import_id=None,
                    line_number=line_number,
                    impact_from=known_date,
                    impact_through=known_date,
                    message=str(error),
                    affects_quantity=True,
                )
            )
            usable = False
        else:
            observations.append(observation)
            usable = True
        rows.append(
            SourceRow(
                line_number=line_number,
                row_sha256=row_hash,
                fact_key=fact_key,
                fields=redacted,
                transaction_fingerprint=transaction,
                contract_fingerprint=contract,
                usable=usable,
            )
        )

    return ParsedPortfolioFile(
        source_type="delivery_statement",
        format_id=decoded.format_id,
        parser_version=decoded.parser_version,
        file_sha256=decoded.file_sha256,
        rows=tuple(rows),
        snapshot_positions=(),
        delivery_observations=tuple(observations),
        warnings=tuple(warnings),
        observed_on=None,
        full_snapshot=False,
        coverage_from=min(known_dates, default=None),
        coverage_through=max(known_dates, default=None),
    )


def delivery_observation_to_document(
    observation: DeliveryObservation,
) -> dict[str, object]:
    document = {
        field.name: _scalar(getattr(observation, field.name))
        for field in fields(observation)
        if field.name != "evidence"
    }
    document["evidence"] = asdict(observation.evidence)
    return document


def delivery_observation_from_document(
    value: dict[str, object],
) -> DeliveryObservation:
    document = dict(value)
    evidence = document.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("serialized delivery evidence must be a document")
    document["evidence"] = EvidenceRef(**evidence)

    trade_date = document.get("trade_date")
    if not isinstance(trade_date, str):
        raise ValueError("serialized delivery date must be a string")
    document["trade_date"] = date.fromisoformat(trade_date)

    trade_time = document.get("trade_time")
    if trade_time is not None:
        if not isinstance(trade_time, str):
            raise ValueError("serialized delivery time must be a string")
        document["trade_time"] = time.fromisoformat(trade_time)

    security = document.get("security")
    if security is not None:
        if not isinstance(security, str):
            raise ValueError("serialized delivery security must be a string")
        document["security"] = SecurityId.parse(security)

    for name in _DECIMAL_COLUMNS:
        stored = document.get(name)
        if stored is not None:
            if not isinstance(stored, str):
                raise ValueError("serialized delivery decimals must be strings")
            document[name] = Decimal(stored)
    return DeliveryObservation(**document)
