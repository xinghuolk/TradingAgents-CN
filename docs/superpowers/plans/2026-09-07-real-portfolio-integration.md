# Real Portfolio Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import Guotai Haitong position snapshots and delivery statements into TradingAgents-CN, then expose reliable real holdings, normalized transactions, and import history inside the existing authenticated application.

**Architecture:** Port the proven immutable parsing and ledger behavior from `../stock` into a focused `app.services.real_portfolio` package. Persist redacted source evidence in MongoDB and publish snapshots plus normalized events with a copy-then-switch derived generation so an interrupted import cannot replace the last readable portfolio. Keep the V1 product fixed to the authenticated user's `main` real account and keep all real collections separate from `paper_*` data.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, Motor/PyMongo, pytest/pytest-asyncio, Vue 3, TypeScript, Element Plus, Axios.

**Spec:** `docs/superpowers/specs/2026-09-06-real-portfolio-integration-design.md`

## Global Constraints

- Read the spec and the relevant `../stock/src/stock/portfolio/` source before implementing each porting task.
- V1 supports only `guotai-snapshot-v1` and `guotai-delivery-v1` exact ordered headers decoded with strict GB18030.
- Keep personal-use imports forgiving at row level: collect sanitized warnings and continue with usable rows; reject only the file-level failures listed in spec Section 8. Do not add approval workflows, warning acknowledgements, confidence thresholds, or extra confirmation gates.
- V1 always uses `account_alias="main"`; do not add account-management UI or free-form archive path components.
- The supported runtime is the repository's default single-worker backend with standalone MongoDB; do not require MongoDB transactions, a replica set, or a distributed lock.
- Keep every imported numeric fact as `Decimal` in domain code and serialize it as a canonical decimal string; never use binary floats for broker facts.
- Fingerprint transaction and contract identifiers before persistence and never return raw identifiers, raw source rows, archive paths, or internal event ids from normal APIs.
- Store real data only in `real_portfolio_*` collections; do not read from or write to `paper_*` collections.
- Build a new complete derived generation and atomically switch `active_derived_generation`; all portfolio reads use only that generation.
- Preserve analysis output labels as “分析建议” or “决策草稿”; do not present AI output as a user-approved decision.
- Do not add order placement, broker connectivity, live synchronization, manual editing, tax-lot accounting, or realized-PnL calculation.
- Reuse `settings.MAX_UPLOAD_SIZE`, `settings.TRADINGAGENTS_DATA_DIR`, `get_mongo_db`, `get_current_user`, and the existing `ok(...)` envelope.
- Preserve unrelated worktree changes. In particular, do not stage or modify the existing `uv.lock`, `.codex/`, `Untitled`, or unrelated `docs/analysis/` files.

## File Map And Locked Interfaces

Create this backend package:

```text
app/services/real_portfolio/
  __init__.py          public service exports
  errors.py            stable domain error codes
  models.py            immutable Decimal-based domain records
  formats.py           strict GB18030 decoding and exact format dispatch
  snapshot.py          position-snapshot parser
  delivery.py          delivery-row parser and redacted serialization
  events.py            deterministic event/posting construction
  holdings.py          pure snapshot-anchored reconstruction
  reconciliation.py   cross-import fact selection and generation building
  archive.py           private exact-byte archive
  storage.py           Motor indexes, import state, warnings, generations, and queries
  quotes.py            optional current-quote enrichment
  service.py           import orchestration and read use cases
```

The following function and method names are contracts across tasks:

- `decode_portfolio_file(content: bytes) -> DecodedPortfolioFile`
- `parse_portfolio_file(content: bytes, as_of: date | None = None) -> ParsedPortfolioFile`
- `parse_snapshot_v1(decoded: DecodedPortfolioFile, as_of: date) -> ParsedPortfolioFile`
- `parse_delivery_v1(decoded: DecodedPortfolioFile) -> ParsedPortfolioFile`
- `build_delivery_events(observations: Sequence[DeliveryObservation]) -> EventBuildResult`
- `reconcile_imports(*, user_id: str, account_alias: str, imports: Sequence[ImportedFacts]) -> ReconciledPortfolio`
- `build_portfolio_view(portfolio: ReconciledPortfolio, as_of: date) -> PortfolioView`
- `archive_portfolio_bytes(root: Path, user_id: str, content: bytes) -> ArchiveResult`
- `ensure_real_portfolio_indexes(db: AsyncIOMotorDatabase) -> None`
- `RealPortfolioService.preview_file(*, user_id: str, filename: str, content: bytes, as_of: date | None) -> ImportSummary`
- `RealPortfolioService.import_file(*, user_id: str, filename: str, content: bytes, as_of: date | None) -> ImportSummary`
- `RealPortfolioService.get_positions(*, user_id: str, as_of: date | None) -> PortfolioView`
- `RealPortfolioService.list_trades(*, user_id: str, filters: TradeFilters, page: int, page_size: int) -> Page[TradeItem]`
- `RealPortfolioService.list_imports(*, user_id: str, page: int, page_size: int) -> Page[ImportHistoryItem]`

---

### Task 1: Immutable Portfolio Domain Model

**Files:**
- Create: `app/services/real_portfolio/__init__.py`
- Create: `app/services/real_portfolio/errors.py`
- Create: `app/services/real_portfolio/models.py`
- Create: `tests/unit/real_portfolio/__init__.py`
- Create: `tests/unit/real_portfolio/fixtures.py`
- Create: `tests/unit/real_portfolio/test_models.py`

**Interfaces:**
- Consumes: Python standard-library `dataclasses`, `datetime`, `decimal`, `enum`, and typing APIs only.
- Produces: `SecurityId`, `DecodedPortfolioFile`, `SourceRow`, `ParseWarning`, `EvidenceRef`, `Posting`, `PortfolioEvent`, `EventBuildResult`, `SnapshotPosition`, `DeliveryObservation`, `ParsedPortfolioFile`, `ImportSummary`, `Holding`, `SnapshotAnchor`, `ImportedFacts`, `ReconciledPortfolio`, `PortfolioView`, `CurrencyMovement`, `LatestQuote`, `TradeFilters`, `TradeItem`, `ImportHistoryItem`, `Page[T]`, and `PortfolioError`.

- [ ] **Step 1: Add synthetic broker fixtures with no personal data**

Copy the header and anonymous row constants from `../stock/tests/portfolio_fixtures.py` into `tests/unit/real_portfolio/fixtures.py`. Keep these builders exact:

```python
def portfolio_bytes(
    header: tuple[str, ...], rows: tuple[tuple[str, ...], ...], line_ending: str = "\n"
) -> bytes:
    lines = ("\t".join(header), *("\t".join(row) for row in rows))
    return (line_ending.join(lines) + line_ending).encode("gb18030")


def snapshot_bytes(*rows: tuple[str, ...]) -> bytes:
    selected = rows or (SNAPSHOT_ROW,)
    return portfolio_bytes(SNAPSHOT_HEADER, tuple(selected))


def delivery_bytes(*rows: tuple[str, ...]) -> bytes:
    selected = rows or (DELIVERY_ROW,)
    return portfolio_bytes(DELIVERY_HEADER, tuple(selected))


def replace_cells(
    header: tuple[str, ...], row: tuple[str, ...], changes: dict[str, str]
) -> tuple[str, ...]:
    cells = list(row)
    for name, value in changes.items():
        cells[header.index(name)] = value
    return tuple(cells)


def snapshot_row(**changes: str) -> tuple[str, ...]:
    return replace_cells(SNAPSHOT_HEADER, SNAPSHOT_ROW, changes)


def delivery_row(**changes: str) -> tuple[str, ...]:
    return replace_cells(DELIVERY_HEADER, DELIVERY_ROW, changes)


def hk_pair() -> tuple[tuple[str, ...], tuple[str, ...]]:
    execution = delivery_row(
        **{"证券代码": "00700", "市场名称": "沪HK", "交易币种": "HKD", "发生金额": "0"}
    )
    settlement = delivery_row(
        **{
            "成交日期": "2026-09-03",
            "证券代码": "00700",
            "市场名称": "沪HK",
            "交易币种": "HKD",
            "发生金额": "-901.23",
        }
    )
    return execution, settlement
```

- [ ] **Step 2: Write failing model-invariant tests**

Add tests proving security ids, immutable tuples, finite decimals, canonical serialization, and error codes:

```python
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import SecurityId, canonical_decimal_string


def test_security_id_accepts_supported_markets_only():
    assert str(SecurityId("A", "600519")) == "A:600519"
    assert str(SecurityId("HK", "00700")) == "HK:00700"
    with pytest.raises(ValueError, match="invalid security id"):
        SecurityId("US", "AAPL")


def test_security_id_is_immutable():
    security = SecurityId("A", "600519")
    with pytest.raises(FrozenInstanceError):
        security.code = "000001"


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_decimal_serializer_rejects_non_finite_values(value):
    with pytest.raises(ValueError, match="finite"):
        canonical_decimal_string(value)


def test_portfolio_error_has_stable_safe_code():
    error = PortfolioError("SNAPSHOT_DATE_REQUIRED", "snapshot imports require as_of")
    assert error.code == "SNAPSHOT_DATE_REQUIRED"
    assert error.message == "snapshot imports require as_of"
    assert error.context == {}
```

- [ ] **Step 3: Run the model tests and confirm the red state**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_models.py -v`

Expected: FAIL during import because `app.services.real_portfolio.models` and `errors` do not exist.

- [ ] **Step 4: Implement the immutable records and error type**

Port the validation helpers and immutable records from `../stock/src/stock/portfolio/models.py` and `../stock/src/stock/security.py`. Keep domain markets as `A` and `HK`; API conversion to `CN` belongs in Task 8. Update `ImportSummary` to the approved counters:

```python
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
```

Add the persistence/reconstruction records with immutable tuple fields:

```python
@dataclass(frozen=True, slots=True)
class ImportedFacts:
    import_id: str
    import_sequence: int
    parsed: ParsedPortfolioFile


@dataclass(frozen=True, slots=True)
class SnapshotAnchor:
    snapshot_id: str
    import_id: str
    import_sequence: int
    observed_on: date
    full_snapshot: bool
    positions: tuple[SnapshotPosition, ...]


@dataclass(frozen=True, slots=True)
class ReconciledPortfolio:
    snapshots: tuple[SnapshotAnchor, ...]
    events: tuple[PortfolioEvent, ...]
    warnings: tuple[ParseWarning, ...]
    reported_coverage: tuple[tuple[date, date], ...]
    import_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CurrencyMovement:
    currency: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class LatestQuote:
    price: Decimal
    currency: str
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class TradeFilters:
    date_from: date | None = None
    date_through: date | None = None
    market: Literal["A", "HK"] | None = None
    security_id: SecurityId | None = None
    event_type: str | None = None
    completeness: Literal["complete", "partial", "informational", "unclassified"] | None = None


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


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    page: int
    page_size: int
    total: int
```

Extend the ported `Holding` record with nullable `latest_quote_price`,
`latest_quote_currency`, and `quote_as_of` fields. These current quote fields
remain separate from snapshot market value and PnL.

Implement `PortfolioError` as a small exception carrying `code`, safe `message`, and a copied `context: dict[str, str]`. Do not store the original exception, raw row, path, transaction id, or contract id in it.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_models.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the domain foundation**

```bash
git add app/services/real_portfolio/__init__.py app/services/real_portfolio/errors.py app/services/real_portfolio/models.py tests/unit/real_portfolio/__init__.py tests/unit/real_portfolio/fixtures.py tests/unit/real_portfolio/test_models.py
git commit -m "feat(portfolio): add real portfolio domain models"
```

---

### Task 2: Exact Format Detection And Snapshot Parsing

**Files:**
- Create: `app/services/real_portfolio/formats.py`
- Create: `app/services/real_portfolio/snapshot.py`
- Create: `tests/unit/real_portfolio/test_formats.py`
- Create: `tests/unit/real_portfolio/test_snapshot.py`

**Interfaces:**
- Consumes: Task 1 domain records and synthetic fixtures.
- Produces: `PARSER_VERSION`, `decode_portfolio_file`, `fingerprint_identifier`, `parse_portfolio_file`, and `parse_snapshot_v1` using the locked signatures.

- [ ] **Step 1: Write exact-decoding tests**

Cover both exact headers, LF/CRLF byte hashes, strict GB18030 rejection, missing trailing header cell, header-only files, blank rows, and delivery `as_of` rejection. The central assertions are:

```python
def test_decode_requires_exact_header_and_preserves_source_hash():
    content = snapshot_bytes()
    decoded = decode_portfolio_file(content)
    assert decoded.format_id == "guotai-snapshot-v1"
    assert decoded.header == SNAPSHOT_HEADER
    assert decoded.file_sha256 == sha256(content).hexdigest()


def test_decode_requires_trailing_empty_header_cell():
    content = portfolio_bytes(SNAPSHOT_HEADER[:-1], (SNAPSHOT_ROW[:-1],))
    with pytest.raises(PortfolioError) as captured:
        decode_portfolio_file(content)
    assert captured.value.code == "UNSUPPORTED_FORMAT"


def test_delivery_rejects_as_of():
    with pytest.raises(PortfolioError) as captured:
        parse_portfolio_file(delivery_bytes(), as_of=date(2026, 9, 6))
    assert captured.value.code == "DELIVERY_AS_OF_NOT_ALLOWED"
```

- [ ] **Step 2: Write snapshot parser tests**

Port the cases from `../stock/tests/test_portfolio_snapshot.py`: A/H market mapping, all 15 numeric fields, HK snapshot price in HKD while cost/value/PnL remain CNY, malformed rows, non-empty trailing cell, duplicate security exclusion, and mandatory `as_of`.

```python
AS_OF = date(2026, 9, 6)


def test_duplicate_security_makes_snapshot_partial_and_excludes_both_rows():
    duplicate = snapshot_row(**{"股票余额": "101"})
    parsed = parse_portfolio_file(snapshot_bytes(SNAPSHOT_ROW, duplicate), as_of=AS_OF)
    assert parsed.snapshot_positions == ()
    assert parsed.full_snapshot is False
    assert parsed.warnings[0].affects_quantity is True


def test_hk_snapshot_keeps_mixed_currency_semantics():
    row = snapshot_row(**{"证券代码": "02476", "交易市场": "沪HK"})
    position = parse_portfolio_file(snapshot_bytes(row), as_of=AS_OF).snapshot_positions[0]
    assert str(position.security) == "HK:02476"
    assert position.market_price_currency == "HKD"
    assert position.reference_cost_currency == "CNY"
    assert position.market_value_currency == "CNY"
```

- [ ] **Step 3: Run the parser tests and confirm failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_formats.py tests/unit/real_portfolio/test_snapshot.py -v`

Expected: FAIL because the format and snapshot functions are not implemented.

- [ ] **Step 4: Port strict decoding and dispatch**

Implement `formats.py` from `../stock/src/stock/portfolio/formats.py`, replacing raw `UnicodeDecodeError`/`ValueError` at the public boundary with stable `PortfolioError` codes:

```python
def decode_portfolio_file(content: bytes) -> DecodedPortfolioFile:
    try:
        text = content.decode("gb18030", errors="strict")
    except UnicodeDecodeError:
        raise PortfolioError("INVALID_ENCODING", "file must be valid GB18030 text") from None
    rows = tuple(tuple(row) for row in csv.reader(io.StringIO(text, newline=""), delimiter="\t"))
    if not rows or rows[0] not in FORMAT_BY_HEADER:
        raise PortfolioError("UNSUPPORTED_FORMAT", "unsupported portfolio file header")
    header, *body = rows
    return DecodedPortfolioFile(
        format_id=FORMAT_BY_HEADER[header],
        parser_version=PARSER_VERSION,
        file_sha256=sha256(content).hexdigest(),
        header=header,
        rows=tuple(body),
    )
```

- [ ] **Step 5: Port snapshot parsing**

Port `../stock/src/stock/portfolio/snapshot.py` without loosening numeric or row-width behavior. `parse_portfolio_file` must detect the source type before applying `as_of` rules and must raise `NO_USABLE_ROWS` when every non-empty row is unusable.

```python
def parse_portfolio_file(content: bytes, as_of: date | None = None) -> ParsedPortfolioFile:
    decoded = decode_portfolio_file(content)
    if decoded.format_id == "guotai-snapshot-v1":
        if as_of is None:
            raise PortfolioError(
                "SNAPSHOT_DATE_REQUIRED",
                "snapshot imports require as_of",
                {"source_type": "snapshot"},
            )
        parsed = parse_snapshot_v1(decoded, as_of)
    else:
        if as_of is not None:
            raise PortfolioError(
                "DELIVERY_AS_OF_NOT_ALLOWED",
                "delivery statements take dates from source rows",
            )
        parsed = parse_delivery_v1(decoded)
    if not parsed.rows or not any(row.usable for row in parsed.rows):
        raise PortfolioError("NO_USABLE_ROWS", "portfolio file contains no usable data rows")
    return parsed
```

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_formats.py tests/unit/real_portfolio/test_snapshot.py -v`

Expected: PASS.

```bash
git add app/services/real_portfolio/formats.py app/services/real_portfolio/snapshot.py tests/unit/real_portfolio/test_formats.py tests/unit/real_portfolio/test_snapshot.py
git commit -m "feat(portfolio): parse broker position snapshots"
```

---

### Task 3: Delivery Parsing And Deterministic Event Construction

**Files:**
- Create: `app/services/real_portfolio/delivery.py`
- Create: `app/services/real_portfolio/events.py`
- Create: `tests/unit/real_portfolio/test_delivery.py`
- Create: `tests/unit/real_portfolio/test_events.py`

**Interfaces:**
- Consumes: `DecodedPortfolioFile`, `DeliveryObservation`, `EvidenceRef`, `Posting`, `PortfolioEvent`, and `ParseWarning` from Task 1.
- Produces: `parse_delivery_v1`, `delivery_observation_to_document`, `delivery_observation_from_document`, and `build_delivery_events`.

- [ ] **Step 1: Write delivery parsing and redaction tests**

Port the row-level cases from `../stock/tests/test_portfolio_delivery_trades.py`. Assert exact `Decimal` fields, normalized dates, coverage from all known dates, transaction/contract fingerprints, and `None` at raw identifier cell indexes 6 and 18.

```python
def test_delivery_redacts_identifiers_before_persistence_shape():
    parsed = parse_portfolio_file(delivery_bytes())
    source = parsed.rows[0]
    observation = parsed.delivery_observations[0]
    assert source.fields[6] is None
    assert source.fields[18] is None
    assert source.transaction_fingerprint == observation.transaction_fingerprint
    assert source.contract_fingerprint == observation.contract_fingerprint
    assert observation.cash_movement == Decimal("-1001")
```

- [ ] **Step 2: Write event behavior tests**

Port the full operation matrix from `../stock/tests/test_portfolio_delivery_operations.py` and the HK pairing cases from `test_portfolio_delivery_trades.py`. Required cases are A-share buy/sell, HK execution plus settlement, missing execution, missing settlement, fees, dividends, interest, tax, transfer cash, reverse repo open/close, bond subscription/listing, share adjustments, and unknown operations.

Define the local row helpers used by the examples:

```python
def parse_delivery_rows(*rows: tuple[str, ...]) -> ParsedPortfolioFile:
    return parse_portfolio_file(delivery_bytes(*rows))


def unknown_row() -> tuple[str, ...]:
    return delivery_row(**{"操作": "未识别操作", "成交数量": "3"})
```

```python
def test_hk_execution_and_settlement_become_one_complete_event():
    parsed = parse_delivery_rows(*hk_pair())
    result = build_delivery_events(parsed.delivery_observations)
    assert len(result.events) == 1
    event = result.events[0]
    assert event.completeness == "complete"
    assert event.trade_date == date(2026, 9, 1)
    assert event.settlement_date == date(2026, 9, 3)
    assert [posting.currency for posting in event.postings if posting.role == "cash"] == ["CNY"]


def test_unknown_operation_is_preserved_without_postings():
    result = build_delivery_events(parse_delivery_rows(unknown_row()).delivery_observations)
    assert result.events[0].completeness == "unclassified"
    assert result.events[0].postings == ()
    assert result.warnings[0].affects_quantity is True
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_delivery.py tests/unit/real_portfolio/test_events.py -v`

Expected: FAIL because delivery parsing and event construction are absent.

- [ ] **Step 4: Port the parser into `delivery.py`**

Keep `_normalized_row`, strict decimal/date/time parsing, known-date coverage, fingerprint domains, and document round-tripping from `../stock/src/stock/portfolio/delivery.py`. The public serialization functions must return BSON-safe dictionaries with canonical decimal and ISO date/time strings:

```python
def scalar(value: object) -> object:
    if isinstance(value, Decimal):
        return canonical_decimal_string(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, SecurityId):
        return str(value)
    return value


def delivery_observation_to_document(observation: DeliveryObservation) -> dict[str, object]:
    document = {
        field.name: scalar(getattr(observation, field.name))
        for field in fields(observation)
        if field.name != "evidence"
    }
    document["evidence"] = asdict(observation.evidence)
    return document
```

- [ ] **Step 5: Move event construction into `events.py`**

Port event logic without changing its identities. Cash postings always use CNY from `发生金额`; `trade_currency` remains event detail. Namespace event ids later in reconciliation, after cross-import matching.

```python
def build_delivery_events(
    observations: Sequence[DeliveryObservation],
) -> EventBuildResult:
    trade_result = build_trade_events(observations)
    trade_evidence = {
        (ref.import_id, ref.line_number, ref.fact_key)
        for event in trade_result.events
        for ref in event.evidence
    }
    remaining = tuple(
        observation
        for observation in observations
        if (
            observation.evidence.import_id,
            observation.evidence.line_number,
            observation.evidence.fact_key,
        ) not in trade_evidence
    )
    operation_result = build_operation_events(remaining)
    events = tuple(sorted(trade_result.events + operation_result.events, key=lambda item: item.event_id))
    warnings = tuple(warning for event in events for warning in event.warnings)
    return EventBuildResult(events=events, warnings=warnings)
```

- [ ] **Step 6: Verify all portfolio parsing tests and commit**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_models.py tests/unit/real_portfolio/test_formats.py tests/unit/real_portfolio/test_snapshot.py tests/unit/real_portfolio/test_delivery.py tests/unit/real_portfolio/test_events.py -v`

Expected: PASS.

```bash
git add app/services/real_portfolio/delivery.py app/services/real_portfolio/events.py tests/unit/real_portfolio/test_delivery.py tests/unit/real_portfolio/test_events.py
git commit -m "feat(portfolio): normalize broker delivery events"
```

---

### Task 4: Cross-Import Reconciliation And Pure Holdings Reconstruction

**Files:**
- Create: `app/services/real_portfolio/reconciliation.py`
- Create: `app/services/real_portfolio/holdings.py`
- Create: `tests/unit/real_portfolio/test_reconciliation.py`
- Create: `tests/unit/real_portfolio/test_holdings.py`

**Interfaces:**
- Consumes: ordered `ImportedFacts` from persistence and event builders from Task 3.
- Produces: `reconcile_imports(*, user_id, account_alias, imports) -> ReconciledPortfolio` and `build_portfolio_view(portfolio, as_of) -> PortfolioView`.

- [ ] **Step 1: Write fact-reconciliation tests**

Test equal facts merging evidence, conflicting facts selecting greatest immutable `(import_sequence, line_number)`, query-order independence, HK settlement completion across imports, and account-scoped event ids.

Define this local builder at the top of `test_reconciliation.py` so every example is executable without hidden fixtures:

```python
def imported_delivery(
    *, import_sequence: int, cash_movement: str = "-1001.00"
) -> ImportedFacts:
    row = delivery_row(**{"发生金额": cash_movement})
    return ImportedFacts(
        import_id=f"delivery-{import_sequence}",
        import_sequence=import_sequence,
        parsed=parse_portfolio_file(delivery_bytes(row)),
    )
```

```python
def test_conflicting_fact_winner_is_independent_of_input_order():
    older = imported_delivery(import_sequence=1, cash_movement="-1001")
    newer = imported_delivery(import_sequence=2, cash_movement="-1002")
    forward = reconcile_imports(
        user_id="user-1", account_alias="main", imports=(older, newer)
    )
    reverse = reconcile_imports(
        user_id="user-1", account_alias="main", imports=(newer, older)
    )
    assert forward.events == reverse.events
    assert any(w.warning_type == "source_fact_replaced" for w in forward.warnings)


def test_overlapping_equal_fact_keeps_both_evidence_refs_once():
    first = imported_delivery(import_sequence=1)
    second = imported_delivery(import_sequence=2)
    result = reconcile_imports(
        user_id="user-1", account_alias="main", imports=(first, second)
    )
    assert len(result.events) == 1
    assert len(result.events[0].evidence) == 2
```

- [ ] **Step 2: Write holdings and completeness tests**

Port timeline coverage from `../stock/tests/test_portfolio_timeline.py`. Cover exact full anchor, latest prior anchor, earliest later anchor, forward and reverse interval boundaries, same-day replacement by greatest `import_sequence`, exact partial snapshot only, adjacent coverage merging, disjoint gaps, and quantity-affecting warning intersection.

Define the two builders used below in `test_holdings.py`; they deliberately exercise the public parser and reconciler rather than manually constructing a partial domain graph:

```python
def portfolio_with_snapshot_and_trade(
    *, snapshot_date: str, snapshot_quantity: str, trade_date: str, trade_quantity: str
) -> ReconciledPortfolio:
    snapshot = parse_portfolio_file(
        snapshot_bytes(snapshot_row(**{"股票余额": snapshot_quantity})),
        as_of=date.fromisoformat(snapshot_date),
    )
    trade = parse_portfolio_file(
        delivery_bytes(
            delivery_row(**{"成交日期": trade_date, "成交数量": trade_quantity})
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
        as_of=date.fromisoformat(snapshot_date),
    )
    return reconcile_imports(
        user_id="user-1",
        account_alias="main",
        imports=(ImportedFacts("partial-1", 1, parsed),),
    )
```

```python
def test_forward_reconstruction_uses_open_closed_posting_interval():
    portfolio = portfolio_with_snapshot_and_trade(
        snapshot_date="2026-09-01",
        snapshot_quantity="100",
        trade_date="2026-09-02",
        trade_quantity="20",
    )
    view = build_portfolio_view(portfolio, date(2026, 9, 2))
    assert view.direction == "forward"
    assert view.holdings[0].quantity == Decimal("120")
    assert view.holdings[0].available_quantity is None
    assert view.holdings[0].reference_cost is None


def test_exact_partial_snapshot_never_implies_absent_security_is_zero():
    portfolio = portfolio_with_partial_snapshot("2026-09-01")
    exact = build_portfolio_view(portfolio, date(2026, 9, 1))
    assert exact.direction == "partial_snapshot"
    assert exact.completeness == "incomplete"
    with pytest.raises(PortfolioError, match="no full portfolio snapshot"):
        build_portfolio_view(portfolio, date(2026, 9, 2))
```

- [ ] **Step 3: Run tests and confirm failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_reconciliation.py tests/unit/real_portfolio/test_holdings.py -v`

Expected: FAIL because reconciliation and holdings functions do not exist.

- [ ] **Step 4: Implement deterministic reconciliation**

Build current observations by `fact_key`. Equal hashes merge all evidence; conflicting hashes select greatest `(import_sequence, line_number)` and add a sanitized `source_fact_replaced` warning. Build events from selected observations, then namespace each event id:

```python
def namespace_event_id(user_id: str, account_alias: str, event_id: str) -> str:
    value = f"real-portfolio-event-v1\0{user_id}\0{account_alias}\0{event_id}"
    return sha256(value.encode("utf-8")).hexdigest()
```

Materialize every parsed snapshot as a `SnapshotAnchor`, merge delivery coverage intervals, and sort every output tuple before constructing `ReconciledPortfolio`.

- [ ] **Step 5: Implement pure holdings reconstruction**

Select the exact/latest-prior/earliest-later full anchor exactly as specified. Apply only security postings in `(low_date, high_date]`, reversing their sign for a later anchor. For non-anchor dates, copy only security identity, name, route, and reconstructed quantity; set available quantity and snapshot cost/value/PnL fields to `None`.

```python
def build_portfolio_view(portfolio: ReconciledPortfolio, as_of: date) -> PortfolioView:
    anchor = select_snapshot(portfolio.snapshots, as_of)
    if anchor.observed_on == as_of:
        return exact_snapshot_view(anchor, portfolio)
    direction = "forward" if anchor.observed_on < as_of else "reverse"
    holdings = apply_security_postings(anchor, portfolio.events, as_of, direction)
    completeness, warnings = evaluate_coverage(anchor, as_of, portfolio)
    return PortfolioView(
        as_of=as_of,
        anchor_date=anchor.observed_on,
        direction=direction,
        holdings=holdings,
        reported_coverage=portfolio.reported_coverage,
        completeness=completeness,
        warnings=warnings,
    )
```

- [ ] **Step 6: Verify and commit**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_reconciliation.py tests/unit/real_portfolio/test_holdings.py -v`

Expected: PASS.

```bash
git add app/services/real_portfolio/reconciliation.py app/services/real_portfolio/holdings.py tests/unit/real_portfolio/test_reconciliation.py tests/unit/real_portfolio/test_holdings.py
git commit -m "feat(portfolio): rebuild snapshot anchored holdings"
```

---

### Task 5: Private Archive And Mongo Persistence Primitives

**Files:**
- Create: `app/services/real_portfolio/archive.py`
- Create: `app/services/real_portfolio/storage.py`
- Create: `tests/unit/real_portfolio/test_archive.py`
- Create: `tests/unit/real_portfolio/test_storage.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: Task 1 records, Motor database from `get_mongo_db`, `settings.TRADINGAGENTS_DATA_DIR`, and the fixed `main` account.
- Produces: `ArchiveResult`, `ImportReservation`, `GenerationManifest`, `archive_portfolio_bytes`, `RealPortfolioRepository`, `ensure_real_portfolio_indexes`, and startup readiness flag `app.state.real_portfolio_import_ready`.

- [ ] **Step 1: Write archive tests**

Use `tmp_path` only. Test exact bytes, SHA-256 filename, stable hashed user directory, exclusive create, existing-byte verification, POSIX modes when available, and cleanup of a newly created file.

```python
def test_archive_uses_server_generated_components_and_exact_bytes(tmp_path):
    content = snapshot_bytes()
    result = archive_portfolio_bytes(tmp_path, "user@example.com", content)
    assert result.created is True
    assert result.path.read_bytes() == content
    assert result.path.name == f"{sha256(content).hexdigest()}.xls"
    assert "user@example.com" not in result.path.parts


def test_existing_archive_must_match_content(tmp_path):
    first = archive_portfolio_bytes(tmp_path, "user-1", snapshot_bytes())
    first.path.write_bytes(b"different")
    with pytest.raises(PortfolioError) as captured:
        archive_portfolio_bytes(tmp_path, "user-1", snapshot_bytes())
    assert captured.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
```

- [ ] **Step 2: Write repository/index tests with AsyncMock collections**

Assert all required index keys and unique flags, immutable sequence reservation, duplicate import reload, BSON-safe source documents, import-owned parse warnings, generation-scoped derived warnings/snapshot/event writes, active-generation query filters, and completed-at history sort.

```python
@pytest.mark.asyncio
async def test_active_reads_always_include_generation_filter():
    accounts = MagicMock()
    accounts.find_one = AsyncMock(return_value={
        "active_derived_generation": "generation-2"
    })
    events = MagicMock()
    events.find.return_value.to_list = AsyncMock(return_value=[])
    collections = {
        "real_portfolio_accounts": accounts,
        "real_portfolio_snapshots": MagicMock(),
        "real_portfolio_snapshot_positions": MagicMock(),
        "real_portfolio_events": events,
        "real_portfolio_postings": MagicMock(),
        "real_portfolio_event_evidence": MagicMock(),
        "real_portfolio_warnings": MagicMock(),
    }
    for name, collection in collections.items():
        if name != "real_portfolio_accounts" and name != "real_portfolio_events":
            collection.find.return_value.to_list = AsyncMock(return_value=[])
    db = MagicMock()
    db.__getitem__.side_effect = collections.__getitem__

    repository = RealPortfolioRepository(db)
    await repository.load_active_portfolio(user_id="user-1", account_alias="main")

    events.find.assert_called_once_with(
        {
            "user_id": "user-1",
            "account_alias": "main",
            "derived_generation": "generation-2",
        }
    )
```

- [ ] **Step 3: Run tests and confirm failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_archive.py tests/unit/real_portfolio/test_storage.py -v`

Expected: FAIL because archive and repository code do not exist.

- [ ] **Step 4: Implement exact-byte archive behavior**

Implement `ArchiveResult(path: Path, created: bool)` and archive below `root/private/real_portfolio/<user-key>/main/<sha>.xls`. Derive `user-key` from `sha256(user_id.encode("utf-8"))`, use `os.open(..., O_CREAT | O_EXCL, 0o600)`, verify existing regular-file bytes, and reject symlink ancestors below the configured private root. Never use the upload filename in the path.

- [ ] **Step 5: Implement indexes and repository primitives**

Create named indexes from spec Section 7. Implement these repository methods with explicit user/account filters:

```python
@dataclass(frozen=True, slots=True)
class ImportReservation:
    document: Mapping[str, object]
    created: bool


@dataclass(frozen=True, slots=True)
class GenerationManifest:
    generation: str
    import_ids: tuple[str, ...]
    snapshot_count: int
    position_count: int
    event_count: int
    posting_count: int
    evidence_count: int
    warning_count: int


class RealPortfolioRepository:
    async def reserve_import(
        self,
        *,
        user_id: str,
        account_alias: str,
        source_filename: str,
        parsed: ParsedPortfolioFile,
    ) -> ImportReservation:
        """Atomically return the unique import or create it with one immutable sequence."""

    async def replace_import_facts(
        self,
        *,
        user_id: str,
        account_alias: str,
        import_doc: Mapping[str, object],
        parsed: ParsedPortfolioFile,
    ) -> None:
        """Idempotently replace this import's source rows and parse warnings."""

    async def load_rebuild_imports(
        self, *, user_id: str, account_alias: str, current_import_id: str
    ) -> tuple[ImportedFacts, ...]:
        """Load imported facts plus the complete current publishing import only."""

    async def write_generation(
        self,
        *,
        user_id: str,
        account_alias: str,
        generation: str,
        portfolio: ReconciledPortfolio,
    ) -> GenerationManifest:
        """Write all derived collections and return their expected counts."""

    async def activate_generation(
        self,
        *,
        user_id: str,
        account_alias: str,
        manifest: GenerationManifest,
    ) -> None:
        """Recount the generation, then atomically switch the account pointer."""

    async def find_active_generation_for_import(
        self, *, user_id: str, account_alias: str, import_id: str
    ) -> str | None:
        """Return the active generation containing import_id, if present."""

    async def mark_imported(
        self, *, import_id: str, generation: str, summary: ImportSummary
    ) -> None:
        """Store the successful generation, summary, version, and completion time."""

    async def mark_failed(self, *, import_id: str, error_class: str) -> None:
        """Keep the import resumable while storing only a safe error class."""

    async def load_active_portfolio(
        self, *, user_id: str, account_alias: str
    ) -> ReconciledPortfolio:
        """Load one complete portfolio from the account's active generation."""

    async def list_active_trades(
        self,
        *,
        user_id: str,
        account_alias: str,
        filters: TradeFilters,
        page: int,
        page_size: int,
    ) -> Page[TradeItem]:
        """Return sanitized active-generation trades in stable descending order."""

    async def list_imports(
        self, *, user_id: str, account_alias: str, page: int, page_size: int
    ) -> Page[ImportHistoryItem]:
        """Return account-scoped import history ordered by completion time."""
```

Store `Decimal` and dates as strings, source fields with identifier cells already redacted, and parsed observations as BSON-safe nested documents. Do not use `Decimal128` in V1 because canonical text is the domain/API contract.

Use `real_portfolio_warnings.warning_scope` to distinguish `parse` and
`derived`. Parse warnings are uniquely owned by `import_id`; derived warnings
are owned by `derived_generation`. Add non-unique warning lookup indexes on
`{user_id, account_alias, import_id}` and
`{user_id, account_alias, derived_generation}`. Active reads load parse
warnings only for the generation's `import_ids` and derived warnings only for
the active generation. Inactive-generation cleanup removes only derived
warnings, never import-owned parse warnings.

- [ ] **Step 6: Wire startup index readiness**

After `await init_db()` in `app.main.lifespan`, initialize required indexes without stopping unrelated features:

```python
    try:
        await ensure_real_portfolio_indexes(get_mongo_db())
        app.state.real_portfolio_import_ready = True
    except Exception:
        logger.exception("real portfolio indexes are unavailable")
        app.state.real_portfolio_import_ready = False
```

Do not add these indexes to the broad catch-all `create_database_indexes`; keep this feature's readiness explicit.

- [ ] **Step 7: Verify and commit**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_archive.py tests/unit/real_portfolio/test_storage.py -v`

Expected: PASS.

Run: `git check-ignore data/private/real_portfolio/example/main/example.xls`

Expected: prints the path because the existing `data/` rule already ignores it.

```bash
git add app/services/real_portfolio/archive.py app/services/real_portfolio/storage.py app/main.py tests/unit/real_portfolio/test_archive.py tests/unit/real_portfolio/test_storage.py
git commit -m "feat(portfolio): persist real portfolio generations"
```

---

### Task 6: Resumable Import Publication Service

**Files:**
- Create: `app/services/real_portfolio/service.py`
- Create: `tests/unit/real_portfolio/test_import_service.py`

**Interfaces:**
- Consumes: parsing, archive, reconciliation, and `RealPortfolioRepository` from Tasks 2-5.
- Produces: `RealPortfolioService(repository: RealPortfolioRepository, archive_root: Path, quote_service: UnifiedStockService | None = None)`, `preview_file`, and `import_file`; later tasks extend the same class with read methods.

- [ ] **Step 1: Write preview and duplicate tests**

Assert preview has no writes, imported duplicates return the stored summary, same snapshot bytes with a different `as_of` raise `SNAPSHOT_DATE_CONFLICT`, and two awaited imports for one account produce one import identity.

```python
@pytest.mark.asyncio
async def test_preview_never_archives_or_writes(tmp_path):
    repository = AsyncMock(spec=RealPortfolioRepository)
    service = RealPortfolioService(repository, tmp_path)
    summary = await service.preview_file(
        user_id="user-1",
        filename="position.xls",
        content=snapshot_bytes(),
        as_of=date(2026, 9, 6),
    )
    assert summary.status == "preview"
    repository.reserve_import.assert_not_awaited()
    assert list(tmp_path.rglob("*.xls")) == []


@pytest.mark.asyncio
async def test_same_snapshot_bytes_reject_different_date(tmp_path):
    repository = AsyncMock(spec=RealPortfolioRepository)
    repository.reserve_import.return_value = ImportReservation(
        document={
            "import_id": "snapshot-1",
            "status": "imported",
            "observed_on": "2026-09-06",
            "parser_version": PARSER_VERSION,
            "derived_version": DERIVED_VERSION,
        },
        created=False,
    )
    service = RealPortfolioService(repository, tmp_path)
    with pytest.raises(PortfolioError) as captured:
        await service.import_file(
            user_id="user-1",
            filename="position.xls",
            content=snapshot_bytes(),
            as_of=date(2026, 9, 7),
        )
    assert captured.value.code == "SNAPSHOT_DATE_CONFLICT"
    assert captured.value.context == {"observed_on": "2026-09-06"}
```

- [ ] **Step 2: Write failure-boundary and generation tests**

Inject failure before import reservation, after source persistence, during generation write, after activation, and before `mark_imported`. Assert archive cleanup/retention, `failed` state, previous generation visibility, retry behavior, stale `derived_version` rebuild, and no duplicate source rows.

```python
@pytest.mark.asyncio
async def test_generation_failure_keeps_previous_pointer(tmp_path):
    parsed = parse_portfolio_file(delivery_bytes())
    repository = AsyncMock(spec=RealPortfolioRepository)
    repository.reserve_import.return_value = ImportReservation(
        document={"import_id": "delivery-1", "status": "publishing"},
        created=True,
    )
    repository.load_rebuild_imports.return_value = (
        ImportedFacts("delivery-1", 1, parsed),
    )
    repository.find_active_generation_for_import.return_value = None
    repository.write_generation.side_effect = RuntimeError("injected write failure")
    service = RealPortfolioService(repository, tmp_path)
    with pytest.raises(PortfolioError) as captured:
        await service.import_file(
            user_id="user-1",
            filename="delivery.xls",
            content=delivery_bytes(),
            as_of=None,
        )
    assert captured.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
    repository.activate_generation.assert_not_awaited()
    repository.mark_failed.assert_awaited_once()
```

- [ ] **Step 3: Run tests and confirm failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_import_service.py -v`

Expected: FAIL because service orchestration is not implemented.

- [ ] **Step 4: Implement preview summaries**

Set `DERIVED_VERSION = "real-portfolio-v1"` in `service.py`; changing derived identity or reconstruction semantics later requires changing this value. Implement `build_import_summary(import_doc: Mapping[str, object], portfolio: ReconciledPortfolio, status: Literal["preview", "imported", "duplicate"]) -> ImportSummary` and `summary_from_import(import_doc: Mapping[str, object], status: Literal["imported", "duplicate"]) -> ImportSummary`. Build counts from parsed rows and the current completed facts loaded by the repository. `source_rows` counts every non-empty retained row; `usable_rows` counts `row.usable`; `new_facts`, `duplicate_facts`, and `conflicting_facts` use fact key plus normalized row hash. The persisted import document stores the complete successful summary so duplicate and post-activation recovery paths do not recompute counters. Preview must never reserve a sequence, archive bytes, or write Mongo documents.

- [ ] **Step 5: Implement single-worker publication**

Parse and validate before taking the lock so row-level warnings remain ordinary import output and file-level `PortfolioError` codes are preserved. Implement `account_import_lock(user_id: str, account_alias: str) -> asyncio.Lock` with a module-level registry keyed by `(user_id, "main")`. Inside the lock, perform these exact stages:

```python
async with account_import_lock(user_id, "main"):
    reservation = await repository.reserve_import(
        user_id=user_id,
        account_alias="main",
        source_filename=Path(filename).name[:255] or "portfolio.xls",
        parsed=parsed,
    )
    import_doc = reservation.document
    if (
        not reservation.created
        and parsed.source_type == "snapshot"
        and import_doc.get("observed_on") != parsed.observed_on.isoformat()
    ):
        raise PortfolioError(
            "SNAPSHOT_DATE_CONFLICT",
            "snapshot bytes were already imported with a different date",
            {"observed_on": str(import_doc["observed_on"])},
        )
    if (
        import_doc.get("status") == "imported"
        and import_doc.get("parser_version") == PARSER_VERSION
        and import_doc.get("derived_version") == DERIVED_VERSION
    ):
        return summary_from_import(import_doc, status="duplicate")
    recovered_generation = await repository.find_active_generation_for_import(
        user_id=user_id,
        account_alias="main",
        import_id=str(import_doc["import_id"]),
    )
    if recovered_generation is not None:
        summary = summary_from_import(import_doc, status="imported")
        await repository.mark_imported(
            import_id=str(import_doc["import_id"]),
            generation=recovered_generation,
            summary=summary,
        )
        return summary
    archive = archive_portfolio_bytes(archive_root, user_id, content)
    try:
        await repository.replace_import_facts(
            user_id=user_id,
            account_alias="main",
            import_doc=import_doc,
            parsed=parsed,
        )
        inputs = await repository.load_rebuild_imports(
            user_id=user_id,
            account_alias="main",
            current_import_id=str(import_doc["import_id"]),
        )
        portfolio = reconcile_imports(user_id=user_id, account_alias="main", imports=inputs)
        generation = uuid4().hex
        manifest = await repository.write_generation(
            user_id=user_id,
            account_alias="main",
            generation=generation,
            portfolio=portfolio,
        )
        await repository.activate_generation(
            user_id=user_id,
            account_alias="main",
            manifest=manifest,
        )
        summary = build_import_summary(import_doc, portfolio, status="imported")
        await repository.mark_imported(
            import_id=str(import_doc["import_id"]),
            generation=generation,
            summary=summary,
        )
        return summary
    except Exception as error:
        await repository.mark_failed(
            import_id=str(import_doc["import_id"]),
            error_class=type(error).__name__,
        )
        raise PortfolioError(
            "PORTFOLIO_STORAGE_UNAVAILABLE", "portfolio import could not be saved"
        ) from None
```

If activation succeeded but `mark_imported` did not, retry must validate that the active generation includes this `import_id` and complete the status without generating duplicate facts. Cleanup only inactive generations and archives without any import reference.

- [ ] **Step 6: Verify all service cases and commit**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_import_service.py -v`

Expected: PASS, including all injected failure stages.

```bash
git add app/services/real_portfolio/service.py tests/unit/real_portfolio/test_import_service.py
git commit -m "feat(portfolio): publish resumable real imports"
```

---

### Task 7: Read Queries And Optional Quote Enrichment

**Files:**
- Create: `app/services/real_portfolio/quotes.py`
- Modify: `app/services/real_portfolio/storage.py`
- Modify: `app/services/real_portfolio/service.py`
- Create: `tests/unit/real_portfolio/test_queries.py`
- Create: `tests/unit/real_portfolio/test_quotes.py`

**Interfaces:**
- Consumes: active-generation repository data and pure `build_portfolio_view`.
- Produces: `RealPortfolioService.get_positions`, `list_trades`, `list_imports`, and `load_latest_quotes`.

- [ ] **Step 1: Write active-generation query tests**

Test latest-full-snapshot default date, explicit historical date, empty account errors, generation filtering, transaction filters, stable ordering, bounded pagination, sanitized trade items, and import history using `completed_at`.

Define this builder in `test_queries.py`:

```python
def portfolio_with_full_snapshots(*observed_dates: str) -> ReconciledPortfolio:
    imports = tuple(
        ImportedFacts(
            import_id=f"snapshot-{sequence}",
            import_sequence=sequence,
            parsed=parse_portfolio_file(
                snapshot_bytes(), as_of=date.fromisoformat(observed_on)
            ),
        )
        for sequence, observed_on in enumerate(observed_dates, start=1)
    )
    return reconcile_imports(
        user_id="user-1", account_alias="main", imports=imports
    )
```

```python
@pytest.mark.asyncio
async def test_positions_default_to_latest_full_snapshot(tmp_path):
    repository = AsyncMock(spec=RealPortfolioRepository)
    repository.load_active_portfolio.return_value = portfolio_with_full_snapshots(
        "2026-08-31", "2026-09-06"
    )
    service = RealPortfolioService(repository, tmp_path, quote_service=None)
    view = await service.get_positions(user_id="user-1", as_of=None)
    assert view.as_of == date(2026, 9, 6)
    assert view.completeness == "authoritative"


@pytest.mark.asyncio
async def test_trade_page_never_exposes_source_identifiers(tmp_path):
    repository = AsyncMock(spec=RealPortfolioRepository)
    repository.list_active_trades.return_value = Page(
        items=(
            TradeItem(
                id="ui-key",
                trade_date=date(2026, 9, 1),
                settlement_date=None,
                security=SecurityId("A", "000001"),
                name="anonymous",
                event_type="trade",
                operation_label="证券买入",
                security_quantity=Decimal("100"),
                trade_currency="CNY",
                cash_movements=(CurrencyMovement("CNY", Decimal("-1001")),),
                completeness="complete",
                warnings=(),
            ),
        ),
        page=1,
        page_size=50,
        total=1,
    )
    service = RealPortfolioService(repository, tmp_path, quote_service=None)
    page = await service.list_trades(
        user_id="user-1", filters=TradeFilters(), page=1, page_size=50
    )
    serialized = repr(page)
    assert "transaction_fingerprint" not in serialized
    assert "contract_fingerprint" not in serialized
    assert "line_number" not in serialized
```

- [ ] **Step 2: Write quote degradation tests**

Mock `UnifiedStockService.get_stock_quote`. Verify internal `A` maps to API market `CN`, HK remains `HK`, decimal strings are returned, and quote failures leave `latest_quote_price=None` without failing holdings.

```python
@pytest.mark.asyncio
async def test_quote_failure_does_not_fail_holdings():
    quote_service = AsyncMock(spec=UnifiedStockService)
    quote_service.get_stock_quote.side_effect = RuntimeError("provider unavailable")
    quotes = await load_latest_quotes(
        quote_service, (SecurityId("HK", "00700"),)
    )
    assert quotes == {}
```

- [ ] **Step 3: Run tests and confirm failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_queries.py tests/unit/real_portfolio/test_quotes.py -v`

Expected: FAIL because read methods and quote adapter are absent.

- [ ] **Step 4: Implement repository reads and service queries**

All snapshot/event queries first load `real_portfolio_accounts.active_derived_generation` and include it in every derived collection filter. Use Mongo `skip`/`limit` and stable sort for list endpoints. Convert stored canonical strings back to `Decimal` only inside domain records. Implement `latest_full_snapshot_date(snapshots: Sequence[SnapshotAnchor]) -> date` to select the greatest full-snapshot date or raise a safe no-snapshot `PortfolioError`. Implement `attach_latest_quotes(view: PortfolioView, quotes: Mapping[SecurityId, LatestQuote]) -> PortfolioView` with `dataclasses.replace`, changing only the three latest-quote fields on each holding.

```python
async def get_positions(self, *, user_id: str, as_of: date | None) -> PortfolioView:
    portfolio = await self.repository.load_active_portfolio(
        user_id=user_id, account_alias="main"
    )
    requested = as_of or latest_full_snapshot_date(portfolio.snapshots)
    view = build_portfolio_view(portfolio, requested)
    quotes = await load_latest_quotes(self.quote_service, tuple(h.security for h in view.holdings))
    return attach_latest_quotes(view, quotes)
```

- [ ] **Step 5: Implement quote adapter without valuation synthesis**

Use `UnifiedStockService(db).get_stock_quote("CN" or "HK", code)` so the adapter reads existing quote collections and does not trigger a large external fetch. Accept `price`, `current_price`, or `close`, plus `updated_at`/`trade_date`; ignore missing or non-positive values. Do not calculate historical market value or PnL from current quotes.

- [ ] **Step 6: Verify and commit**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_queries.py tests/unit/real_portfolio/test_quotes.py -v`

Expected: PASS.

```bash
git add app/services/real_portfolio/quotes.py app/services/real_portfolio/storage.py app/services/real_portfolio/service.py tests/unit/real_portfolio/test_queries.py tests/unit/real_portfolio/test_quotes.py
git commit -m "feat(portfolio): query real holdings and trades"
```

---

### Task 8: Authenticated FastAPI Contract

**Files:**
- Create: `app/routers/real_portfolio.py`
- Modify: `app/main.py`
- Create: `tests/unit/test_real_portfolio_router.py`

**Interfaces:**
- Consumes: `RealPortfolioService`, `get_mongo_db`, `get_current_user`, `settings`, and `ok`.
- Produces: `/api/real-portfolio/import`, `/positions`, `/trades`, and `/imports` with the exact spec Section 13 shapes.

- [ ] **Step 1: Write router contract tests with a minimal FastAPI app**

Override `get_current_user` and `get_real_portfolio_service`; do not import `app.main` in these unit tests. Cover auth, upload limit, dry run, structured errors, position serialization, trade pagination, import pagination, and fixed account behavior.

```python
def create_test_app(service: AsyncMock, authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    app.state.real_portfolio_import_ready = True
    app.include_router(real_portfolio.router, prefix="/api")
    app.dependency_overrides[real_portfolio.get_real_portfolio_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: {
            "id": "user-1", "username": "personal", "is_admin": False
        }
    return app


def test_snapshot_date_error_is_structured():
    service = AsyncMock(spec=RealPortfolioService)
    service.import_file.side_effect = PortfolioError(
        "SNAPSHOT_DATE_REQUIRED",
        "snapshot imports require as_of",
        {"source_type": "snapshot"},
    )
    with TestClient(create_test_app(service)) as client:
        response = client.post(
            "/api/real-portfolio/import",
            files={"file": ("position.xls", snapshot_bytes())},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "SNAPSHOT_DATE_REQUIRED",
        "message": "snapshot imports require as_of",
        "source_type": "snapshot",
    }
```

- [ ] **Step 2: Run router tests and confirm failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/test_real_portfolio_router.py -v`

Expected: FAIL because the router does not exist.

- [ ] **Step 3: Implement schemas, limited upload reading, and error mapping**

Use `UploadFile = File(...)`, `as_of: date | None = Form(None)`, and `dry_run: bool = Form(False)`. Read at most `MAX_UPLOAD_SIZE + 1` bytes and return `UPLOAD_TOO_LARGE` with HTTP 413 without parsing. Map only these codes:

```python
ERROR_STATUS = {
    "INVALID_ENCODING": 400,
    "UNSUPPORTED_FORMAT": 400,
    "SNAPSHOT_DATE_REQUIRED": 422,
    "DELIVERY_AS_OF_NOT_ALLOWED": 400,
    "SNAPSHOT_DATE_CONFLICT": 409,
    "NO_USABLE_ROWS": 400,
    "UPLOAD_TOO_LARGE": 413,
    "PORTFOLIO_STORAGE_UNAVAILABLE": 503,
}
```

Serialize warnings and all `Decimal` values explicitly; do not rely on FastAPI float coercion.

- [ ] **Step 4: Implement endpoints and router registration**

Use `router = APIRouter(prefix="/real-portfolio", tags=["real-portfolio"])`. Import uses `current_user["id"]` and rejects writes with 503 when `request.app.state.real_portfolio_import_ready` is false. Read endpoints can serve an existing active generation even when startup index creation failed.

Register in `app/main.py` beside the paper router:

```python
from app.routers import real_portfolio as real_portfolio_router

app.include_router(real_portfolio_router.router, prefix="/api", tags=["real-portfolio"])
```

- [ ] **Step 5: Verify router and backend suite**

Run: `python -m pytest -c tests/pytest.ini tests/unit/test_real_portfolio_router.py tests/unit/real_portfolio -v`

Expected: PASS.

Run: `python -m pytest -c tests/pytest.ini tests/ -q`

Expected: PASS under the repository's default marker filters.

- [ ] **Step 6: Commit the API**

```bash
git add app/routers/real_portfolio.py app/main.py tests/unit/test_real_portfolio_router.py
git commit -m "feat(portfolio): expose real portfolio API"
```

---

### Task 9: Frontend API Types, Upload Client, And Formatting

**Files:**
- Create: `frontend/src/api/realPortfolio.ts`
- Create: `frontend/src/views/Portfolio/portfolioFormatters.ts`

**Interfaces:**
- Consumes: Task 8 API shapes and existing `ApiClient`.
- Produces: typed `realPortfolioApi`, error-detail extraction, and shared portfolio formatters.

- [ ] **Step 1: Define exact frontend API types**

Create string-decimal types and mirror every response field. Do not reuse paper types because paper uses JavaScript numbers and different field names.

```typescript
export type DecimalText = string
export type PortfolioCompleteness = 'authoritative' | 'reported_coverage' | 'incomplete'
export type EventCompleteness = 'complete' | 'partial' | 'informational' | 'unclassified'

export interface PortfolioWarning {
  type: string
  message: string
  affects_quantity: boolean
  impact_from: string | null
  impact_through: string | null
}

export interface RealPositionItem {
  security_id: string
  market: 'CN' | 'HK'
  code: string
  name: string
  quantity: DecimalText
  available_quantity: DecimalText | null
  reference_cost: DecimalText | null
  reference_cost_currency: string | null
  broker_market_price: DecimalText | null
  broker_market_price_currency: string | null
  snapshot_market_value: DecimalText | null
  snapshot_market_value_currency: string | null
  snapshot_unrealized_pnl: DecimalText | null
  snapshot_pnl_currency: string | null
  latest_quote_price: DecimalText | null
  latest_quote_currency: string | null
  quote_as_of: string | null
}

export interface CashMovement {
  currency: string
  amount: DecimalText
}

export interface RealTradeItem {
  id: string
  trade_date: string | null
  settlement_date: string | null
  security_id: string | null
  market: 'CN' | 'HK' | null
  code: string | null
  name: string | null
  event_type: string
  operation_label: string
  security_quantity: DecimalText | null
  trade_currency: string | null
  cash_movements: CashMovement[]
  completeness: EventCompleteness
  warnings: PortfolioWarning[]
}

export interface ImportSummary {
  status: 'preview' | 'imported' | 'duplicate'
  source_type: 'snapshot' | 'delivery_statement'
  format_id: string
  file_sha256_short: string
  source_rows: number
  usable_rows: number
  new_facts: number
  duplicate_facts: number
  conflicting_facts: number
  events: number
  partial_events: number
  unclassified_events: number
  warnings: PortfolioWarning[]
}

export interface ImportHistoryItem {
  completed_at: string | null
  source_type: 'snapshot' | 'delivery_statement'
  format_id: string
  source_filename: string
  file_sha256_short: string
  row_count: number
  observed_on: string | null
  coverage_from: string | null
  coverage_through: string | null
  status: 'publishing' | 'imported' | 'failed'
  warning_count: number
}

export interface Page<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export interface PortfolioErrorDetail {
  code: string
  message: string
  source_type?: 'snapshot'
  observed_on?: string
}

export interface TradeQuery {
  date_from?: string
  date_through?: string
  market?: 'CN' | 'HK'
  security_id?: string
  event_type?: string
  completeness?: EventCompleteness
  page?: number
  page_size?: number
}

export interface PositionResponse {
  account_alias: 'main'
  requested_date: string
  anchor_date: string
  direction: 'exact' | 'partial_snapshot' | 'forward' | 'reverse'
  completeness: PortfolioCompleteness
  reported_coverage: Array<{ from: string; through: string }>
  warning_count: number
  warnings: PortfolioWarning[]
  items: RealPositionItem[]
}
```

- [ ] **Step 2: Implement multipart upload and structured-error extraction**

Construct `FormData` directly because `ApiClient.upload` cannot append `as_of` and `dry_run`. Set `skipErrorHandler: true` so the page can handle the structured 422 without an `[object Object]` toast.

```typescript
async importFile(file: File, options: { asOf?: string; dryRun?: boolean }) {
  const body = new FormData()
  body.append('file', file)
  if (options.asOf) body.append('as_of', options.asOf)
  body.append('dry_run', String(options.dryRun ?? false))
  return ApiClient.post<ImportSummary>('/api/real-portfolio/import', body, {
    headers: { 'Content-Type': 'multipart/form-data' },
    skipErrorHandler: true,
    showLoading: true
  })
}
```

Complete the same exported API object with these calls:

```typescript
async getPositions(asOf?: string) {
  return ApiClient.get<PositionResponse>('/api/real-portfolio/positions', {
    as_of: asOf
  })
},
async getTrades(params: TradeQuery) {
  return ApiClient.get<Page<RealTradeItem>>('/api/real-portfolio/trades', params)
},
async getImports(page = 1, pageSize = 50) {
  return ApiClient.get<Page<ImportHistoryItem>>('/api/real-portfolio/imports', {
    page,
    page_size: pageSize
  })
}
```

Export `getPortfolioErrorDetail(error: unknown): PortfolioErrorDetail | null` that reads `AxiosError.response.data.detail` only when it contains string `code` and `message`.

- [ ] **Step 3: Add formatting helpers**

`portfolioFormatters.ts` exports `formatDecimal`, `formatMoney`, `formatDate`, `marketLabel`, `completenessLabel`, and `warningType`. Unknown/null values return `'-'`; functions must not convert decimal strings through arithmetic.

- [ ] **Step 4: Type-check and commit frontend contracts**

Run: `cd frontend && npm run type-check`

Expected: PASS.

```bash
git add frontend/src/api/realPortfolio.ts frontend/src/views/Portfolio/portfolioFormatters.ts
git commit -m "feat(portfolio): add real portfolio frontend client"
```

---

### Task 10: Real Holdings, Transactions, And Import History Views

**Files:**
- Create: `frontend/src/views/Portfolio/RealHoldings.vue`
- Create: `frontend/src/views/Portfolio/RealTransactions.vue`
- Create: `frontend/src/views/Portfolio/ImportHistory.vue`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/components/Layout/SidebarMenu.vue`
- Modify: `frontend/src/views/Stocks/Detail.vue`

**Interfaces:**
- Consumes: `realPortfolioApi` and formatter helpers from Task 9.
- Produces: the complete real portfolio UI, `/portfolio/*` routes, navigation grouping, stock-detail link, snapshot-date retry, and all loading/empty/error states.

- [ ] **Step 1: Build the holdings view**

Use an unframed page header with title “真实持仓”, date picker, and icon refresh button. Under it render a compact `el-descriptions` summary, warning `el-alert`, and one `el-table`. Keep table height stable and use horizontal scrolling for all broker and quote columns.

```typescript
const selectedDate = ref<string>()
const loading = ref(false)
const portfolio = ref<PositionResponse | null>(null)

async function loadPositions() {
  loading.value = true
  try {
    const response = await realPortfolioApi.getPositions(selectedDate.value)
    portfolio.value = response.data
  } finally {
    loading.value = false
  }
}
```

Show snapshot broker fields only when non-null. Label the separately sourced field “最新行情”; never compute value or PnL in the browser. Add links from supported security rows to the existing `StockDetail` route using `row.code`.

- [ ] **Step 2: Build the transactions view**

Use date-range, market, event type, and completeness controls in one compact toolbar. Render operation date, security, operation, signed quantity, CNY settlement cash, trade currency, completion tag, and safe warnings. Pagination changes call the backend and do not paginate client-side.

```typescript
async function loadTrades() {
  loading.value = true
  try {
    const response = await realPortfolioApi.getTrades({
      date_from: filters.value.dateRange?.[0],
      date_through: filters.value.dateRange?.[1],
      market: filters.value.market || undefined,
      event_type: filters.value.eventType || undefined,
      completeness: filters.value.completeness || undefined,
      page: page.value,
      page_size: pageSize.value
    })
    trades.value = response.data.items
    total.value = response.data.total
  } finally {
    loading.value = false
  }
}
```

- [ ] **Step 3: Build import history and upload dialog**

Render completed time, source type, filename, short SHA, row/warning counts, coverage/snapshot date, and state. The primary action opens one upload dialog. Store the `File` object in component state until success or explicit cancel.

```typescript
async function submitImport() {
  if (!selectedFile.value) return
  try {
    const response = await realPortfolioApi.importFile(selectedFile.value, {
      asOf: snapshotDate.value,
      dryRun: false
    })
    ElMessage.success(response.data.status === 'duplicate' ? '文件已导入' : '导入完成')
    closeImportDialog()
    await loadImports()
  } catch (error: unknown) {
    const detail = getPortfolioErrorDetail(error)
    if (detail?.code === 'SNAPSHOT_DATE_REQUIRED') {
      needsSnapshotDate.value = true
      await nextTick()
      snapshotDateInput.value?.focus()
      return
    }
    ElMessage.error(detail?.message || '导入失败')
  }
}
```

Show the snapshot date picker only after `SNAPSHOT_DATE_REQUIRED`. For `SNAPSHOT_DATE_CONFLICT`, display the returned original date. Do not expose delete, edit, archive download, or raw-row actions.

- [ ] **Step 4: Add routes, navigation grouping, and stock-detail link**

Keep the existing `/paper` route unchanged. Add one `/portfolio` route with `BasicLayout` and children for `real-holdings`, `real-transactions`, and `imports`. Replace the standalone paper menu item with an Element Plus `Wallet` submenu containing `/paper` and the three real routes.

```typescript
{
  path: '/portfolio',
  name: 'Portfolio',
  component: () => import('@/layouts/BasicLayout.vue'),
  redirect: '/portfolio/real-holdings',
  meta: { title: '投资组合', icon: 'Wallet', requiresAuth: true },
  children: [
    { path: 'real-holdings', name: 'RealHoldings', component: () => import('@/views/Portfolio/RealHoldings.vue'), meta: { title: '真实持仓', requiresAuth: true } },
    { path: 'real-transactions', name: 'RealTransactions', component: () => import('@/views/Portfolio/RealTransactions.vue'), meta: { title: '真实成交记录', requiresAuth: true } },
    { path: 'imports', name: 'PortfolioImports', component: () => import('@/views/Portfolio/ImportHistory.vue'), meta: { title: '导入记录', requiresAuth: true } }
  ]
}
```

Add an icon button labeled “查看真实持仓” to `Stocks/Detail.vue`. It links by canonical security id, without mutating either portfolio:

```typescript
function openRealHolding() {
  const internalMarket = market.value === 'HK' ? 'HK' : 'A'
  const normalizedCode = internalMarket === 'HK'
    ? symbol.value.padStart(5, '0')
    : symbol.value.padStart(6, '0')
  router.push({
    name: 'RealHoldings',
    query: { security_id: `${internalMarket}:${normalizedCode}` }
  })
}
```

`RealHoldings.vue` reads `route.query.security_id`, filters or highlights the matching row after loading, and still allows clearing that filter to see the entire account.

- [ ] **Step 5: Verify responsive layout and text containment**

Run: `cd frontend && npm run type-check`

Expected: PASS.

Run: `cd frontend && npm run build`

Expected: PASS.

Start the existing frontend dev server during execution and inspect desktop `1440x900` and mobile `390x844`. Verify no toolbar, dialog, table header, tag, or pagination control overlaps; tables scroll horizontally rather than shrinking labels into unreadable text.

- [ ] **Step 6: Commit complete views**

```bash
git add frontend/src/views/Portfolio/RealHoldings.vue frontend/src/views/Portfolio/RealTransactions.vue frontend/src/views/Portfolio/ImportHistory.vue frontend/src/router/index.ts frontend/src/components/Layout/SidebarMenu.vue frontend/src/views/Stocks/Detail.vue
git commit -m "feat(portfolio): add real portfolio views"
```

---

### Task 11: Standalone Mongo Integration, Documentation, And End-To-End Verification

**Files:**
- Create: `tests/integration/test_real_portfolio_mongo.py`
- Create: `docs/development/real_portfolio.md`

**Interfaces:**
- Consumes: completed backend, frontend, default Docker standalone MongoDB, and synthetic broker fixtures.
- Produces: executable persistence proof and operator documentation for the supported personal deployment.

- [ ] **Step 1: Write a real standalone-Mongo integration test**

Use `REAL_PORTFOLIO_TEST_MONGO_URI` and skip with a clear reason when unset. Give each run a unique database name, create indexes, import a snapshot and delivery file, repeat the delivery import, read holdings/trades, inject an incomplete generation, and verify the account pointer remains on the prior complete generation. Drop only the unique test database in `finally`.

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_portfolio_round_trip_on_standalone_mongo(tmp_path):
    uri = os.getenv("REAL_PORTFOLIO_TEST_MONGO_URI")
    if not uri:
        pytest.skip("REAL_PORTFOLIO_TEST_MONGO_URI is not configured")
    client = AsyncIOMotorClient(uri)
    database_name = f"real_portfolio_test_{uuid4().hex}"
    db = client[database_name]
    try:
        paper_collections_before = {
            name for name in await db.list_collection_names() if name.startswith("paper_")
        }
        await ensure_real_portfolio_indexes(db)
        repository = RealPortfolioRepository(db)
        service = RealPortfolioService(repository, tmp_path)
        await service.import_file(
            user_id="user-1",
            filename="position.xls",
            content=snapshot_bytes(),
            as_of=date(2026, 9, 6),
        )
        first = await service.import_file(
            user_id="user-1",
            filename="delivery.xls",
            content=delivery_bytes(),
            as_of=None,
        )
        duplicate = await service.import_file(
            user_id="user-1",
            filename="delivery.xls",
            content=delivery_bytes(),
            as_of=None,
        )
        assert first.status == "imported"
        assert duplicate.status == "duplicate"
        assert (await service.get_positions(user_id="user-1", as_of=date(2026, 9, 6))).holdings
        assert (await service.list_trades(
            user_id="user-1", filters=TradeFilters(), page=1, page_size=50
        )).items

        account_filter = {"user_id": "user-1", "account_alias": "main"}
        active_before = (await db.real_portfolio_accounts.find_one(account_filter))[
            "active_derived_generation"
        ]
        incomplete_generation = uuid4().hex
        await db.real_portfolio_events.insert_one(
            {
                **account_filter,
                "derived_generation": incomplete_generation,
                "event_id": "incomplete-event",
            }
        )
        bad_manifest = GenerationManifest(
            generation=incomplete_generation,
            import_ids=("snapshot-1", "delivery-1"),
            snapshot_count=1,
            position_count=1,
            event_count=1,
            posting_count=1,
            evidence_count=1,
            warning_count=0,
        )
        with pytest.raises(PortfolioError):
            await repository.activate_generation(
                user_id="user-1", account_alias="main", manifest=bad_manifest
            )
        active_after = (await db.real_portfolio_accounts.find_one(account_filter))[
            "active_derived_generation"
        ]
        assert active_after == active_before
        assert {
            name for name in await db.list_collection_names() if name.startswith("paper_")
        } == paper_collections_before
    finally:
        await client.drop_database(database_name)
        client.close()
```

- [ ] **Step 2: Document the supported personal deployment**

Document these exact operational facts in `docs/development/real_portfolio.md`:

- feature requires the existing authenticated backend and MongoDB;
- imports are supported with one backend worker;
- source archives live below `${TRADINGAGENTS_DATA_DIR}/private/real_portfolio` and the existing Docker `./data:/app/data` mount persists them;
- snapshot imports require the broker snapshot date; delivery imports derive dates from rows;
- real and simulated accounts are separate;
- no order placement or broker connection exists;
- backup must include MongoDB plus the private archive directory;
- retrying the same file is safe and returns `duplicate` after successful publication.

- [ ] **Step 3: Run the focused unit suite**

Run: `python -m pytest -c tests/pytest.ini tests/unit/real_portfolio tests/unit/test_real_portfolio_router.py -v`

Expected: PASS.

- [ ] **Step 4: Run against the default standalone MongoDB**

Run: `docker compose up -d mongodb`

Expected: MongoDB health check becomes healthy.

Run: `REAL_PORTFOLIO_TEST_MONGO_URI='mongodb://admin:tradingagents123@localhost:37017/?authSource=admin' python -m pytest -c tests/pytest.ini tests/integration/test_real_portfolio_mongo.py -m integration -v`

Expected: PASS without replica-set configuration.

- [ ] **Step 5: Run full backend and frontend verification**

Run: `python -m pytest -c tests/pytest.ini tests/ -q`

Expected: PASS under default filters.

Run: `cd frontend && npm run type-check`

Expected: PASS.

Run: `cd frontend && npm run build`

Expected: PASS.

- [ ] **Step 6: Perform the authenticated UI smoke**

Start the backend and frontend with the repository commands. Log in, import the synthetic snapshot with `as_of=2026-09-06`, import the synthetic delivery statement, repeat the delivery import, and verify:

1. import history has one row per file and the repeated request reports duplicate;
2. Real Holdings uses the snapshot anchor and shows explicit completeness;
3. Real Transactions contains normalized rows without broker identifiers;
4. `/paper` still shows the unchanged simulated account;
5. desktop and mobile layouts contain no overlapping controls or text.

- [ ] **Step 7: Commit integration proof and documentation**

```bash
git add tests/integration/test_real_portfolio_mongo.py docs/development/real_portfolio.md
git commit -m "test(portfolio): verify standalone mongo workflow"
```

---

## Completion Gate

Before declaring the feature complete:

- [ ] Confirm every task commit contains only its listed files plus necessary lockfile changes caused by an explicitly approved dependency update; this plan requires no new dependency.
- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Run the focused real-portfolio unit and integration commands from Task 11 and record their pass counts.
- [ ] Run the repository backend suite, frontend type-check, and frontend production build and record exact results.
- [ ] Search API fixtures and captured output for `成交编号`, `合同编号`, `transaction_fingerprint`, `contract_fingerprint`, and private archive paths; confirm none are exposed.
- [ ] Confirm no `paper_*` collection changed during the real-portfolio integration test.
- [ ] Request a final code review against `docs/superpowers/specs/2026-09-06-real-portfolio-integration-design.md` before integrating the branch.
