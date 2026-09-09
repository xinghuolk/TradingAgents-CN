# Real Portfolio Integration Design

Date: 2026-09-06
Status: approved for written specification
Scope: merge the personal broker portfolio import flow from `../stock` into TradingAgents-CN as part of the existing product workflow

## 1. Purpose

TradingAgents-CN already covers stock discovery, watchlists, AI analysis,
reports, and paper trading. The adjacent `../stock` workspace adds the missing
real-execution side: importing broker position snapshots and delivery
statements, deriving real holdings, and exposing transaction history for later
review.

These should be one product because they serve the same user, securities, and
workflow:

```text
screening/watchlists
  -> AI and Turtle research
  -> user-approved decision records
  -> paper execution or real broker import
  -> holdings
  -> review
```

The first integration slice adds real broker import and read-only portfolio
views. It does not move TradingAgents-CN into live brokerage, automated
execution, or investment-advice automation.

## 2. Goals

1. Import the Guotai Haitong position snapshot and delivery statement formats
   already supported by `../stock`.
2. Generate real end-of-day holdings from an authoritative broker snapshot and
   dated security postings.
3. Show normalized real transaction records derived from delivery statements.
4. Preserve broker source evidence and idempotent imports without exposing raw
   transaction or contract identifiers in normal API responses.
5. Keep real portfolio data separate from paper trading data.
6. Reuse TradingAgents-CN authentication, MongoDB, API response conventions,
   stock identity conventions, quote services, and Vue application shell.
7. Leave future user-approved decision records and review workflows as explicit
   follow-up work, while designing the data shape so they can link cleanly.

## 3. Non-Goals

- No automatic ordering, broker trading connection, or live account sync.
- No mixing of paper positions and real broker positions in storage or account
  performance.
- No generic broker plugin system in the first slice.
- No arbitrary spreadsheet parser. Detection is still content-based and
  exact-header based for observed Guotai Haitong formats.
- No migration of the full Markdown research workspace from `../stock`.
- No editing, deleting, or manually correcting imported broker facts in V1.
- No tax-lot accounting, complete realized PnL engine, or complete historical
  return series in V1.
- No claim that AI `final_trade_decision` is a user-approved investment
  decision.

## 4. Existing Context

TradingAgents-CN has:

- FastAPI routers under `app/routers/`.
- MongoDB access through `app.core.database.get_mongo_db`.
- authenticated user context through `app.routers.auth_db.get_current_user`.
- paper trading under `app/routers/paper.py`, using `paper_accounts`,
  `paper_positions`, `paper_orders`, and `paper_trades`.
- Vue 3 frontend under `frontend/src`, including `frontend/src/api/paper.ts`
  and `frontend/src/views/PaperTrading/index.vue`.
- a documented workflow goal in
  `docs/development/roadmap/trading_workflow_dev_plan.md`: screening,
  analysis, planning, simulated execution, and review.

The `../stock` project has:

- exact GB18030 TSV content detection for broker `.xls` exports;
- immutable portfolio domain models using `Decimal`;
- parsers for Guotai Haitong snapshot V1 and delivery-statement V1;
- event construction for A-share trades, Hong Kong execution/settlement pairs,
  cash events, dividends, fees, reverse repo, stock adjustments, and unknown
  operations;
- snapshot-anchored historical holdings reconstruction;
- SQLite persistence and CLI entry points.

The integration should port the domain behavior, not the standalone product
shell.

## 5. Product Boundaries

The UI should expose one investment workflow, with clearly separated execution
sources.

Recommended navigation:

```text
Investment / Portfolio
  - Paper Trading
  - Real Holdings
  - Real Transactions
  - Import History
```

The existing `/paper` route remains the simulated account. A new real portfolio
route is added for imported broker data. Shared page labels must make the
source explicit:

- keep `/paper` unchanged for existing links;
- add `/portfolio/real-holdings`;
- add `/portfolio/real-transactions`;
- add `/portfolio/imports`;
- group these entries visually under Investment / Portfolio without creating a
  second `/paper` implementation.

- "模拟交易" for paper trading.
- "真实持仓" for broker-imported holdings.
- "真实成交记录" for normalized imported transaction events.
- "分析建议" or "决策草稿" for AI output.

No page may present AI output as a final user-approved decision.

## 6. Backend Architecture

Add a focused backend package:

```text
app/services/real_portfolio/
  __init__.py
  models.py
  formats.py
  snapshot.py
  delivery.py
  events.py
  holdings.py
  storage.py
  service.py

app/routers/real_portfolio.py
```

The service package ports the proven `../stock/src/stock/portfolio` domain
logic, adjusted for TradingAgents-CN:

- use Python 3.11-compatible type syntax where needed;
- keep `Decimal` internally and serialize canonical strings or explicit
  decimal-safe API fields;
- map `A:600519` to existing CN code conventions and `HK:00700` to existing
  HK conventions at API boundaries;
- keep content detection based on strict GB18030 decoding and exact ordered
  headers with trailing empty cells;
- keep transaction and contract identifiers fingerprinted before persistence;
- expose warnings as sanitized user-facing messages.

The FastAPI router handles authentication, upload validation, request/response
schemas, and HTTP errors. It should not contain parser or reconstruction
business logic.

## 7. Storage Design

Use MongoDB collections under the authenticated user scope. Do not reuse
`paper_*` collections.

Recommended collections:

- `real_portfolio_accounts`
- `real_portfolio_imports`
- `real_portfolio_source_rows`
- `real_portfolio_warnings`
- `real_portfolio_snapshots`
- `real_portfolio_snapshot_positions`
- `real_portfolio_events`
- `real_portfolio_postings`
- `real_portfolio_event_evidence`

Common keys:

- `user_id`: authenticated TradingAgents-CN user id.
- `account_alias`: fixed to `main` in V1. Multiple broker-account management is
  deferred; the backend keeps the field so the schema can evolve without a
  migration.
- `import_id`: generated stable import id.
- `file_sha256`: source bytes identity.
- `security_id`: canonical internal value such as `A:600519` or `HK:00700`.

Required indexes:

- unique account identity on `{user_id, account_alias}`;
- unique import identity across every import status on
  `{user_id, account_alias, file_sha256}`;
- non-unique source fact lookup on `{user_id, account_alias, fact_key}`;
- unique source row identity on `{import_id, line_number}`;
- derived snapshots on
  `{user_id, account_alias, derived_generation, observed_on, import_id}`;
- unique derived snapshot position identity on
  `{snapshot_id, security_id}`;
- unique derived events on
  `{user_id, account_alias, derived_generation, event_id}`;
- postings on
  `{user_id, account_alias, derived_generation, event_id, effective_date}`;
- unique event evidence on `{user_id, account_alias, derived_generation,
  event_id, import_id, line_number, evidence_role}`;
- import history on `{user_id, account_alias, completed_at}`.

`import_id`, `snapshot_id`, and `derived_generation` are generated opaque ids
and are globally unique. Index creation is part of application startup through
a focused `ensure_real_portfolio_indexes(db)` function. If a required unique
index cannot be created, import endpoints return service unavailable rather
than accepting data without their idempotency guarantees. Read-only endpoints
may continue to serve the last active generation.

Source archive:

- store exact uploaded bytes below
  `${TRADINGAGENTS_DATA_DIR}/private/real_portfolio/<user-key>/main/<sha256>.xls`;
- derive `user-key` as a stable SHA-256 namespace of the authenticated user id;
  do not place the raw user id in a path;
- create directories with private permissions where the platform supports it;
- do not accept a user-supplied archive path or filename; the original filename
  is sanitized display metadata only;
- V1 uses the fixed `main` account directory. A later multi-account feature
  must map account ids to server-generated path components rather than placing
  free-form aliases in filesystem paths;
- resolve the configured private root and candidate archive path, then reject
  any path that escapes the private root;
- reject symlink ancestors below the private root before writing;
- write archives with exclusive-create semantics and verify an existing file's
  bytes before treating it as already archived;
- use `0700` directories and `0600` files where the platform supports POSIX
  permissions;
- never return archive paths, raw source rows, transaction numbers, contract
  numbers, or raw file bytes through normal APIs.

### 7.1 Publication and retry

The default TradingAgents-CN Docker deployment uses a standalone MongoDB, so
V1 must not depend on multi-document transactions. The supported deployment is
the repository's default single-worker backend. Imports for the same
`{user_id, account_alias}` are serialized with a process-local asynchronous
lock. The unique import index is still the final protection against a repeated
request or double click. Multi-worker import coordination is outside V1;
deployments that enable this feature must retain a single backend worker.

Each file identity owns exactly one import document. The service atomically
creates that document or loads the existing one and advances it through:

- `publishing`: archive exists or source facts are being written, but derived
  data may be incomplete;
- `imported`: source facts and all current derived data for the account were
  rebuilt with the current `derived_version`;
- `failed`: a previous publication attempt failed after a durable write.

Duplicate handling only returns `status: duplicate` for an `imported` document
whose `parser_version` and `derived_version` match the current code. A request
that finds `publishing`, `failed`, or stale derived data resumes the same import
document instead of creating another one. Import documents record `started_at`,
`import_sequence`, `completed_at`, `parser_version`, `derived_version`,
`attempt`, and the last safe error class when publication fails.
`import_sequence` is assigned once when the import document is first created
from an account-local atomic counter; retry never changes it. Gaps are allowed
when a process stops after reserving a sequence.

Retry writes are deterministic:

- update the existing import document in place;
- upsert source rows by `{import_id, line_number}`;
- replace parse warnings owned by that import;
- never delete another import's source rows or warnings;
- rebuild only from completed imports plus the current fully parsed import;
  unrelated `publishing` or `failed` imports are excluded;
- publish snapshots, snapshot positions, events, postings, and evidence as a
  new `derived_generation`.

Derived publication is copy-then-switch. The service writes and validates a
complete new generation containing both portfolio anchors and transaction
events, then atomically changes
`real_portfolio_accounts.active_derived_generation`. Read endpoints only query
snapshots and events from the active generation. A failed or interrupted
rebuild therefore leaves the previous holdings and transactions visible;
inactive generations may be cleaned up after a successful switch. After the
switch, the service marks the current import `imported`. If the process stops
between those two writes, retry recognizes that the active generation already
contains the import, validates it, and completes the status transition without
publishing duplicate facts.

The source archive and Mongo state follow the same recovery boundary. A newly
created archive may be removed when failure occurs before the import document
is persisted. Once the import document exists, keep the archive and mark the
import `failed` so another upload of the same bytes can resume. Cleanup only
removes an inactive generation or an archive that has no import reference.

### 7.2 Source facts and derived identity

Source rows are observations, so overlapping imports may contain the same
fact. Delivery rows use the transaction fingerprint as `fact_key`; a row with
no usable transaction number falls back to a hash of its complete normalized
content. During rebuild:

- equal `fact_key` and equal normalized row hash become one current fact with
  every source row retained as evidence;
- equal `fact_key` with different normalized content selects the observation
  with the greatest `(import_sequence, line_number)` ordering;
- a conflicting replacement emits a sanitized `source_fact_replaced` warning;
- selection is deterministic and independent of Mongo query order.

Derived event identity is account-scoped and deterministic. Port the
`../stock` identity rules:

- mainland trades and non-trade events use the non-null source fact key;
- complete Hong Kong trade events use the canonical matching-group key, with
  execution date, settlement date, and cash movement excluded so a later
  settlement leg can complete an earlier partial execution;
- unpairable Hong Kong rows without a usable contract use their own fact key so
  unrelated anonymous legs never collide;
- reverse-repo phases use contract fingerprint plus row date and cash-sign
  phase, falling back to row fact key when the contract is unavailable;
- snapshot events, if represented as events, use account, observed date, and
  source-file SHA-256.

The stored `event_id` is namespaced by `user_id` and `account_alias` before
persistence. Rebuilding an account publishes a new derived generation without
deleting source imports, source rows, warning evidence, or the currently active
generation.

## 8. Import Flow

`POST /api/real-portfolio/import` accepts multipart upload fields:

- `file`: required;
- `as_of`: required only for snapshot files;
- `dry_run`: optional boolean, default false.

V1 always imports into the authenticated user's `main` account. Uploads reuse
the existing `settings.MAX_UPLOAD_SIZE` limit; exceeding it returns HTTP 413.
The broker format is detected from exact decoded content, not from extension,
MIME type, or the original filename.

Flow:

1. Read exact upload bytes and compute SHA-256.
2. Decode and detect format by content.
3. If the file is a snapshot and `as_of` is missing, return a validation error
   that identifies the detected source type.
4. If the file is a delivery statement and `as_of` is provided, reject the
   request. Delivery statement dates come only from the source rows.
5. Parse valid rows and collect row-level warnings.
6. Reject only file-level failures: unsupported header, invalid encoding,
   header-only or all-unusable data, archive failure, or database write failure.
7. On dry run, return parse summary and warnings without writing archive or
   MongoDB documents.
8. On import, archive exact bytes, persist source evidence, then publish one
   complete derived generation containing snapshots, snapshot positions,
   events, postings, and evidence for the account.
9. On failure, follow the archive and retry boundary in Section 7.1 and return a
   sanitized error without hiding a previously active derived generation.

Snapshot duplicate behavior is strict. If the same snapshot bytes are already
stored with the same `observed_on`, return `status: duplicate`. If the same
snapshot bytes are submitted with a different `as_of`, return a sanitized
conflict that includes the originally stored `observed_on` and does not create
a second anchor. V1 does not provide manual correction or deletion, so a wrong
snapshot date must not be silently replaced. Delivery-statement duplicate
identity remains `{user_id, account_alias, file_sha256}`.

Different snapshot bytes for the same `observed_on` are allowed because a later
broker export may correct the earlier snapshot. Among full snapshots on the
same date, the greatest `import_sequence` is authoritative and a different
replacement emits `snapshot_replaced`. A partial snapshot never replaces a
full snapshot anchor.

Supported source types in V1:

- Guotai Haitong position snapshot V1.
- Guotai Haitong delivery statement V1.

## 9. Holdings Flow

`GET /api/real-portfolio/positions` accepts:

- `as_of`, optional. If omitted, use the latest full snapshot date.

Result:

- account alias;
- requested date;
- anchor snapshot date;
- reconstruction direction: `exact`, `forward`, `reverse`, or
  `partial_snapshot`;
- completeness: `authoritative`, `reported_coverage`, or `incomplete`;
- holdings list;
- warning summary;
- reported delivery coverage intervals.

Rules:

- On the exact full snapshot date, the snapshot is authoritative.
- For an exact date with multiple full snapshots, use the greatest
  `import_sequence`.
- For any other date, first select the latest full snapshot on or before the
  requested date; if none exists, select the earliest later full snapshot.
- For dates after an anchor, add security postings after the anchor and through
  the requested date.
- For dates before an anchor, subtract security postings after the requested
  date and through the anchor.
- If no full snapshot exists, exact partial snapshot views may show known
  positions as incomplete; other dates return a clear error.
- Do not infer missing execution dates, settlement dates, FX rates, fee splits,
  historical cost basis, or absent securities outside an authoritative
  snapshot.
- Monetary totals are grouped by currency and never added across currencies.
- Snapshot cost, market value, and profit/loss fields are returned only when
  the requested date is the selected snapshot date. A reconstructed date
  returns quantity and security identity, with available quantity and broker
  cost/value/PnL fields set to `null`; transaction prices must not be
  substituted for broker cost.
- The optional latest quote is a separately sourced current-market fact and may
  still be returned on a reconstructed view. V1 does not combine it with a
  historical quantity to manufacture historical market value or PnL.

A snapshot is full only when every non-empty data row parses into one unique
security position without a row-validity warning. A malformed row, invalid
market/code, unexpected non-empty trailing cell, or duplicate security makes
the snapshot partial. Informational metadata warnings alone do not make it
partial. Duplicate securities are warned and excluded from the authoritative
position set so they cannot be silently counted twice.

Delivery coverage is the inclusive minimum and maximum usable row date for
each delivery import. Overlapping or adjacent intervals are merged. For a
reconstructed date, the posting interval is
`(min(anchor, as_of), max(anchor, as_of)]`. Completeness is
`reported_coverage` only when merged delivery intervals cover that posting
interval and no quantity-affecting warning intersects it; otherwise it is
`incomplete`. A warning with a null lower or upper impact bound is unbounded on
that side. The response still states that reported date bounds cannot prove a
broker export was unfiltered.

## 10. Transactions Flow

`GET /api/real-portfolio/trades` accepts:

- date range filters;
- market/security filter;
- event type filter;
- completeness filter;
- pagination with a bounded page size.

The endpoint returns normalized events, not raw broker rows. Each row contains:

- operation date;
- security id and display name when available;
- operation label;
- signed security quantity when present;
- signed cash movement grouped by currency when present;
- completion state;
- warnings that are safe to show.

The response must not expose internal event ids, source line numbers,
fingerprints, archive paths, transaction numbers, contract numbers, or raw
details JSON. It may expose the opaque UI key defined in Section 13.

## 11. Import History Flow

`GET /api/real-portfolio/imports` returns:

- import timestamp;
- source type;
- format id;
- source filename;
- file SHA-256 short display value;
- row count;
- coverage dates or observed snapshot date;
- current publication status (`publishing`, `imported`, or `failed`);
- warning count.

This page is operational evidence for the user. It is not a raw archive
browser. A duplicate request updates or returns the original import and does
not create a second history row.

## 12. Frontend Design

Add:

```text
frontend/src/api/realPortfolio.ts
frontend/src/views/Portfolio/RealHoldings.vue
frontend/src/views/Portfolio/RealTransactions.vue
frontend/src/views/Portfolio/ImportHistory.vue
```

Update:

```text
frontend/src/router/index.ts
frontend/src/components/Layout/SidebarMenu.vue
```

The UI should be dense and operational, matching the existing Element Plus
application:

- top summary for account, date, anchor, completeness, and warning count;
- holdings table with market, code, name, quantity, available quantity,
  reference cost, broker snapshot price/value/PnL, and a separately labeled
  latest quote when available;
- transaction table with date, security, operation, quantity, cash movement,
  and completeness;
- import dialog or import page with one upload control and snapshot date field;
- visible separation between paper and real data;
- warning bands for incomplete coverage or parse warnings.

The snapshot date field may start hidden. When the backend responds with
`SNAPSHOT_DATE_REQUIRED`, the dialog retains the selected file, shows the date
field, and resubmits after the user chooses a date. This keeps format detection
in one backend implementation and does not require the user to classify the
broker file manually.

Do not use marketing hero sections or a separate standalone Portfolio Web UI.
The feature belongs inside the existing TradingAgents-CN shell.

## 13. API Response Shape

Use the existing `ok(...)` response envelope.

All dates are ISO `YYYY-MM-DD`, timestamps are ISO 8601 strings, and decimal
quantities or money are canonical decimal strings. Nullable facts use JSON
`null`; an unknown value is never serialized as zero. A warning has this
common shape:

```json
{
  "type": "coverage_gap",
  "message": "reported delivery intervals do not cover all reconstruction dates",
  "affects_quantity": true,
  "impact_from": "2026-08-01",
  "impact_through": "2026-08-31"
}
```

Representative import `data` payload inside the existing `ok(...)` envelope:

```json
{
  "status": "imported",
  "source_type": "snapshot",
  "format_id": "guotai-snapshot-v1",
  "file_sha256_short": "fb35...e1e",
  "source_rows": 18,
  "usable_rows": 18,
  "new_facts": 18,
  "duplicate_facts": 0,
  "conflicting_facts": 0,
  "events": 0,
  "partial_events": 0,
  "unclassified_events": 0,
  "warnings": []
}
```

`source_rows` is the number of non-empty source rows retained as evidence;
`usable_rows` is the number that produced a snapshot position or delivery
observation. `new_facts` counts previously unseen fact identities,
`duplicate_facts` counts equal facts retained as additional evidence, and
`conflicting_facts` counts replacements accompanied by a
`source_fact_replaced` warning.

Dry run uses the same payload with `status: "preview"` and does not create an
import, archive, or derived generation. Duplicate import uses
`status: "duplicate"` and returns the stored import summary after ensuring its
derived version is current.

`GET /api/real-portfolio/positions` returns this `data` shape:

```json
{
  "account_alias": "main",
  "requested_date": "2026-09-06",
  "anchor_date": "2026-09-06",
  "direction": "exact",
  "completeness": "authoritative",
  "reported_coverage": [{"from": "2026-01-01", "through": "2026-09-06"}],
  "warning_count": 0,
  "warnings": [],
  "items": []
}
```

Representative holding item:

```json
{
  "security_id": "A:600519",
  "market": "CN",
  "code": "600519",
  "name": "贵州茅台",
  "quantity": "100",
  "available_quantity": "100",
  "reference_cost": "1500.00",
  "reference_cost_currency": "CNY",
  "broker_market_price": "1600.00",
  "broker_market_price_currency": "CNY",
  "snapshot_market_value": "160000.00",
  "snapshot_market_value_currency": "CNY",
  "snapshot_unrealized_pnl": "10000.00",
  "snapshot_pnl_currency": "CNY",
  "latest_quote_price": "1612.00",
  "latest_quote_currency": "CNY",
  "quote_as_of": "2026-09-07T15:00:00+08:00"
}
```

`GET /api/real-portfolio/trades` accepts optional ISO `date_from` and
`date_through`, `market`, `security_id`, `event_type`, `completeness`, plus
`page` defaulting to 1 and `page_size` defaulting to 50 with a maximum of 200.
It returns:

```json
{
  "items": [
    {
      "id": "opaque-ui-key",
      "trade_date": "2026-08-12",
      "settlement_date": "2026-08-14",
      "security_id": "HK:00700",
      "market": "HK",
      "code": "00700",
      "name": "腾讯控股",
      "event_type": "trade",
      "operation_label": "证券买入",
      "security_quantity": "100",
      "trade_currency": "HKD",
      "cash_movements": [{"currency": "CNY", "amount": "-35000.00"}],
      "completeness": "complete",
      "warnings": []
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 1
}
```

`cash_movements` comes from the broker's settlement cash field (`发生金额`),
which is CNY in the supported delivery format; `trade_currency` is kept as a
separate fact. `id` is a non-sensitive opaque frontend key. It is stable within
the active derived generation but may change after a `derived_version` change.
It is not a broker transaction or contract identifier, and clients use it only
as a render key.

`GET /api/real-portfolio/imports` accepts `page` and `page_size` with the same
bounds and returns:

```json
{
  "items": [
    {
      "completed_at": "2026-09-06T10:30:00+08:00",
      "source_type": "snapshot",
      "format_id": "guotai-snapshot-v1",
      "source_filename": "position.xls",
      "file_sha256_short": "fb35...e1e",
      "row_count": 18,
      "observed_on": "2026-09-06",
      "coverage_from": null,
      "coverage_through": null,
      "status": "imported",
      "warning_count": 0
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 1
}
```

## 14. Error Handling

User-facing errors must be concise and sanitized:

- `INVALID_ENCODING` (400): file must be valid GB18030 text;
- `UNSUPPORTED_FORMAT` (400): unsupported portfolio file header;
- `SNAPSHOT_DATE_REQUIRED` (422): snapshot imports require `as_of`; include
  `source_type: "snapshot"` so the frontend can retain the selected file,
  reveal the date field, and resubmit;
- `DELIVERY_AS_OF_NOT_ALLOWED` (400): delivery statements take dates from their
  source rows;
- `SNAPSHOT_DATE_CONFLICT` (409): the same bytes were previously stored with a
  different snapshot date; include the original `observed_on`;
- `NO_USABLE_ROWS` (400): portfolio file contains no usable data rows;
- `UPLOAD_TOO_LARGE` (413): file exceeds the configured upload limit;
- duplicate import: return success envelope with `status: duplicate`;
- `PORTFOLIO_STORAGE_UNAVAILABLE` (503): portfolio import could not be saved or
  required indexes are unavailable.

Errors use FastAPI's error response with a structured `detail` containing
`code`, `message`, and only the safe contextual fields listed above.

Do not return raw cells, local filesystem paths, stack traces, database errors,
transaction identifiers, or contract identifiers.

## 15. Testing Strategy

Backend tests:

- exact header detection for both supported formats;
- strict GB18030 decoding and trailing empty-cell preservation;
- snapshot parsing and full/partial snapshot status;
- delivery parsing for A-share trades, Hong Kong execution/settlement pairs,
  fees, dividends, reverse repo, adjustments, unknown operations, and malformed
  rows;
- idempotent duplicate upload behavior;
- repeated or double-clicked upload creates one import document;
- retry from `publishing` and `failed` without duplicate source evidence;
- interrupted derived rebuild keeps the previous active generation readable;
- stale `derived_version` causes an idempotent rebuild;
- archive cleanup before import persistence and archive retention after it;
- event rebuild after overlapping imports;
- source fact conflict selection is independent of Mongo query order;
- holdings reconstruction from snapshot anchors;
- same-date full snapshot replacement, partial snapshot exclusion, delivery
  coverage gaps, and quantity-affecting warning intervals;
- sanitized API errors;
- real and paper collection separation;
- API auth requirement.
- required index initialization and operation with the repository's standalone
  MongoDB deployment.

Frontend tests or type checks:

- API wrapper types compile;
- route and menu entries render;
- import form retains the file and requests a snapshot date after receiving
  `SNAPSHOT_DATE_REQUIRED`;
- holdings and transactions tables render warning and empty states.

Manual smoke:

1. Start backend and frontend.
2. Log in.
3. Import a snapshot with `as_of`.
4. Import a delivery statement.
5. Confirm Real Holdings shows imported positions.
6. Confirm Real Transactions shows normalized events.
7. Confirm Paper Trading data is unchanged.

## 16. Implementation Phases

Phase 1: backend domain port and parser tests.

- Add `app/services/real_portfolio` models, format detection, snapshot parser,
  delivery parser, and event builder.
- Keep code independent from FastAPI route handlers.

Phase 2: Mongo persistence and holdings reconstruction.

- Add archive configuration and ignored private storage, startup indexes,
  import state transitions, idempotent Mongo writes, source fact
  reconciliation, derived-generation publication, and holdings reconstruction.

Phase 3: FastAPI router.

- Add import, positions, transactions, and imports endpoints.
- Register router in `app/main.py`.

Phase 4: frontend.

- Add `realPortfolio.ts`.
- Add real holdings, transaction, and import-history views.
- Update router and sidebar navigation.

Phase 5: workflow links.

- Add non-mutating links from reports or stock detail pages to real holdings
  by symbol.
- Keep AI reports labeled as analysis suggestions.

## 17. Open Extension Points

These are intentionally out of V1 but should remain easy to add:

- user-approved decision records that link analysis reports to future real or
  paper executions;
- review center that detects trades without a linked decision;
- additional broker formats through new exact-header parser functions;
- portfolio analytics, realized PnL, and performance attribution;
- cross-linking imported trades to analysis reports and Turtle payloads.

## 18. Acceptance Criteria

The first integrated slice is acceptable when:

1. A supported Guotai Haitong snapshot can be imported through the
   TradingAgents-CN backend.
2. A supported Guotai Haitong delivery statement can be imported through the
   TradingAgents-CN backend.
3. Duplicate uploads are idempotent.
4. Real holdings can be queried for the latest full snapshot date.
5. Real transactions can be listed without exposing raw broker identifiers.
6. Paper trading collections and real portfolio collections stay separate.
7. Frontend navigation exposes the real portfolio inside TradingAgents-CN.
8. UI copy clearly separates analysis suggestions, paper trading, and real
   broker records.
9. Focused backend tests pass.
10. Frontend type-check or build passes for touched frontend code.
11. The import and read flows work against the repository's default standalone
    MongoDB configuration without requiring a replica set.
12. An interrupted import cannot replace the last active holdings and
    transaction generation with partial data.
