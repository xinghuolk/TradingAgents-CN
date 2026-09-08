# Documentation Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sprawling active documentation surface with five current guides, a small reference set, and a clearly isolated historical archive without deleting unique project history.

**Architecture:** `docs/README.md` is the human entry point, `docs/current/` contains current operational guidance, and `docs/reference/` contains stable technical detail. Historical material moves below `docs/archive/` with its original topic structure retained where practical. The lightweight harness validates canonical and current documents but deliberately does not require frozen archive links to be repaired.

**Tech Stack:** Markdown, Python 3.11, pytest, Git, the existing `scripts/harness.py` validation entry point.

**Spec:** `docs/superpowers/specs/2026-09-08-document-archive-design.md`

## Global Constraints

- Optimize for one personal maintainer; do not add a documentation site generator, metadata service, ownership system, or bot.
- Do not delete a document merely because it is old or has no inbound links.
- Preserve moves with `git mv`; do not rewrite archived content to make it appear current.
- Keep `ARCHITECTURE.md`, `docs/development.md`, `docs/testing.md`, and `docs/technical-debt.md` canonical.
- Keep `docs/learning/` and `docs/paper/` at their current paths because the Vue learning center imports their Markdown at build time.
- Do not touch the uncommitted files in the main checkout; all work occurs in `.worktrees/docs-archive`.
- Every commit body records `Implementation:` and `Verification:`.
- Run `python scripts/harness.py --structural-only` after documentation-only batches and the complete harness before completion.

---

### Task 1: Enforce the archive boundary

**Files:**
- Create: `docs/archive/README.md`
- Modify: `scripts/harness.py`
- Modify: `tests/harness/test_harness.py`

**Interfaces:**
- Consumes: `CANONICAL_DOCUMENTS` and `validate_local_links()` from `scripts/harness.py`.
- Produces: `iter_current_documents(root: Path) -> tuple[Path, ...]` and an archive policy required by structural validation.

- [ ] **Step 1: Write the failing current-document discovery test**

Add this import and test to `tests/harness/test_harness.py`:

```python
from scripts.harness import iter_current_documents


def test_iter_current_documents_includes_current_tree(tmp_path: Path) -> None:
    current = tmp_path / "docs" / "current"
    current.mkdir(parents=True)
    (current / "getting-started.md").write_text("# Start\n", encoding="utf-8")
    (current / "nested").mkdir()
    (current / "nested" / "reference.md").write_text("# Ref\n", encoding="utf-8")

    relative = {
        path.relative_to(tmp_path)
        for path in iter_current_documents(tmp_path)
        if path.exists()
    }

    assert Path("docs/current/getting-started.md") in relative
    assert Path("docs/current/nested/reference.md") in relative
```

- [ ] **Step 2: Run the focused test and observe the missing import**

Run:

```bash
/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python -m pytest -c tests/pytest.ini tests/harness/test_harness.py::test_iter_current_documents_includes_current_tree -q
```

Expected: collection fails because `iter_current_documents` is not defined.

- [ ] **Step 3: Implement current-document discovery**

Add to `scripts/harness.py`:

```python
ARCHIVE_POLICY = Path("docs/archive/README.md")


def iter_current_documents(root: Path) -> tuple[Path, ...]:
    documents = [root / path for path in CANONICAL_DOCUMENTS]
    documents.append(root / ARCHIVE_POLICY)
    current_root = root / "docs" / "current"
    if current_root.is_dir():
        documents.extend(sorted(current_root.rglob("*.md")))
    return tuple(dict.fromkeys(documents))
```

Change `validate_repository()` to iterate over `iter_current_documents(root)` and
report missing paths relative to `root`. Do not traverse `docs/archive/` beyond the
single policy document.

- [ ] **Step 4: Create the archive policy**

Create `docs/archive/README.md` with these sections and rules:

```markdown
# Documentation Archive

This directory preserves historical TradingAgents-CN documentation. It is not a
source of current setup, operation, architecture, or troubleshooting instructions.

## Reading Archived Documents

- Treat commands, ports, dependencies, screenshots, and configuration keys as
  historical unless current documentation confirms them.
- Use Git history when the reason or implementation date matters.
- Start from `docs/README.md` for current guidance.

## Maintenance

Move documents here with `git mv`. Preserve their content and original topic grouping.
Fix links from current documents when moving a target; broken links entirely inside
the archive are non-blocking.
```

- [ ] **Step 5: Verify and commit**

Run:

```bash
/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python -m pytest -c tests/pytest.ini tests/harness/test_harness.py -q
/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python scripts/harness.py --structural-only
git diff --check
```

Commit:

```bash
git commit -m "test(docs): enforce documentation lifecycle boundary" -m "Implementation:
- require an archive policy and discover all current Markdown documents
- keep historical archive links outside the blocking structural gate
- cover recursive current-document discovery with a focused test

Verification:
- pytest tests/harness/test_harness.py
- python scripts/harness.py --structural-only
- git diff --check"
```

### Task 2: Publish the current guide set

**Files:**
- Create: `docs/current/getting-started.md`
- Create: `docs/current/configuration.md`
- Create: `docs/current/usage.md`
- Create: `docs/current/deployment.md`
- Create: `docs/current/troubleshooting.md`
- Modify: `docs/README.md`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: current commands from `docs/development.md`, `docker-compose.yml`, `.env.example`, and routes from `frontend/src/router/index.ts`.
- Produces: the complete current user-facing documentation surface linked by `docs/README.md`.

- [ ] **Step 1: Create five concise current guides**

Write the following verified content boundaries:

- `getting-started.md`: Python 3.11 and Docker prerequisites; cloning; copying
  `.env.example`; `python -m pip install -e .`; frontend frozen Yarn install;
  backend, frontend, CLI, and `docker compose up -d` commands; links to configuration,
  deployment, development, and troubleshooting.
- `configuration.md`: `.env` as the secret-bearing bootstrap file; MongoDB, Redis,
  `JWT_SECRET`, `CSRF_SECRET`, at least one LLM provider, and China data-source
  selection; the Settings UI as the normal runtime configuration surface; proxy,
  extractor, and OAuth notes already represented in `.env.example`; no copied secrets.
- `usage.md`: login, Settings, single and batch analysis, task center, reports,
  screening, favorites, paper trading, real portfolio imports, and learning center,
  matching routes in `frontend/src/router/index.ts`.
- `deployment.md`: `docker compose up -d`, `docker compose ps`, service logs,
  `docker compose down`, frontend at `http://localhost:3000`, backend health at
  `http://localhost:8000/api/health`, MongoDB host port 37017, Redis host port 26379,
  and optional management profile behavior from `docker-compose.yml`.
- `troubleshooting.md`: check Compose health and logs first; distinguish application,
  MongoDB, Redis, provider, and frontend bundle failures; run the harness for source
  changes; never paste credentials into issues or logs.

Each guide starts with a statement that it describes `v1.0.0-preview` and links back
to `../README.md`. Do not copy long provider catalogs or legacy installation scripts.

- [ ] **Step 2: Replace the documentation index**

Update `docs/README.md` to contain four sections:

```markdown
## 使用与运维
## 开发与架构
## 稳定参考
## 历史资料
```

Link all five `current/` guides under the first section, the four canonical engineering
documents under the second, promoted reference documents under the third as they are
created, and only `archive/README.md`, `releases/CHANGELOG.md`, `learning/`, and
`paper/` under history/content. State explicitly that unlisted legacy directories are
being migrated and are not authoritative.

- [ ] **Step 3: Point repository entry files at the current set**

In `README.md`, add direct links to `docs/current/getting-started.md`,
`docs/current/configuration.md`, and `docs/current/deployment.md` in the installation
and documentation sections.

Replace the three stale `.env.example` comments with:

```text
docs/current/configuration.md
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python scripts/harness.py --structural-only
git diff --check
```

Commit:

```bash
git commit -m "docs(current): publish canonical user guides" -m "Implementation:
- add current getting-started, configuration, usage, deployment, and troubleshooting guides
- rebuild the documentation index around current, reference, and historical content
- point README and environment guidance at the canonical current set

Verification:
- python scripts/harness.py --structural-only
- git diff --check"
```

### Task 3: Repair active documentation references

**Files:**
- Modify: `.github/ISSUE_TEMPLATE/config.yml`
- Modify: `.github/ISSUE_TEMPLATE/documentation.md`
- Modify: `.github/ISSUE_TEMPLATE/question.md`
- Modify: `.github/pull_request_template.md`
- Modify: `app/core/config.py`
- Modify: `app/core/startup_validator.py`
- Modify: `tradingagents/agents/analysts/fundamentals_analyst.py`
- Modify: `tradingagents/config/config_manager.py`
- Modify: `app/core/config_compat.py`
- Modify: `scripts/setup-docker.py`
- Modify: `scripts/setup/quick_install.py`
- Modify: `scripts/harness.py`
- Modify: `tests/harness/test_harness.py`

**Interfaces:**
- Consumes: current guide paths created in Task 2.
- Produces: valid links from active templates and runtime diagnostics into current documentation.

- [ ] **Step 1: Write the failing support-document link test**

Add:

```python
def test_active_support_document_links_resolve() -> None:
    documents = (
        ROOT / "README.md",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "question.md",
        ROOT / ".github" / "pull_request_template.md",
    )
    errors = [
        error
        for document in documents
        for error in validate_local_links(document, ROOT)
    ]
    assert errors == []
```

Run the focused test and confirm it reports the existing `../docs/`,
`DOCKER_GUIDE.md`, and `LLM_INTEGRATION_GUIDE.md` targets.

- [ ] **Step 2: Repair template links**

Use these targets:

- issue template project docs: `../../docs/README.md`;
- issue template deployment: `../../docs/current/deployment.md`;
- issue template quick start: `../../docs/current/getting-started.md`;
- issue template configuration: `../../docs/current/configuration.md`;
- pull request contribution guide: `../docs/development.md`;
- `config.yml` deployment URL:
  `https://github.com/hsliuping/TradingAgents-CN/blob/main/docs/current/deployment.md`.

Update the documentation issue example to `docs/current/deployment.md`.

- [ ] **Step 3: Add active support documents to structural validation**

Add to `scripts/harness.py` and include these paths in
`iter_current_documents(root)`:

```python
ACTIVE_SUPPORT_DOCUMENTS = (
    Path("README.md"),
    Path(".github/ISSUE_TEMPLATE/question.md"),
    Path(".github/pull_request_template.md"),
)
```

- [ ] **Step 4: Repair runtime and setup-script references**

Change current configuration references in `app/core/config.py` and
`app/core/startup_validator.py` to `docs/current/configuration.md`. Change setup script
references to `docs/current/getting-started.md` and `docs/current/deployment.md`.

Change analyst configuration references to
`docs/reference/agents/configuration.md`. Change deprecation references to
`docs/reference/deprecations.md`; Task 6 promotes those targets before the final full
gate.

- [ ] **Step 5: Verify and commit**

Run the focused harness tests and structural validation. Commit as
`docs(links): repair active documentation references` with the required Implementation
and Verification body.

### Task 4: Archive retrospective engineering records

**Files:**
- Move: `docs/fixes/` to `docs/archive/fixes-and-incidents/fixes/`
- Move: `docs/bugfix/` to `docs/archive/fixes-and-incidents/bugfix/`
- Move: `docs/summary/` to `docs/archive/implementation-reports/summary/`
- Move: `docs/changes/DEPRECATION_NOTICE.md` to `docs/reference/deprecations.md`
- Move: the remaining `docs/changes/` to `docs/archive/implementation-reports/changes/`
- Move: `docs/migration/` to `docs/archive/implementation-reports/migration/`
- Move: `docs/improvements/` to `docs/archive/implementation-reports/improvements/`
- Move: `docs/tech_reviews/` to `docs/archive/engineering-plans/tech-reviews/`

**Interfaces:**
- Consumes: the archive policy from Task 1.
- Produces: 116 historical documents removed from the active topic namespace and one current deprecation reference.

- [ ] **Step 1: Record and verify the source inventory**

Run:

```bash
find docs/fixes docs/bugfix docs/summary docs/changes docs/migration docs/improvements docs/tech_reviews -type f | wc -l
```

Expected: 117, comprising 116 archive records and one promoted deprecation notice.

- [ ] **Step 2: Move each concern with Git history preserved**

Create the three archive parents and `docs/reference/`. First move
`docs/changes/DEPRECATION_NOTICE.md` to `docs/reference/deprecations.md`, then move the
remaining seven source groups with `git mv`.

- [ ] **Step 3: Verify and commit**

Verify that none of the seven old directories remain, run structural validation and `git diff --check`,
then commit as `docs(archive): move retrospective engineering records` with inventory
counts in the commit body.

### Task 5: Archive version-specific documentation

**Files:**
- Move: `docs/agents/v0.1.13/` to `docs/archive/versions/v0.1.13/agents/`
- Move: `docs/architecture/v0.1.13/` to `docs/archive/versions/v0.1.13/architecture/`
- Move: `docs/architecture/v0.1.16/` to `docs/archive/versions/v0.1.16/architecture/`
- Move: `docs/design/v0.1.16/` to `docs/archive/versions/v0.1.16/design/`
- Move: `docs/development/v0.1.16/` to `docs/archive/versions/v0.1.16/development/`
- Move: `docs/deployment/v0.1.16/` to `docs/archive/versions/v0.1.16/deployment/`
- Move: `docs/design/v1.0.1/` to `docs/archive/versions/v1.0.1/design/`

**Interfaces:**
- Consumes: version labels already encoded in source paths.
- Produces: 42 version-specific documents under an explicit historical boundary.

- [ ] **Step 1: Confirm the inventory**

Run `find` over the seven exact source directories and confirm 42 files.

- [ ] **Step 2: Move by version**

Use `git mv` for each exact directory. Keep current unversioned architecture and
development paths untouched.

- [ ] **Step 3: Verify and commit**

Run structural validation and `git diff --check`. Commit as
`docs(archive): isolate version-specific documentation` with the seven source-to-target
mappings in the commit body.

### Task 6: Promote stable references and clear root clutter

**Files:**
- Move: `docs/ANALYST_DATA_CONFIGURATION.md` to `docs/reference/agents/configuration.md`
- Move: `docs/LLM_ADAPTER_TEMPLATE.py` to `docs/reference/llm/adapter_template.py`
- Move: `docs/LLM_CONFIG_AND_EXTRACTOR_INTEGRATION.md` to `docs/reference/llm/financial-report-extractor.md`
- Move: all other non-canonical root documentation files except `CNAME` to `docs/archive/legacy/root/`
- Modify: `docs/README.md`

**Interfaces:**
- Consumes: reference links introduced in Task 3.
- Produces: a root containing only canonical documents, `CNAME`, and lifecycle directories.

- [ ] **Step 1: Verify promoted references against code**

Compare analyst configuration names with
`tradingagents/agents/analysts/fundamentals_analyst.py` and extractor installation
details with `pyproject.toml` and `Dockerfile.backend`. Confirm the deprecation notice
promoted in Task 4 still names symbols present in `tradingagents/config/config_manager.py`.
Correct only statements contradicted by those current files.

- [ ] **Step 2: Promote the stable root references**

Create `docs/reference/agents/` and `docs/reference/llm/`, then use `git mv` for the
three exact root files. Add their final paths and `docs/reference/deprecations.md`
under the stable-reference section of `docs/README.md`.

- [ ] **Step 3: Archive the remaining legacy root files**

Keep only:

```text
docs/CNAME
docs/README.md
docs/development.md
docs/testing.md
docs/technical-debt.md
```

Move every other file directly under `docs/` to `docs/archive/legacy/root/`. No file
is deleted.

- [ ] **Step 4: Verify and commit**

Run structural validation, relevant `rg` path checks, and `git diff --check`. Commit
as `docs(reference): separate stable references from legacy root notes`.

### Task 7: Archive superseded user and operations guides

**Files:**
- Move: `docs/overview/`, `docs/usage/`, `docs/faq/`, `docs/examples/`, and `docs/guides/` to `docs/archive/legacy/user-guides/`
- Move: `docs/configuration/`, `docs/deployment/`, `docs/docker/`, `docs/troubleshooting/`, `docs/llm/`, and `docs/integration/` to `docs/archive/legacy/operations/`
- Promote: `docs/maintenance/upstream-sync.md` to `docs/reference/operations/upstream-sync.md`
- Promote: `docs/api/batch-analysis-limits.md` to `docs/reference/api/batch-analysis-limits.md`
- Promote: `docs/security/api_keys_security.md` to `docs/reference/security/api-keys.md`
- Modify: `.github/workflows/upstream-sync-check.yml`
- Modify: `docs/README.md`

**Interfaces:**
- Consumes: the five current guides that supersede general setup and operations instructions.
- Produces: a small stable reference set and removes conflicting Streamlit-era guides from the active namespace.

- [ ] **Step 1: Promote the three stable operational references**

Use `git mv` for the three exact files, update the upstream workflow issue link to
`docs/reference/operations/upstream-sync.md`, and list the references in
`docs/README.md`.

- [ ] **Step 2: Archive the superseded directories**

Move each exact source directory to its named archive parent with `git mv`. If a
promoted source directory becomes empty, do not recreate it.

- [ ] **Step 3: Check active paths**

Run:

```bash
rg -n 'docs/(DOCKER_GUIDE|INSTALLATION_GUIDE|configuration_guide|proxy_configuration|AGGREGATOR_SUPPORT)' README.md .env.example .github app tradingagents scripts --glob '!scripts/deployment/release_*'
```

Expected: no current runtime or support-template references to superseded paths.

- [ ] **Step 4: Verify and commit**

Run the focused harness tests, structural validation, and `git diff --check`. Commit
as `docs(archive): retire superseded user and operations guides` with the promoted
reference list in the body.

### Task 8: Archive remaining historical topic areas

**Files:**
- Move: unversioned `docs/architecture/`, `docs/design/`, `docs/features/`, `docs/frontend/`, `docs/implementation/`, `docs/data/`, `docs/technical/`, `docs/technical-debt/`, `docs/config/`, `docs/development/`, `docs/maintenance/`, and `docs/security/` to `docs/archive/legacy/engineering/`
- Move: `docs/blog/`, `docs/community/`, `docs/survey/`, and `docs/localization/` to `docs/archive/announcements/`
- Move: all files under `docs/releases/` except `CHANGELOG.md` to `docs/archive/versions/releases/`
- Keep: `docs/analysis/`, `docs/images/`, `docs/learning/`, `docs/paper/`, `docs/releases/CHANGELOG.md`, and canonical/current/reference/archive paths.
- Modify: `README.md`
- Modify: `docs/README.md`

**Interfaces:**
- Consumes: canonical architecture and current guides.
- Produces: an active documentation tree whose remaining top-level directories have an explicit current, reference, runtime-content, analysis-evidence, release-index, or archive role.

- [ ] **Step 1: Archive engineering history by exact top-level group**

Use `git mv` for each listed source directory. `docs/development.md` and
`docs/technical-debt.md` are files and remain canonical; only their same-named
directories move.

- [ ] **Step 2: Archive announcements and individual release records**

Move the four announcement directories. Preserve `docs/releases/CHANGELOG.md` at its
existing public path and move every other release file to
`docs/archive/versions/releases/` with `git mv`.

- [ ] **Step 3: Remove current links to archived community material**

Replace the README tester-announcement link with a plain statement that contribution
and testing coordination happens through GitHub issues and discussions. Keep the
changelog link unchanged.

- [ ] **Step 4: Verify and commit**

Run structural validation, the Vite bundle because learning and paper paths are a
frontend build contract, and `git diff --check`. Commit as
`docs(archive): isolate historical feature and project records`.

### Task 9: Archive completed agent work and close the inventory

**Files:**
- Move: completed files in `docs/superpowers/specs/` to `docs/archive/engineering-plans/superpowers/specs/`
- Move: completed files in `docs/superpowers/plans/` to `docs/archive/engineering-plans/superpowers/plans/`
- Create: `docs/archive/inventory.md`
- Modify: `docs/technical-debt.md`
- Modify: `docs/README.md`

**Interfaces:**
- Consumes: completed implementation state and all preceding moves.
- Produces: a measured final inventory and no completed plan material in the active documentation namespace.

- [ ] **Step 1: Archive prior completed work documents**

Move every existing file in `docs/superpowers/specs/` and `docs/superpowers/plans/`
except these two active documents:

```text
docs/superpowers/specs/2026-09-08-document-archive-design.md
docs/superpowers/plans/2026-09-08-document-archive.md
```

The completed personal harnessing design and plan move with the other completed work.

- [ ] **Step 2: Record the final inventory**

Create `docs/archive/inventory.md` with counts for current, reference, archive,
runtime learning/paper content, retained analysis evidence, and unresolved archive
links. Include the exact date and commands used to reproduce each count.

- [ ] **Step 3: Update technical debt**

Replace the old global documentation-link evidence with two values: zero unresolved
links in the harness-controlled current set, and the measured non-blocking archive
count. State that archive links are repaired only when a historical document is
promoted or substantively edited.

- [ ] **Step 4: Run the complete gate**

Run:

```bash
/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python scripts/harness.py
git diff --check
git status --short
```

Expected: 288 or more Python tests pass, Python compilation succeeds, the Vite bundle
succeeds, `git diff --check` is empty, and only this task's intended files are staged.

- [ ] **Step 5: Commit the measured archive state**

Commit as `docs(archive): record the consolidated documentation inventory` with exact
counts and complete harness results in the commit body.

### Task 10: Independent review and final cleanup

**Files:**
- Move after review: the current design and plan from `docs/superpowers/` into `docs/archive/engineering-plans/superpowers/`
- Modify if review requires: only files implicated by valid Critical or Important findings

**Interfaces:**
- Consumes: the complete branch diff from `2cf34100` to the branch HEAD.
- Produces: a reviewed branch with its own completed planning records archived.

- [ ] **Step 1: Review the full diff**

Review for accidental loss of current instructions, unresolved active links, broken
frontend Markdown imports, incorrect source-to-target moves, and unnecessary
governance for a personal project. Critical and Important findings must be fixed;
Minor findings are fixed only when they materially improve correctness or navigation.

- [ ] **Step 2: Archive this completed design and plan**

After all implementation tasks pass, move the two 2026-09-08 documents into their
matching archive `specs/` and `plans/` directories. Update any current link that still
points to their old locations.

- [ ] **Step 3: Run final verification and commit**

Run the full harness, `git diff --check`, and a clean-status check. Commit as
`docs(archive): close documentation consolidation review`, recording review findings
and verification results in the body.
