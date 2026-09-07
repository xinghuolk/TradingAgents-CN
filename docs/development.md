# Development

This is the canonical command reference for the current v1.0.0-preview codebase.

## Toolchain

- Python: 3.11 (`pyproject.toml` requires `>=3.11,<3.12`)
- Node.js: 22, matching `Dockerfile.frontend`
- Frontend package manager in CI and Docker: Yarn 1.22.22 with `yarn.lock`
- Containers: Docker Compose v2 (`docker compose`)

## Setup

Copy `.env.example` to `.env` and add local credentials without committing the file.
For a locked Python environment, use:

```bash
uv sync --frozen
```

The equivalent pip-based environment can be installed from the committed lock export:

```bash
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
```

Install frontend dependencies from the lockfile:

```bash
corepack enable
corepack prepare yarn@1.22.22 --activate
yarn --cwd frontend install --frozen-lockfile
```

Environment creation is a one-time setup step. Daily validation reuses the installed
Python environment and `frontend/node_modules`.

CI installs the narrower pinned `requirements-harness.txt` because the service-free
gate does not need the project's LLM, market-data, or vector-database runtimes.

## Run The Application

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
yarn --cwd frontend dev
python -m cli.main
docker compose up -d
```

The standalone backend expects reachable MongoDB and Redis services. The API also
validates application configuration during startup.

## Current Quality Commands

Until the unified harness lands, the service-free Python baseline is:

```bash
python -m pytest -c tests/pytest.ini tests/config tests/unit/real_portfolio -q
```

The deployed frontend bundle can be checked from the repository root with the package
script shared by the harness and Docker:

```bash
npm --prefix frontend run bundle
```

Frontend lint and type checking are non-mutating diagnostics today because known
legacy errors remain:

```bash
npm --prefix frontend run lint
npm --prefix frontend run type-check
```

Do not run formatting or lint with an automatic fix flag as a general gate.
See [testing](testing.md) for test selection and [technical debt](technical-debt.md)
for the measured baseline.

## Change Workflow

1. Read `AGENTS.md` and the relevant canonical document.
2. Add a focused regression test for behavior changes and observe it fail for the
   intended reason.
3. Make the smallest coherent change and update current documentation with it.
4. Run the focused test and the quick validation path.
5. Commit one independently understandable concern with verification evidence.

When a change touches or fixes an item in `docs/technical-debt.md`, update or remove
that entry in the same commit. Add a service-free regression test to the quick suite
when it protects behavior important enough to retain.

GitHub Actions runs `python scripts/harness.py` for pull requests and pushes to
`main`. Version-tag Docker publication reuses that workflow and cannot begin until it
passes.

The scheduled upstream workflow only detects commits and opens an issue. Apply
upstream changes manually on a reviewable branch; automation must not merge or push
them directly to `main`.
