# Personal Harnessing Design

## Context

This repository is maintained primarily for personal use. The harness should make
routine agent-assisted changes easier to understand and verify without introducing
an enterprise governance system.

The design borrows the practical parts of OpenAI's harness engineering approach:

- keep repository knowledge discoverable and current;
- give agents one small map instead of a long instruction manual;
- turn important rules into executable checks;
- make the common validation loop fast and deterministic;
- record known debt instead of pretending the whole legacy repository is clean.

Source: [Harness engineering: leveraging Codex in an agent-first world](https://openai.com/zh-Hans-CN/index/harness-engineering/).

## Audit Findings

The initial read-only audit found these actionable problems:

1. `AGENTS.md`, `CLAUDE.md`, `README.md`, and `docs/README.md` disagree about the
   supported Python version, frontend package manager, and validation commands.
2. `docs/README.md` still presents v0.1.12 as current while the project version is
   v1.0.0-preview, and many legacy documentation links are broken.
3. There is no normal pull-request or push CI. Existing workflows only publish
   Docker images or inspect upstream changes.
4. The default pytest collection is not a usable gate. Imports attempt to open a
   container-owned repository log file, legacy tests perform network/database work
   during collection, and several tests import removed modules.
5. The frontend `lint` command references a missing `frontend/.gitignore`, mutates
   files with `--fix`, and depends on an undeclared ESLint patch package.
6. Frontend type checking currently reports about 240 errors in 41 files. Fixing all
   of them is too large for this harnessing pass.
7. The installed `tradingagents` console script targets `main:main`, but root
   `main.py` has no `main()` function and executes an NVDA analysis at import time.
8. The upstream auto-sync workflow can push directly to `main` without running a
   project validation command.

## Goals

1. Make the current architecture and daily development commands easy to find.
2. Provide one local command that gives a trustworthy answer for ordinary changes.
3. Run that same command in a small GitHub Actions workflow.
4. Fix the repository issues that prevent the lightweight gate from working.
5. Leave a concise, prioritized backlog for larger legacy cleanup.

## Non-Goals

- Fixing every existing frontend TypeScript or formatting error.
- Repairing every link in the historical documentation archive.
- Splitting all large Python and Vue files.
- Adding quality scores, scheduled gardening agents, mandatory local hooks, an ADR
  process, an observability stack, or per-worktree application orchestration.
- Requiring live MongoDB, Redis, market-data providers, or LLM credentials in the
  quick validation path.

## Repository Knowledge

`AGENTS.md` will remain short and become the entry map. It will point to:

- `ARCHITECTURE.md` for current component boundaries and runtime flow;
- `docs/README.md` for the current documentation index;
- `docs/development.md` for setup, common commands, and change workflow;
- `docs/testing.md` for quick checks and opt-in integration checks;
- `docs/technical-debt.md` for known legacy failures and follow-up priorities.

Existing historical and feature-specific documents will stay in place. The new
index will distinguish current reference documents from historical material so the
change does not require a disruptive documentation move.

## Lightweight Validation Harness

The repository will expose one executable entry point:

```bash
python scripts/harness.py
```

It will run only deterministic checks that do not require credentials or services:

1. repository-specific structural checks implemented with the Python standard
   library;
2. Python source compilation;
3. a curated pytest quick suite;
4. frontend production bundling with the checked-in Yarn lockfile;
5. non-mutating frontend lint when its existing error level permits it.

The structural checks will validate a small set of high-value invariants:

- required repository-map documents exist and their local links resolve;
- the Apache-licensed `tradingagents/` package does not import proprietary `app/`
  modules;
- the project console entry point resolves to a callable function;
- newly introduced oversized source files are rejected while existing large files
  remain documented debt.

The harness will print the exact failed command and remediation guidance. It will
return a non-zero exit status on failure.

## Test Strategy

Pytest configuration will separate the quick, service-free suite from legacy and
integration checks. The quick suite will be selected explicitly rather than relying
on the current broad `tests/` collection.

The logging setup will honor an explicit log-directory override before opening a
file. Tests will set that directory to a temporary location so imports do not depend
on ownership of runtime log files.

Every behavior-changing fix will follow a focused red-green cycle. Configuration and
documentation changes will be verified with the harness itself.

Frontend type-check debt will be documented but will not be hidden: `yarn type-check`
remains available as a full diagnostic command. The quick gate uses the actual Vite
production build because that is the repository's current deploy path.

## CI And Automation

A single lightweight GitHub Actions workflow will run on pull requests and pushes to
`main`. It will install Python and frontend dependencies from the committed project
metadata, then invoke the same local harness command.

Docker publishing and upstream synchronization must run the harness before publishing
or pushing. The upstream workflow will not gain more automation; it will only lose
the ability to bypass validation.

## Implementation Sequence

1. Record this design, audit, and a task-level execution plan.
2. Replace stale entry-point documentation with the small repository knowledge map.
3. Add and test the structural checker and unified local harness command.
4. Fix test logging isolation and define a deterministic Python quick suite.
5. Repair the frontend lint command and validate the current production build path.
6. Add the CI workflow and guard existing publish/sync workflows.
7. Fix the console-script entry point and add an installation smoke check.
8. Re-run all lightweight gates and update the technical-debt document with measured
   remaining failures.

## Commit Policy

Changes will be committed by independently verifiable concern. Each commit message
will use a Conventional Commit title and include two body sections:

```text
Implementation:
- concrete changes in this commit

Verification:
- commands run and their results
```

The original checkout's modified `uv.lock`, `.codex/`, `Untitled`, and untracked
analysis documents are outside this worktree and outside the scope of every commit.
