import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Generic, Literal, TypeVar


def canonical_decimal_string(value: Decimal) -> str:
    """Return one fixed-point representation for a finite Decimal value."""
    if type(value) is not Decimal:
        raise TypeError("value must be a Decimal")
    if not value.is_finite():
        raise ValueError("value must be finite")
    if value.is_zero():
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


_SECURITY_PATTERNS = {
    "A": re.compile(r"^[0-9]{6}$"),
    "HK": re.compile(r"^[0-9]{5}$"),
}


@dataclass(frozen=True, slots=True)
class SecurityId:
    market: str
    code: str

    def __post_init__(self) -> None:
        pattern = (
            _SECURITY_PATTERNS.get(self.market)
            if isinstance(self.market, str)
            else None
        )
        if (
            pattern is None
            or not isinstance(self.code, str)
            or pattern.fullmatch(self.code) is None
        ):
            raise ValueError(f"invalid security id: {self.market}:{self.code}")

    @classmethod
    def parse(cls, raw: str) -> "SecurityId":
        try:
            market, code = raw.split(":", maxsplit=1)
        except ValueError:
            raise ValueError(f"invalid security id: {raw}") from None
        return cls(market=market, code=code)

    def __str__(self) -> str:
        return f"{self.market}:{self.code}"


def _require_decimal(value: Decimal, name: str) -> None:
    try:
        canonical_decimal_string(value)
    except TypeError as error:
        raise TypeError(f"{name} must be a Decimal") from error
    except ValueError as error:
        raise ValueError(f"{name} must be finite") from error


def _require_optional_decimal(value: Decimal | None, name: str) -> None:
    if value is not None:
        _require_decimal(value, name)


def _require_date(value: date, name: str) -> None:
    if type(value) is not date:
        raise TypeError(f"{name} must be a date")


def _require_optional_date(value: date | None, name: str) -> None:
    if value is not None:
        _require_date(value, name)


def _require_optional_datetime(value: datetime | None, name: str) -> None:
    if value is not None and type(value) is not datetime:
        raise TypeError(f"{name} must be a datetime or None")


def _require_literal(value: str, allowed: set[str], name: str) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {name}: {value}")


def _require_tuple(value: object, name: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    return value


def _require_tuple_elements(
    value: object, name: str, element_type: type[object], element_name: str
) -> tuple[object, ...]:
    items = _require_tuple(value, name)
    for item in items:
        if type(item) is not element_type:
            raise TypeError(f"{name} entries must be {element_name}")
    return items


def _require_string_or_none_tuple(value: object, name: str) -> tuple[object, ...]:
    items = _require_tuple(value, name)
    for item in items:
        if item is not None and type(item) is not str:
            raise TypeError(f"{name} entries must be str or None")
    return items


def _require_coverage(value: object, name: str) -> None:
    for interval in _require_tuple(value, name):
        if (
            type(interval) is not tuple
            or len(interval) != 2
            or type(interval[0]) is not date
            or type(interval[1]) is not date
        ):
            raise TypeError(f"{name} entries must contain dates")


@dataclass(frozen=True, slots=True)
class DecodedPortfolioFile:
    format_id: Literal["guotai-snapshot-v1", "guotai-delivery-v1"]
    parser_version: str
    file_sha256: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        _require_literal(
            self.format_id,
            {"guotai-snapshot-v1", "guotai-delivery-v1"},
            "format_id",
        )
        _require_tuple_elements(self.header, "header", str, "str")
        for row in _require_tuple(self.rows, "rows"):
            if type(row) is not tuple:
                raise TypeError("rows entries must be tuple")
            _require_tuple_elements(row, "rows entries", str, "str")


@dataclass(frozen=True, slots=True)
class SourceRow:
    line_number: int
    row_sha256: str
    fact_key: str
    fields: tuple[str | None, ...]
    transaction_fingerprint: str | None
    contract_fingerprint: str | None
    usable: bool

    def __post_init__(self) -> None:
        _require_string_or_none_tuple(self.fields, "fields")


@dataclass(frozen=True, slots=True)
class ParseWarning:
    warning_type: str
    import_id: str | None
    line_number: int | None
    impact_from: date | None
    impact_through: date | None
    message: str
    affects_quantity: bool

    def __post_init__(self) -> None:
        _require_optional_date(self.impact_from, "impact_from")
        _require_optional_date(self.impact_through, "impact_through")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    import_id: str | None
    line_number: int
    fact_key: str
    role: str


@dataclass(frozen=True, slots=True)
class Posting:
    role: Literal["security", "cash", "fee"]
    effective_date: date
    amount: Decimal
    unit: Literal["shares", "currency"]
    currency: str | None
    source_field: str

    def __post_init__(self) -> None:
        _require_literal(self.role, {"security", "cash", "fee"}, "role")
        _require_date(self.effective_date, "effective_date")
        _require_decimal(self.amount, "amount")
        _require_literal(self.unit, {"shares", "currency"}, "unit")


@dataclass(frozen=True, slots=True)
class PortfolioEvent:
    event_id: str
    event_type: str
    security: SecurityId | None
    trade_date: date | None
    settlement_date: date | None
    completeness: Literal["complete", "partial", "informational", "unclassified"]
    postings: tuple[Posting, ...]
    evidence: tuple[EvidenceRef, ...]
    warnings: tuple[ParseWarning, ...]
    details: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.security is not None and not isinstance(self.security, SecurityId):
            raise TypeError("security must be a SecurityId or None")
        _require_optional_date(self.trade_date, "trade_date")
        _require_optional_date(self.settlement_date, "settlement_date")
        _require_literal(
            self.completeness,
            {"complete", "partial", "informational", "unclassified"},
            "completeness",
        )
        _require_tuple_elements(self.postings, "postings", Posting, "Posting")
        _require_tuple_elements(self.evidence, "evidence", EvidenceRef, "EvidenceRef")
        _require_tuple_elements(self.warnings, "warnings", ParseWarning, "ParseWarning")
        details = _require_tuple(self.details, "details")
        for detail in details:
            if type(detail) is not tuple or len(detail) != 2:
                raise TypeError("details entries must contain strings")
            if type(detail[0]) is not str or type(detail[1]) is not str:
                raise TypeError("details entries must contain strings")
        if details != tuple(sorted(details)):
            raise ValueError("details must be sorted")


@dataclass(frozen=True, slots=True)
class EventBuildResult:
    events: tuple[PortfolioEvent, ...]
    warnings: tuple[ParseWarning, ...]

    def __post_init__(self) -> None:
        _require_tuple_elements(self.events, "events", PortfolioEvent, "PortfolioEvent")
        _require_tuple_elements(self.warnings, "warnings", ParseWarning, "ParseWarning")


@dataclass(frozen=True, slots=True)
class SnapshotPosition:
    security: SecurityId
    broker_name: str
    route: str
    total_profit_loss: Decimal
    profit_loss_percent: Decimal
    quantity: Decimal
    available_quantity: Decimal
    frozen_quantity: Decimal
    reference_cost: Decimal
    reference_cost_currency: str
    market_price: Decimal
    market_price_currency: str
    daily_profit_loss: Decimal
    daily_profit_loss_percent: Decimal
    market_value: Decimal
    market_value_currency: str
    position_weight_percent: Decimal
    same_day_buy: Decimal
    same_day_sell: Decimal
    profit_loss_price: Decimal
    profit_loss_currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.security, SecurityId):
            raise TypeError("security must be a SecurityId")
        for name in (
            "total_profit_loss",
            "profit_loss_percent",
            "quantity",
            "available_quantity",
            "frozen_quantity",
            "reference_cost",
            "market_price",
            "daily_profit_loss",
            "daily_profit_loss_percent",
            "market_value",
            "position_weight_percent",
            "same_day_buy",
            "same_day_sell",
            "profit_loss_price",
        ):
            _require_decimal(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class DeliveryObservation:
    evidence: EvidenceRef
    trade_date: date
    trade_time: time | None
    security: SecurityId | None
    broker_name: str | None
    operation: str
    quantity: Decimal
    transaction_fingerprint: str | None
    price: Decimal
    gross_amount: Decimal
    current_security_balance: Decimal | None
    security_balance: Decimal | None
    available_balance: Decimal | None
    cash_movement: Decimal
    commission: Decimal | None
    stamp_tax: Decimal | None
    misc_fee: Decimal | None
    cash_balance: Decimal | None
    current_cash_amount: Decimal | None
    contract_fingerprint: str | None
    route: str | None
    transfer_fee: Decimal | None
    trade_currency: str | None
    settlement_currency: str | None
    settlement_fx: Decimal | None
    hk_trading_fee: Decimal | None
    currency: str | None
    market_code: str | None

    def __post_init__(self) -> None:
        _require_date(self.trade_date, "trade_date")
        if self.trade_time is not None and type(self.trade_time) is not time:
            raise TypeError("trade_time must be a time or None")
        if self.security is not None and not isinstance(self.security, SecurityId):
            raise TypeError("security must be a SecurityId or None")
        for name in ("quantity", "price", "gross_amount", "cash_movement"):
            _require_decimal(getattr(self, name), name)
        for name in (
            "current_security_balance",
            "security_balance",
            "available_balance",
            "commission",
            "stamp_tax",
            "misc_fee",
            "cash_balance",
            "current_cash_amount",
            "transfer_fee",
            "settlement_fx",
            "hk_trading_fee",
        ):
            _require_optional_decimal(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ParsedPortfolioFile:
    source_type: Literal["snapshot", "delivery_statement"]
    format_id: str
    parser_version: str
    file_sha256: str
    rows: tuple[SourceRow, ...]
    snapshot_positions: tuple[SnapshotPosition, ...]
    delivery_observations: tuple[DeliveryObservation, ...]
    warnings: tuple[ParseWarning, ...]
    observed_on: date | None
    full_snapshot: bool
    coverage_from: date | None
    coverage_through: date | None

    def __post_init__(self) -> None:
        _require_literal(
            self.source_type,
            {"snapshot", "delivery_statement"},
            "source_type",
        )
        _require_optional_date(self.observed_on, "observed_on")
        _require_optional_date(self.coverage_from, "coverage_from")
        _require_optional_date(self.coverage_through, "coverage_through")
        _require_tuple_elements(self.rows, "rows", SourceRow, "SourceRow")
        _require_tuple_elements(
            self.snapshot_positions,
            "snapshot_positions",
            SnapshotPosition,
            "SnapshotPosition",
        )
        _require_tuple_elements(
            self.delivery_observations,
            "delivery_observations",
            DeliveryObservation,
            "DeliveryObservation",
        )
        _require_tuple_elements(self.warnings, "warnings", ParseWarning, "ParseWarning")


@dataclass(frozen=True, slots=True)
class ImportedFacts:
    import_id: str
    import_sequence: int
    parsed: ParsedPortfolioFile
    source_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.parsed, ParsedPortfolioFile):
            raise TypeError("parsed must be a ParsedPortfolioFile")


@dataclass(frozen=True, slots=True)
class SnapshotAnchor:
    snapshot_id: str
    import_id: str
    import_sequence: int
    observed_on: date
    full_snapshot: bool
    positions: tuple[SnapshotPosition, ...]

    def __post_init__(self) -> None:
        _require_date(self.observed_on, "observed_on")
        _require_tuple_elements(
            self.positions, "positions", SnapshotPosition, "SnapshotPosition"
        )


@dataclass(frozen=True, slots=True)
class ReconciledPortfolio:
    snapshots: tuple[SnapshotAnchor, ...]
    events: tuple[PortfolioEvent, ...]
    warnings: tuple[ParseWarning, ...]
    reported_coverage: tuple[tuple[date, date], ...]
    import_ids: tuple[str, ...]
    source_revisions: tuple[tuple[str, str], ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        _require_tuple_elements(
            self.snapshots, "snapshots", SnapshotAnchor, "SnapshotAnchor"
        )
        _require_tuple_elements(self.events, "events", PortfolioEvent, "PortfolioEvent")
        _require_tuple_elements(self.warnings, "warnings", ParseWarning, "ParseWarning")
        _require_coverage(self.reported_coverage, "reported_coverage")
        _require_tuple_elements(self.import_ids, "import_ids", str, "str")
        for revision in _require_tuple(self.source_revisions, "source_revisions"):
            _require_tuple_elements(revision, "source_revision", str, "str")
            if len(revision) != 2:
                raise ValueError("source revision must contain import and revision ids")


@dataclass(frozen=True, slots=True)
class ImportSummary:
    status: Literal["preview", "imported", "duplicate"]
    source_type: Literal["snapshot", "delivery_statement"]
    format_id: str
    file_sha256_short: str
    source_rows: int
    usable_rows: int
    new_facts: int
    duplicate_facts: int
    conflicting_facts: int
    events: int
    partial_events: int
    unclassified_events: int
    warnings: tuple[ParseWarning, ...]

    def __post_init__(self) -> None:
        _require_literal(self.status, {"preview", "imported", "duplicate"}, "status")
        _require_literal(
            self.source_type,
            {"snapshot", "delivery_statement"},
            "source_type",
        )
        _require_tuple_elements(self.warnings, "warnings", ParseWarning, "ParseWarning")


@dataclass(frozen=True, slots=True)
class Holding:
    security: SecurityId
    broker_name: str
    route: str
    quantity: Decimal
    available_quantity: Decimal | None
    frozen_quantity: Decimal | None
    reference_cost: Decimal | None
    reference_cost_currency: str | None
    market_price: Decimal | None
    market_price_currency: str | None
    market_value: Decimal | None
    market_value_currency: str | None
    total_profit_loss: Decimal | None
    profit_loss_currency: str | None
    profit_loss_percent: Decimal | None
    daily_profit_loss: Decimal | None
    daily_profit_loss_percent: Decimal | None
    position_weight_percent: Decimal | None
    same_day_buy: Decimal | None
    same_day_sell: Decimal | None
    profit_loss_price: Decimal | None
    latest_quote_price: Decimal | None = None
    latest_quote_currency: str | None = None
    quote_as_of: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.security, SecurityId):
            raise TypeError("security must be a SecurityId")
        _require_decimal(self.quantity, "quantity")
        for name in (
            "available_quantity",
            "frozen_quantity",
            "reference_cost",
            "market_price",
            "market_value",
            "total_profit_loss",
            "profit_loss_percent",
            "daily_profit_loss",
            "daily_profit_loss_percent",
            "position_weight_percent",
            "same_day_buy",
            "same_day_sell",
            "profit_loss_price",
            "latest_quote_price",
        ):
            _require_optional_decimal(getattr(self, name), name)
        _require_optional_datetime(self.quote_as_of, "quote_as_of")


@dataclass(frozen=True, slots=True)
class PortfolioView:
    as_of: date
    anchor_date: date | None
    direction: Literal["exact", "partial_snapshot", "forward", "reverse"]
    holdings: tuple[Holding, ...]
    reported_coverage: tuple[tuple[date, date], ...]
    completeness: Literal["authoritative", "reported_coverage", "incomplete"]
    warnings: tuple[ParseWarning, ...]

    def __post_init__(self) -> None:
        _require_date(self.as_of, "as_of")
        _require_optional_date(self.anchor_date, "anchor_date")
        _require_literal(
            self.direction,
            {"exact", "partial_snapshot", "forward", "reverse"},
            "direction",
        )
        _require_literal(
            self.completeness,
            {"authoritative", "reported_coverage", "incomplete"},
            "completeness",
        )
        _require_tuple_elements(self.holdings, "holdings", Holding, "Holding")
        _require_coverage(self.reported_coverage, "reported_coverage")
        _require_tuple_elements(self.warnings, "warnings", ParseWarning, "ParseWarning")


@dataclass(frozen=True, slots=True)
class CurrencyMovement:
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        _require_decimal(self.amount, "amount")


@dataclass(frozen=True, slots=True)
class LatestQuote:
    price: Decimal
    currency: str
    observed_at: datetime | None

    def __post_init__(self) -> None:
        _require_decimal(self.price, "price")
        _require_optional_datetime(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class TradeFilters:
    date_from: date | None = None
    date_through: date | None = None
    market: Literal["A", "HK"] | None = None
    security_id: SecurityId | None = None
    event_type: str | None = None
    completeness: (
        Literal["complete", "partial", "informational", "unclassified"] | None
    ) = None

    def __post_init__(self) -> None:
        _require_optional_date(self.date_from, "date_from")
        _require_optional_date(self.date_through, "date_through")
        if self.market is not None:
            _require_literal(self.market, {"A", "HK"}, "market")
        if self.security_id is not None and not isinstance(
            self.security_id, SecurityId
        ):
            raise TypeError("security_id must be a SecurityId or None")
        if self.completeness is not None:
            _require_literal(
                self.completeness,
                {"complete", "partial", "informational", "unclassified"},
                "completeness",
            )


@dataclass(frozen=True, slots=True)
class TradeItem:
    id: str
    trade_date: date | None
    settlement_date: date | None
    security: SecurityId | None
    name: str | None
    event_type: str
    operation_label: str
    security_quantity: Decimal | None
    trade_currency: str | None
    cash_movements: tuple[CurrencyMovement, ...]
    completeness: str
    warnings: tuple[ParseWarning, ...]

    def __post_init__(self) -> None:
        _require_optional_date(self.trade_date, "trade_date")
        _require_optional_date(self.settlement_date, "settlement_date")
        if self.security is not None and not isinstance(self.security, SecurityId):
            raise TypeError("security must be a SecurityId or None")
        _require_optional_decimal(self.security_quantity, "security_quantity")
        _require_tuple_elements(
            self.cash_movements,
            "cash_movements",
            CurrencyMovement,
            "CurrencyMovement",
        )
        _require_tuple_elements(self.warnings, "warnings", ParseWarning, "ParseWarning")


@dataclass(frozen=True, slots=True)
class ImportHistoryItem:
    completed_at: datetime | None
    source_type: str
    format_id: str
    source_filename: str
    file_sha256_short: str
    row_count: int
    observed_on: date | None
    coverage_from: date | None
    coverage_through: date | None
    status: Literal["publishing", "imported", "failed"]
    warning_count: int

    def __post_init__(self) -> None:
        _require_optional_datetime(self.completed_at, "completed_at")
        _require_optional_date(self.observed_on, "observed_on")
        _require_optional_date(self.coverage_from, "coverage_from")
        _require_optional_date(self.coverage_through, "coverage_through")
        _require_literal(self.status, {"publishing", "imported", "failed"}, "status")


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    page: int
    page_size: int
    total: int

    def __post_init__(self) -> None:
        _require_tuple(self.items, "items")
