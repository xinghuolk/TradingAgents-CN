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
- `account_alias`: defaults to `main`, reserved for future multiple broker
  accounts.
- `import_id`: generated stable import id.
- `file_sha256`: source bytes identity.
- `security_id`: canonical internal value such as `A:600519` or `HK:00700`.

Indexes:

- unique import identity on `{user_id, account_alias, file_sha256}`;
- source row lookup on `{user_id, account_alias, fact_key}`;
- source row uniqueness on `{import_id, line_number}`;
- snapshots on `{user_id, account_alias, observed_on, import_id}`;
- events on `{user_id, account_alias, security_id}`;
- postings on `{event_id, effective_date}`;
- import history on `{user_id, account_alias, imported_at}`.

Source archive:

- store exact uploaded bytes in a private server-side directory such as
  `data/private/real_portfolio/<user-id>/<account>/<sha256>.xls`;
- create directories with private permissions where the platform supports it;
- never return archive paths, raw source rows, transaction numbers, contract
  numbers, or raw file bytes through normal APIs.

MongoDB writes should be ordered and idempotent. On a duplicate import, return
the existing import summary without duplicating rows or events.

## 8. Import Flow

`POST /api/real-portfolio/import` accepts multipart upload fields:

- `file`: required;
- `account_alias`: optional, default `main`;
- `as_of`: required only for snapshot files;
- `dry_run`: optional boolean, default false.

Flow:

1. Read exact upload bytes and compute SHA-256.
2. Decode and detect format by content.
3. If the file is a snapshot and `as_of` is missing, return a validation error
   that identifies the detected source type.
4. Parse valid rows and collect row-level warnings.
5. Reject only file-level failures: unsupported header, invalid encoding,
   header-only or all-unusable data, archive failure, or database write failure.
6. On dry run, return parse summary and warnings without writing archive or
   MongoDB documents.
7. On import, archive exact bytes, persist source evidence, persist snapshot
   rows when applicable, rebuild current events for the account, and return a
   concise summary.
8. If database publication fails after creating a new archive file, remove that
   newly-created archive file.

Supported source types in V1:

- Guotai Haitong position snapshot V1.
- Guotai Haitong delivery statement V1.

## 9. Holdings Flow

`GET /api/real-portfolio/positions` accepts:

- `account_alias`, default `main`;
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

## 10. Transactions Flow

`GET /api/real-portfolio/trades` accepts:

- `account_alias`, default `main`;
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

The response must not expose event ids unless needed for frontend keys, source
line numbers, fingerprints, archive paths, transaction numbers, contract
numbers, or raw details JSON.

## 11. Import History Flow

`GET /api/real-portfolio/imports` returns:

- import timestamp;
- source type;
- format id;
- source filename;
- file SHA-256 short display value;
- row count;
- coverage dates or observed snapshot date;
- duplicate/imported status;
- warning count.

This page is operational evidence for the user. It is not a raw archive
browser.

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
  reference cost, latest price when available, market value, and PnL fields;
- transaction table with date, security, operation, quantity, cash movement,
  and completeness;
- import dialog or import page with one upload control and snapshot date field;
- visible separation between paper and real data;
- warning bands for incomplete coverage or parse warnings.

Do not use marketing hero sections or a separate standalone Portfolio Web UI.
The feature belongs inside the existing TradingAgents-CN shell.

## 13. API Response Shape

Use the existing `ok(...)` response envelope.

Representative import response:

```json
{
  "status": "imported",
  "source_type": "snapshot",
  "format_id": "guotai-snapshot-v1",
  "file_sha256": "fb35...e1e",
  "source_rows": 18,
  "new_rows": 18,
  "duplicate_rows": 0,
  "events": 0,
  "partial_events": 0,
  "unclassified_events": 0,
  "warnings": []
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
  "market_price": "1600.00",
  "market_price_currency": "CNY",
  "market_value": "160000.00",
  "market_value_currency": "CNY",
  "unrealized_pnl": "10000.00",
  "pnl_currency": "CNY"
}
```

## 14. Error Handling

User-facing errors must be concise and sanitized:

- invalid encoding: file must be valid GB18030 text;
- unsupported format: unsupported portfolio file header;
- missing snapshot date: snapshot imports require `as_of`;
- no usable rows: portfolio file contains no usable data rows;
- duplicate import: return success envelope with `status: duplicate`;
- storage failure: portfolio import could not be saved.

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
- event rebuild after overlapping imports;
- holdings reconstruction from snapshot anchors;
- sanitized API errors;
- real and paper collection separation;
- API auth requirement.

Frontend tests or type checks:

- API wrapper types compile;
- route and menu entries render;
- import form requires snapshot date after source detection;
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

- Add archive handling, Mongo collection writes, duplicate detection, event
  rebuild, and holdings view.

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
