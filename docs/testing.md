# Testing

The repository distinguishes fast, service-free checks from opt-in integration work.

## Service-Free Baseline

Run the canonical repository-map check from the repository root:

```bash
python scripts/harness.py --structural-only
```

The full harness also compiles Python, runs the service-free tests below, and builds
the frontend with the project-local Vite binary:

```bash
python scripts/harness.py
```

GitHub Actions runs this exact command with Python dependencies from
`requirements-harness.txt` and frontend dependencies from `frontend/yarn.lock`.
Docker publishing depends on the same reusable workflow.
The structural phase also checks that the configured console target is a declared
function in a package included by setuptools. A regression test loads that entry
point without initializing the CLI runtime.

Run in a Python 3.11 environment with project dependencies:

```bash
python -m pytest -c tests/pytest.ini tests/config tests/unit/real_portfolio tests/unit/stock_research tests/unit/test_stock_research_router.py tests/harness -q
```

This explicit selection is the current green baseline. It does not need MongoDB,
Redis, network access, market-data credentials, or LLM credentials. Harness regression
tests are included in this selection.
The harness sets `TRADINGAGENTS_LOG_DIR` to a temporary directory, so test imports do
not depend on or modify the repository's runtime logs.

For one change, run the closest test file or node first:

```bash
python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_holdings.py -q
python -m pytest -c tests/pytest.ini tests/unit/real_portfolio/test_holdings.py::test_name -q
```

Research generation and HTTP contracts use injected generators and in-memory
storage, including model selection, user OAuth resolution, immutable originals,
failure compensation, and user-scoped task polling. They do not call a real LLM:

```bash
python -m pytest -c tests/pytest.ini tests/unit/stock_research tests/unit/test_stock_research_router.py -q
```

## Integration Checks

Tests marked `integration` and checks that start the FastAPI application, connect to
MongoDB or Redis, call external market-data providers, or invoke an LLM are opt-in.
Configure the required services and secrets, then select the relevant path explicitly.
Do not add these checks to the personal quick gate.

An explicitly opt-in live research generation smoke requires a running configured
backend, an authenticated bearer token, an existing draft owned by that user, and
configured credentials for the exact enabled model (including user OAuth when
applicable). It creates a persisted generation task and calls that model. Set
`RESEARCH_API_URL`, `RESEARCH_AUTH_TOKEN`, `RESEARCH_DRAFT_ENTRY_ID`,
`RESEARCH_DRAFT_KIND`, `RESEARCH_PROVIDER`, and `RESEARCH_MODEL`, then run:

```bash
python - <<'PY'
import os
import time
import httpx
with httpx.Client(base_url=os.environ['RESEARCH_API_URL'], headers={
    'Authorization': 'Bearer ' + os.environ['RESEARCH_AUTH_TOKEN'],
}, timeout=60) as client:
    response = client.post('/api/research/generation-tasks', json={
        'target_entry_id': os.environ['RESEARCH_DRAFT_ENTRY_ID'],
        'draft_kind': os.environ['RESEARCH_DRAFT_KIND'],
        'provider': os.environ['RESEARCH_PROVIDER'],
        'model_name': os.environ['RESEARCH_MODEL'],
        'reasoning_effort': None, 'references': [],
    })
    response.raise_for_status()
    task = response.json()['data']
    for _ in range(150):
        if task['status'] in {'completed', 'failed'}:
            break
        time.sleep(2)
        response = client.get('/api/research/generation-tasks/' + task['id'])
        response.raise_for_status()
        task = response.json()['data']
    assert task['status'] == 'completed', task.get('error_message') or task['status']
    print(task['id'], task['status'])
PY
```

Open the draft in the browser after completion to inspect the immutable original.
Never put live credentials in fixtures or enable this smoke in the harness.

The broad `tests/` collection is not a reliable gate yet because some legacy modules
perform external work while importing or refer to removed modules. Its current status
is recorded in [technical debt](technical-debt.md).

## Frontend Checks

Research editing has service-free executable checks using the existing TypeScript
compiler and Node assertions. They cover autosave ordering/retry, Markdown safety,
workspace navigation guards, conversion, versions, and trash API calls:

```bash
node frontend/scripts/check-research-editing.mjs
node frontend/scripts/check-research-workspace.mjs
node frontend/scripts/check-research-reviews.mjs
```

The review check exercises minimal decision confirmation and its thesis preview,
manual review creation, explicit formal revisions, cancel/apply thesis changes,
archived read-only boundaries, routed security identity, and draft context association
updates using compiled Vue component scripts.

The actual deployed bundle uses Vite without the legacy type-check pre-step. With
frontend dependencies already installed, run the same package script as Docker:

```bash
npm --prefix frontend run bundle
```

Browser smoke uses native Python Playwright with headless Chromium, intercepted API
fixtures, and the real Vue app. At both `1440x900` and `390x844`, inspect rendered DOM
after `networkidle`, then verify directory search and market/real/paper/watchlist
filters; explicit stock-detail market navigation and non-creating summary reads;
thesis autosave, retry, and manual versions; note/research conversion; minimal
decision confirmation with a thesis snapshot; routine and decision reviews; distinct
real/paper references; confirmed review revisions and cancel/apply thesis diff;
trash restore/delete; configured AI model, effort, and reference selection; failed
generation preserving human text; successful generation retaining original metadata;
and confirmed/archived generation boundaries. Check polling close/unmount/terminal
stops and that retry creates a new task. Capture directory, workspace, decision
confirmation, review diff, and AI metadata screenshots, console/page errors, and
horizontal overflow/control overlap checks at both sizes. No live database or
provider is used in this matrix. On hosts with exhausted file watchers, launch Vite
with `CHOKIDAR_USEPOLLING=1`.

`npm --prefix frontend run lint` is non-mutating, and
`npm --prefix frontend run type-check` only checks types. Both are useful diagnostics
but currently report existing debt. They must become blocking only after their
baselines are green.

## Verified Baseline

On 2026-09-08, the research-updated complete harness passed on the maintainer's
development machine. Python compilation succeeded, 449 tests passed,
and Vite built the production bundle. Three existing Python deprecation warnings and
existing Vite/Sass bundle warnings remain visible but do not fail the gate.
