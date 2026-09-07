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
function in a package included by setuptools.

Run in a Python 3.11 environment with project dependencies:

```bash
python -m pytest -c tests/pytest.ini tests/config tests/unit/real_portfolio -q
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

## Integration Checks

Tests marked `integration` and checks that start the FastAPI application, connect to
MongoDB or Redis, call external market-data providers, or invoke an LLM are opt-in.
Configure the required services and secrets, then select the relevant path explicitly.
Do not add these checks to the personal quick gate.

The broad `tests/` collection is not a reliable gate yet because some legacy modules
perform external work while importing or refer to removed modules. Its current status
is recorded in [technical debt](technical-debt.md).

## Frontend Checks

The actual deployed bundle uses Vite without the legacy type-check pre-step. With
frontend dependencies already installed, run the same package script as Docker:

```bash
npm --prefix frontend run bundle
```

`npm --prefix frontend run lint` is non-mutating, and
`npm --prefix frontend run type-check` only checks types. Both are useful diagnostics
but currently report existing debt. They must become blocking only after their
baselines are green.
