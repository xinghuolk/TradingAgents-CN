# Personal Harnessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the personal maintainer one trustworthy, service-free validation command and repository-owned knowledge that stays aligned with the code.

**Architecture:** Keep `AGENTS.md` and tool-specific instruction files as short maps into canonical development, testing, architecture, and debt documents. A standard-library Python harness runs small structural checks, an explicit pytest selection, Python compilation, and the same Vite bundle used in deployment; GitHub Actions invokes that exact local command.

**Tech Stack:** Python 3.11, pytest 8.4.2, pytest-asyncio 1.2.0, Vue 3, Vite 5, Node 22, Yarn 1.22.22, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-07-personal-harnessing-design.md`

## Global Constraints

- Optimize for one personal maintainer, not enterprise governance.
- The quick harness must require no credentials, MongoDB, Redis, network access, or running application services.
- The normal quick path must target less than two minutes on the maintainer's development machine.
- Keep frontend lint and type checking diagnostic until their recorded legacy failures are cleared.
- Do not add blocking package-boundary or source-file-size checks in this pass.
- Disable automated upstream merges and direct pushes to `main`; retain update detection.
- Every commit uses a Conventional Commit title and `Implementation:` / `Verification:` body sections.

---

### Task 1: Canonical Repository Knowledge

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Create: `ARCHITECTURE.md`
- Modify: `docs/README.md`
- Create: `docs/development.md`
- Create: `docs/testing.md`
- Create: `docs/technical-debt.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: current package layout, `pyproject.toml`, `frontend/package.json`, Dockerfiles, and pytest configuration.
- Produces: canonical files later validated by `scripts/harness.py`.

- [x] **Step 1: Replace duplicated agent guidance with a short map**

Keep `AGENTS.md` and `CLAUDE.md` limited to repository layout, authoritative-document links, the quick command, and security constraints. Commands live only in `docs/development.md`; architecture lives only in `ARCHITECTURE.md`.

- [x] **Step 2: Write current-state architecture and development references**

Document the FastAPI, Vue, CLI, LangGraph, MongoDB/Redis, configuration bridge, worker, and deployment boundaries in `ARCHITECTURE.md`. Record Python 3.11, Node 22, Yarn 1.22.22, install/run commands, and the debt-update rule in `docs/development.md`.

- [x] **Step 3: Replace the stale documentation home**

Make `docs/README.md` identify v1.0.0-preview, link the four canonical references first, and classify the existing versioned documents as historical references. Add a development-guide link to the root README without rewriting user-facing setup.

- [x] **Step 4: Record measured legacy debt**

Create `docs/technical-debt.md` with owners expressed as repository areas, current evidence, impact, and an explicit trigger for revisiting each item: broad pytest collection, frontend type errors, frontend formatting/lint debt, historical broken links, `tradingagents -> app` imports, and oversized production files.

- [x] **Step 5: Verify and commit**

Run:

```bash
rg -n 'TODO|TBD|v0\.1\.12.*current|Python.*3\.10|yarn lint.*--fix' \
  AGENTS.md CLAUDE.md ARCHITECTURE.md docs/README.md \
  docs/development.md docs/testing.md docs/technical-debt.md
git diff --check
```

Expected: no stale-current or placeholder matches; `git diff --check` exits 0.

Commit: `docs(harness): establish canonical repository knowledge`

### Task 2: Tested Structural Checks And Harness Runner

**Files:**
- Create: `scripts/harness.py`
- Create: `tests/harness/__init__.py`
- Create: `tests/harness/test_harness.py`
- Modify: `docs/testing.md`

**Interfaces:**
- Consumes: canonical paths from Task 1 and the repository root derived from `__file__`.
- Produces: `validate_repository(root: Path) -> list[str]`, `build_checks(root: Path) -> list[Check]`, and `main() -> int`.

- [x] **Step 1: Write failing structural-check tests**

Add tests that build a temporary repository map, verify valid relative Markdown links return no errors, then remove one linked file and expect an error naming both the source and missing target. Add a runner test using a real `sys.executable -c` command and assert that a non-zero child status makes `run_checks()` return non-zero without running later checks.

- [x] **Step 2: Verify the tests fail for the missing module**

Run:

```bash
python -m pytest -c tests/pytest.ini tests/harness/test_harness.py -q
```

Expected: collection fails because `scripts.harness` does not exist.

- [x] **Step 3: Implement the minimum harness**

Use `dataclasses.dataclass` for:

```python
@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...]
    cwd: Path
    env: dict[str, str] | None = None
```

Validate local Markdown links only in `AGENTS.md`, `CLAUDE.md`, `ARCHITECTURE.md`, `docs/README.md`, `docs/development.md`, and `docs/testing.md`. Build checks for repository validation, Python compilation with a temporary `PYTHONPYCACHEPREFIX`, the explicit pytest paths, and `npm --prefix frontend run bundle`. Stop on the first failure and print the failed command.

- [x] **Step 4: Verify unit behavior and the structural-only path**

Run:

```bash
python -m pytest -c tests/pytest.ini tests/harness/test_harness.py -q
python scripts/harness.py --structural-only
git diff --check
```

Expected: all harness tests pass, structural validation exits 0, and the diff check is clean.

- [x] **Step 5: Commit**

Commit: `feat(harness): add deterministic repository checks`

### Task 3: Logging Isolation And Service-Free Python Gate

**Files:**
- Modify: `tests/harness/test_harness.py`
- Create: `tests/harness/test_logging_override.py`
- Modify: `tradingagents/utils/logging_manager.py`
- Modify: `scripts/harness.py`
- Modify: `docs/testing.md`

**Interfaces:**
- Consumes: `TRADINGAGENTS_LOG_DIR` and the harness `Check.env` field.
- Produces: TOML logging configuration whose file, error, and structured handler directories honor an explicit environment override.

- [x] **Step 1: Write the failing logging override test**

Instantiate `TradingAgentsLogger` from a temporary current directory containing a TOML file with `directory = "./logs"`, set `TRADINGAGENTS_LOG_DIR` to another temporary path, and assert all enabled file-producing handlers use the override path.

- [x] **Step 2: Verify the current TOML conversion ignores the override**

Run:

```bash
python -m pytest -c tests/pytest.ini tests/harness/test_logging_override.py -q
```

Expected: FAIL because handler directories remain `./logs`.

- [x] **Step 3: Apply the override after TOML conversion**

Add a small `_apply_environment_overrides(config)` helper to `TradingAgentsLogger`; when `TRADINGAGENTS_LOG_DIR` is set, replace `directory` for the `file`, `error`, and `structured` handlers that exist. Call it for both TOML and built-in configuration before handlers are created.

- [x] **Step 4: Add the explicit quick pytest selection**

Set a temporary log directory for the pytest check and select exactly:

```text
tests/config
tests/unit/real_portfolio
tests/harness
```

- [x] **Step 5: Verify and commit**

Run:

```bash
python -m pytest -c tests/pytest.ini tests/harness tests/config tests/unit/real_portfolio -q
python scripts/harness.py --python-only
git diff --check
```

Expected: 277 existing tests plus new harness tests pass without services or repository log writes.

Commit: `fix(harness): isolate quick Python validation`

### Task 4: Non-Mutating Frontend Diagnostics And Bundle Gate

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/.eslintrc.cjs`
- Modify: `frontend/yarn.lock` only if dependency metadata requires it
- Modify: `Dockerfile.frontend`
- Modify: `scripts/deployment/sync_and_build_only.ps1`
- Modify: `scripts/deployment/build_portable_package.ps1`
- Modify: `scripts/deployment/temp_original_build.ps1`
- Modify: `docs/development.md`
- Modify: `docs/testing.md`

**Interfaces:**
- Consumes: checked-in Vite dependency and existing `frontend/node_modules` for local verification.
- Produces: `npm --prefix frontend run bundle` as the canonical production bundle command; `npm --prefix frontend run lint` as a non-mutating diagnostic.

- [x] **Step 1: Demonstrate the current command failures**

Run:

```bash
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: lint fails during configuration because `.gitignore` or `@rushstack/eslint-patch` is unavailable; build fails during existing `vue-tsc` errors.

- [x] **Step 2: Add a bundle script and repair lint invocation**

Add `"bundle": "vite build"`; remove `--fix` and the missing `.gitignore` argument from `lint`; remove the undeclared `@rushstack/eslint-patch` import when ESLint resolves plugins normally from `frontend/node_modules`.

- [x] **Step 3: Point deployment paths at the package script**

Replace direct `yarn vite build` calls with `yarn bundle`, keeping Yarn frozen installs unchanged. Document `bundle` as blocking and `lint` / `type-check` as diagnostic with current debt.

- [x] **Step 4: Verify bundle success and diagnostic semantics**

Run:

```bash
npm --prefix frontend run bundle
npm --prefix frontend run lint
git diff --check
```

Expected: bundle exits 0. Lint reaches source diagnostics without changing tracked files; its non-zero status remains recorded debt.

- [x] **Step 5: Commit**

Commit: `fix(frontend): make local quality commands deterministic`

### Task 5: One CI Gate And Protected Automation

**Files:**
- Create: `.github/workflows/quality.yml`
- Modify: `.github/workflows/docker-publish.yml`
- Modify: `.github/workflows/upstream-sync-check.yml`
- Modify: `docs/development.md`
- Modify: `docs/testing.md`

**Interfaces:**
- Consumes: `python scripts/harness.py`, Python 3.11, Node 22, Yarn 1.22.22, `requirements-lock.txt`, and `frontend/yarn.lock`.
- Produces: a `quality` workflow for pull requests and `main`, plus a successful quality prerequisite for image publication.

- [x] **Step 1: Add the normal quality workflow**

Checkout, set up Python 3.11 and Node 22 with Yarn cache, activate Yarn 1.22.22 through Corepack, install `requirements-lock.txt` and the project, run `yarn --cwd frontend install --frozen-lockfile`, then run `python scripts/harness.py`.

- [x] **Step 2: Gate Docker publication**

Add a `quality` job with the same installation and harness steps to the tag/manual Docker workflow. Make `build-and-push` depend on `quality` so credentials and image pushes happen only after validation.

- [x] **Step 3: Remove direct upstream mutation**

Delete the `auto-sync` job from `upstream-sync-check.yml`. Keep update detection and issue creation so upstream changes remain visible and manually reviewable.

- [x] **Step 4: Validate workflow structure**

Add standard-library workflow checks to the harness: required workflow files exist, the Docker publish job depends on quality, and no workflow line invokes `git push origin main`. Then run:

```bash
python scripts/harness.py --structural-only
git diff --check
```

Expected: structural checks and diff check exit 0.

- [x] **Step 5: Commit**

Commit: `ci(harness): enforce the personal quality gate`

### Task 6: Safe Console Entry Point

**Files:**
- Create: `tests/harness/test_console_entrypoint.py`
- Modify: `pyproject.toml`
- Modify: `scripts/harness.py`
- Modify: `docs/development.md`
- Modify: `docs/testing.md`

**Interfaces:**
- Consumes: `[project.scripts].tradingagents` from `pyproject.toml` and `cli.main.main()`.
- Produces: side-effect-free resolution of the installed `tradingagents` console command.

- [ ] **Step 1: Write the failing entry-point resolution test**

Parse `pyproject.toml` with `tomllib`, resolve the configured `module:function` in a subprocess with a five-second timeout, and assert import succeeds and the target is callable. The subprocess sets a temporary `TRADINGAGENTS_LOG_DIR`.

- [ ] **Step 2: Verify the root demo module is unsafe**

Run:

```bash
python -m pytest -c tests/pytest.ini tests/harness/test_console_entrypoint.py -q
```

Expected: FAIL because importing `main:main` either has no callable `main` or executes the NVDA example.

- [ ] **Step 3: Point the console command at the existing CLI**

Change the entry point to:

```toml
[project.scripts]
tradingagents = "cli.main:main"
```

Add console-entry-point validation to the harness structural checks and update canonical documentation.

- [ ] **Step 4: Verify resolution and editable installation**

Run:

```bash
python -m pytest -c tests/pytest.ini tests/harness/test_console_entrypoint.py -q
python -m pip install --no-deps -e .
python -c "import importlib.metadata as m; ep=next(x for x in m.entry_points(group='console_scripts') if x.name=='tradingagents'); assert callable(ep.load())"
git diff --check
```

Expected: all commands exit 0 without starting an analysis or requiring services.

- [ ] **Step 5: Commit**

Commit: `fix(cli): point console script at the Typer entrypoint`

### Task 7: Full Harness Verification And Debt Alignment

**Files:**
- Modify: `docs/technical-debt.md`
- Modify: `docs/testing.md`
- Modify: `docs/development.md`
- Modify: `docs/superpowers/plans/2026-09-07-personal-harnessing.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: an evidence-backed current debt snapshot and completed implementation checklist.

- [ ] **Step 1: Re-measure non-blocking debt**

Run frontend lint and type checking, broad pytest collection from a temporary working directory, the `tradingagents -> app` import scan, source-file line counts, and a local Markdown-link scan. Record command, date, count, and revisit trigger; do not repair the entire legacy backlog.

- [ ] **Step 2: Run the complete local harness**

Run:

```bash
python scripts/harness.py
```

Expected: structural checks, Python compilation, explicit pytest quick suite, and Vite bundle all exit 0 in less than two minutes on the maintainer's machine.

- [ ] **Step 3: Verify history and worktree scope**

Run:

```bash
git diff --check
git status --short
git log --format=fuller --reverse 1da73091..HEAD
```

Expected: only the final documentation updates are uncommitted; every harnessing commit has Implementation and Verification bodies.

- [ ] **Step 4: Mark completed plan items and commit**

Check every completed box in this plan, update the documented command results, then commit:

`docs(harness): record final validation baseline`

- [ ] **Step 5: Request independent code review**

Review `1da73091..HEAD` against the design and this plan. Fix every critical or important finding, rerun the full harness, and put any accepted minor follow-up in `docs/technical-debt.md`.
