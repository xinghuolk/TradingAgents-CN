# Task 3 Report: Repair active documentation references

## Scope

Repaired active links in issue and pull-request templates, runtime guidance, and
setup-script output. Extended structural validation to cover active support
documents that route contributors and users to the documentation set.

Per the binding ruling, this task deliberately did not change deprecation links
in `tradingagents/config/config_manager.py` or `app/core/config_compat.py`, nor
the analyst configuration comment in
`tradingagents/agents/analysts/fundamentals_analyst.py`. Their reference pages
are created by Tasks 4 and 6 respectively.

## Implementation

- Updated GitHub issue and pull-request template links to the current project,
  deployment, quick-start, configuration, and development guides.
- Updated the documentation issue example and Issue Forms deployment URL.
- Updated runtime configuration guidance and installation/deployment script
  output to use `docs/current/` guides.
- Added `ACTIVE_SUPPORT_DOCUMENTS` to `scripts/harness.py` and included it in
  `iter_current_documents()`.
- Added tests that assert the active support documents are structurally checked
  and that their local Markdown links resolve.

## TDD Evidence

1. Added `test_active_support_document_links_resolve` and ran it before link
   repairs. It failed with the stale question-template `../docs/` and
   `DOCKER_GUIDE.md` targets and the pull-request
   `LLM_INTEGRATION_GUIDE.md` target (five missing local targets in total).
2. Added `test_iter_current_documents_includes_active_support_documents` and
   ran it before the harness change. It failed because `README.md` was not in
   the returned document set.
3. Implemented the smallest changes required to make both behaviors green.

## Verification

- `uv run --with pytest pytest -c tests/pytest.ini tests/harness/test_harness.py -q`
  completed with `9 passed` (one existing pytest configuration warning).
- `uv run python scripts/harness.py --structural-only` reported
  `Repository structure: OK`.
- `git diff --check` completed without whitespace errors.
- Self-review confirmed the deferred files above remain unmodified and no
  obsolete active support-document paths remain in the changed link surfaces.
