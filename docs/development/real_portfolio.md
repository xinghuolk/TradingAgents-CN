# Real Portfolio Imports

Real portfolio imports require the existing authenticated TradingAgents-CN
backend and MongoDB. V1 supports personal use with **one backend worker** and
the fixed account alias `main`. Account imports use a process-local lock;
multiple backend workers or replicas are not supported for imports. The default
Docker standalone MongoDB works without replica sets or transactions.

## Import And Read

Log in to the existing application and open **Import History** at
`/portfolio/imports`. Upload a supported Guotai Haitong position snapshot or
delivery statement. These broker `.xls` exports are GB18030 tab-separated text,
not arbitrary Excel workbooks; recognition uses their exact ordered headers.

- Snapshot imports require the broker snapshot date (`as_of`), for example
  `2026-09-06`. Use the date the broker snapshot actually represents.
- Delivery imports derive dates from their rows; do not supply `as_of`.
- Retrying identical bytes is safe. After successful publication with current
  parser and derived versions, the response is `duplicate`, and import history
  retains one row per file. Failed, interrupted, or outdated imports resume
  using that same import identity. Reusing snapshot bytes with a different
  date returns a conflict and preserves the original date.
- Usable rows are retained with sanitized warnings when individual rows are
  malformed, unknown, or incomplete. Review warnings and completeness before
  relying on a result. No usable rows means the import is rejected.

**Real Holdings** (`/portfolio/real-holdings`) defaults to the latest full broker
snapshot date and shows its anchor and completeness. Historical reconstruction
can be partial where delivery coverage is missing or warnings affect quantity.
It is not a complete tax-lot, realized profit, or historical return system.
**Real Transactions** (`/portfolio/real-transactions`) lists normalized records.
Normal APIs do not expose source rows, raw broker identifiers, their internal
fingerprints, private archive paths, or file downloads.

Real holdings and the simulated account at `/paper` remain separate. Real data
uses `real_portfolio_*` MongoDB collections; simulated data uses `paper_*`.
There is **no order placement, broker connection, live account synchronization,
or automatic trading capability**. Analysis suggestions remain suggestions.

## Deployment And Archives

Use the repository's normal authenticated backend configuration. For a local
backend connecting to the default Docker MongoDB, configure `MONGODB_HOST`,
`MONGODB_PORT=37017`, `MONGODB_USERNAME`, `MONGODB_PASSWORD`,
`MONGODB_AUTH_SOURCE=admin`, and `MONGODB_DATABASE` according to `.env.example`.
Container connections use the `mongodb` service on port `27017`. Retain the
existing Redis and authentication configuration and a private `JWT_SECRET`.
No additional portfolio database or broker credentials are needed.

Source archives live below `${TRADINGAGENTS_DATA_DIR}/private/real_portfolio`.
The existing Docker `./data:/app/data` mount persists them; configure
`TRADINGAGENTS_DATA_DIR=/app/data` inside the container (the normal relative
`./data` resolves there when the working directory is `/app`). Grant the backend
write access and retain private permissions: directories `0700`, files `0600`
where POSIX permissions are supported. User namespaces are hashed and filenames
are content hashes. Never publish or serve this directory as static content.

Startup creates the required real portfolio indexes. Imports return service
unavailable if required uniqueness guarantees cannot be initialized; inspect
server logs and restore the required indexes before retrying. Read endpoints
may continue using the last complete generation. Publication writes and
validates a complete generation before switching the account's active pointer,
so interrupted work does not replace the last readable holdings and trades.
Do not manually edit import records or active generation pointers.

## Backup And Recovery

A backup **must include both MongoDB and the private archive directory**.
Quiesce imports by stopping the single backend worker during a coordinated
database and filesystem backup, then restart it. Back up the application
database, including every `real_portfolio_*` collection, and
`${TRADINGAGENTS_DATA_DIR}/private/real_portfolio` with permissions preserved.
Protect both backups as private financial data. A MongoDB-only backup omits
source evidence; an archive-only backup omits account ownership and publication
state. Restore the matching database and archive backup together, retain the
same authenticated user identities, then start one backend worker and verify
import history, holdings, and transactions. Retry interrupted files through
the import screen; do not delete persisted facts to force a reimport.

## Verification

```bash
python -m pytest -c tests/pytest.ini tests/unit/real_portfolio tests/unit/test_real_portfolio_router.py -v
docker compose up -d mongodb
REAL_PORTFOLIO_TEST_MONGO_URI='mongodb://admin:tradingagents123@localhost:37017/?authSource=admin' python -m pytest -c tests/pytest.ini tests/integration/test_real_portfolio_mongo.py -m integration -v
```

The URI above is the repository's local development default; supply your own
credentials when changed. The integration test skips clearly if the variable
is unset. It requires a standalone server and creates a unique
`real_portfolio_test_<uuid>` database, then drops only that exact database in
`finally`. It checks required indexes, snapshot and delivery publication,
duplicate handling, holdings and trades, incomplete-generation isolation, and
unchanged synthetic `paper_*` records. Source fixtures are synthetic; archives
are written to pytest's temporary directory. Never use private broker exports
in shared test logs or screenshots.
