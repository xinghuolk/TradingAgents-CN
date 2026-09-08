# Task 4 Report: Archive Retrospective Engineering Records

## Status

Implemented, self-reviewed, and ready to commit.

## Changes

- Moved `docs/fixes/` to `docs/archive/fixes-and-incidents/fixes/` (73 files).
- Moved `docs/bugfix/` to `docs/archive/fixes-and-incidents/bugfix/` (10 files).
- Moved `docs/summary/` to `docs/archive/implementation-reports/summary/` (8 files).
- Promoted `docs/changes/DEPRECATION_NOTICE.md` to
  `docs/reference/deprecations.md`.
- Moved the remaining `docs/changes/` files to
  `docs/archive/implementation-reports/changes/` (4 files).
- Moved `docs/migration/` to `docs/archive/implementation-reports/migration/`
  (2 files).
- Moved `docs/improvements/` to
  `docs/archive/implementation-reports/improvements/` (6 files).
- Moved `docs/tech_reviews/` to
  `docs/archive/engineering-plans/tech-reviews/` (13 files).
- Updated current deprecation references in
  `tradingagents/config/config_manager.py` and `app/core/config_compat.py` to
  `docs/reference/deprecations.md`.

## Inventory

The required source inventory was `117` files: `116` historical archive records
and one promoted deprecation notice.

## Verification

- `find docs/fixes docs/bugfix docs/summary docs/changes docs/migration docs/improvements docs/tech_reviews -type f | wc -l`
  - Result before moves: `117`
- Old source directories are absent after the moves.
- `/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python scripts/harness.py --structural-only`
  - Result: `Repository structure: OK`
- Focused `rg` confirms both runtime references use
  `docs/reference/deprecations.md` and finds no old deprecation path there.
- `git diff --check`
  - Result: clean.

No text-assertion tests were added; this task only moves documentation and
updates configuration guidance paths.

## Commit

- `docs(archive): move retrospective engineering records`

## Self-Review and Concerns

- All moves use `git mv`; archived document contents were not rewritten.
- The active deprecation notice remains under `docs/reference/` and is covered by
  current-document structural validation.
- Links within archived records may still reference their historical locations;
  archive links are non-blocking under the repository policy.
- Current canonical documents and the Vue learning and paper paths were not
  moved or edited.
