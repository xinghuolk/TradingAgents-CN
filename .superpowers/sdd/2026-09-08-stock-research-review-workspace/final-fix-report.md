# Stock Research Final Fix Report

Date: 2026-09-09 (Asia/Shanghai; verification began on 2026-09-08).
Reviewed base: `4a33c00248a7623ba9e7fbee31a15988fdb92394`.
Workspace: `.worktrees/stock-research-review-workspace`.
Scope: one unified fix wave for final findings 1-10. All ten are addressed.

## Finding Resolution

| Finding | Resolution | Regression evidence |
| --- | --- | --- |
| 1. Stale replacement can undo confirmation, archive, or deletion | Replaced whole-entry saves with field-only conditional writes matching owner, lifecycle, entry type, and `write_version`. Link updates increment the same version. Thesis writes update only requested fields; revision pointers use monotonic `$max`. Revision restore preserves lifecycle and AI originals. | Deterministic delayed autosave versus confirm/archive/delete; delayed conversion/confirmation/archive/revision restore versus deletion; delayed thesis save versus manual revision. |
| 2. Production reports lack direct ownership field | Explicit report ownership remains supported. Reports without a user resolve ownership only through their `task_id` and an owned `analysis_tasks` row. Other-user tasks and missing tasks are rejected. | Production-shaped report documents without `user_id`, both list and stable-ID resolution, owner and non-owner cases. |
| 3. Note/research manual versions missing | Added `save_entry_version`, owned `POST /entries/{id}/revisions`, optional version label, and the workspace command for editable notes/research. Existing history restores a new version. | Both types tested through service, executable Vue script, real API browser flow, and owned/non-owner route checks. |
| 4. Confirmed decisions cannot manage trade links in UI | Added separate confirmed-decision candidate selection, save/change, refresh, and unlink controls. Real and paper labels remain separate; conflict is shown without changing the formal body or thesis snapshot. Candidate-only fields are projected out of request DTOs. | Executable checks exercise add/change/unlink, conflict, and exact request shape. Browser calls real link endpoints and checks immutable body/snapshot. |
| 5. Review context options/default facts do not reach AI | Shared context builder powers preview and submission. Carries scope, dates/options, theses, recent notes/research, selected decision body/action/thesis, holdings, trades, and report summaries. Decision defaults include the week before the decision through today. Missing sources are explicit and independent. Dialog flushes pending edits before preview, allows source/date adjustments, and preserves exact retry options/references. | Frozen-context, before/after holdings, missing paper source, explicit reference selection, adjusted-context tests; executable preview-flush regression; both browser matrices. |
| 6. Holding universe restricted to first 100 workspaces | Added owned `/references/holdings` reading complete real/paper holdings independently of workspaces. Review UI uses those identities, then paginates optional workspace-name enrichment in pages of 200. Existing source-readiness coordination remains intact. | Empty workspace collection with 126 owned holdings including 125 paper securities; foreign holding excluded; frontend creation/readiness tests and first-time real API browser creation. |
| 7. Review confirmation lacks contemporary thesis snapshots | Confirmation freezes stock/portfolio thesis snapshots and creates versions of each existing workspace. Missing workspaces have explicit unavailable snapshots without creating placeholders. Decision confirmation also versions its frozen thesis. | Both scopes, existing/missing workspaces, snapshot immutability after later thesis edits, version reasons/counts. |
| 8. Global review lifecycle/trash controls missing | Directory review dialog supports archive/delete; directory trash supports restore and confirmed permanent deletion, including market-only reviews with no security associations. | Executable lifecycle calls and browser archive/list/delete/trash/restore/permanent-delete flow through real routes. |
| 9. Exact index specifications underasserted | Fake records complete ordered keys, directions, uniqueness, and options. Regression asserts all seven current research index definitions, including the partial decision/trade uniqueness index. | Initial exact-spec test failed against name-only recording; final exact definitions pass. |
| 10. Non-cascade spy is vacuous | Replaced unused spy with seeded source collections in the actual repository database, write-rejecting collection methods, real source references, and unchanged-document assertions across all lifecycle operations. | Full lifecycle test passes. An in-memory injected cascading deletion fails immediately with `research lifecycle attempted a source write`; no production mutation was introduced. |

## TDD Evidence

All production fixes followed failing focused regressions. Commands used
`../../.venv/bin/python`; Python tests used `-c tests/pytest.ini`.

1. Initial `tests/unit/stock_research/test_final_regressions.py -q`: eight failures exposed lifecycle resurrection and thesis revision reset. After the conditional-write fix, combined final-regression/service selection: 37 passed.
2. Added production report, manual entry version, and review thesis tests: five failed, eight passed. After implementation, combined final-regression/service/reference selection: 55 passed.
3. Added complete holdings and context tests: two failed, thirteen passed. After implementation: fifteen passed.
4. Exact index test (`-k index`): one failed, fifteen deselected because the fake discarded index directions/options. Complete index recording and exact assertions passed afterward.
5. `node frontend/scripts/check-research-final.mjs`: all six behavior groups initially failed for missing commands/context/lifecycle support, then all six passed.
6. Before/after holdings and explicit unavailable-source metadata tests: two failed, sixteen deselected, then eighteen passed. Missing paper-source regression: one failed, eighteen deselected; after isolated source handling, nineteen passed.
7. Native browser found link requests returning HTTP 422 because candidates carried extra fields. Added executable request-shape assertion: one behavior group failed (`0 !== 2`), then all six passed after Reference DTO projection.
8. Native generation reopen browser found context preview racing unsaved review references. Added executable flush assertion: failed (`undefined !== true`), then all six passed after waiting for the editor flush before preview. Native generation matrix subsequently passed both viewports.
9. Non-cascade production behavior was already correct; finding 10 repairs coverage, not product behavior. The in-memory cascade injection above proves the replacement test observes its source boundary.

The expanded router suite required updating the old apply-to-thesis revision count
from one to two because review confirmation now creates the required thesis version.
This was a contract expectation change, not a relaxed assertion. A local sync
dependency fixture initially hung under the sandbox and was changed to async
dependencies; the owned/non-owner API checks then passed without external services.

## Final Verification

- `../../.venv/bin/python scripts/harness.py`: PASS, repository structure and Python compilation succeeded, **469 tests passed**, and Vite production bundle succeeded.
- `../../.venv/bin/python -m pytest -c tests/pytest.ini tests/unit/stock_research tests/unit/test_stock_research_router.py -q`: **178 passed**.
- All seven executable frontend checks passed: `check-research-editing.mjs`, `check-research-workspace.mjs`, `check-research-reviews.mjs`, `check-research-generation.mjs`, `check-research-final.mjs`, `check-latest-request.mjs`, and `check-portfolio-formatters.mjs`.
- Scoped ESLint passed for the six changed Vue/TypeScript source files. Scoped Prettier passed for those files and changed executable JavaScript tests.
- `git diff --check`: PASS.
- Full harness retains three existing Python deprecation warnings plus existing Sass/Rollup/chunk-size warnings. They do not fail the configured gate.
- Python `ruff` and `black` are not installed in the existing virtualenv; no formatter dependencies were added. Compilation, focused/full tests, and direct diff inspection passed.

## Browser Evidence

Native Python Playwright used the real Vue application on
`http://127.0.0.1:5199/`. The final matrix intercepts API requests and routes research
requests through the real FastAPI router/services with in-memory MongoDB and
portfolio fixtures. No MongoDB, Redis, provider, OAuth, or real LLM was contacted.
The existing generation matrix uses intercepted task/config fixtures.

Commands (with existing temporary native Playwright dependencies):

```bash
PYTHONUNBUFFERED=1 PLAYWRIGHT_NODEJS_PATH=/home/like/.nvm/versions/node/v20.19.1/bin/node PYTHONPATH=/tmp/playwright-python-1.61.0:/tmp/task10-pydeps ../../.venv/bin/python frontend/scripts/check-research-final-browser.py
PYTHONUNBUFFERED=1 PLAYWRIGHT_NODEJS_PATH=/home/like/.nvm/versions/node/v20.19.1/bin/node PYTHONPATH=/tmp/playwright-python-1.61.0:/tmp/task10-pydeps ../../.venv/bin/python frontend/scripts/check-research-generation-browser.py --chromium /home/like/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome
```

| Matrix | Desktop 1440x900 | Mobile 390x844 |
| --- | --- | --- |
| Final-wave flows | PASS; 66 API requests; no page/console errors | PASS; 66 API requests; no page/console errors |
| Generation reopen/retry | PASS; three tasks; exact retry; no page/console errors | PASS; three tasks; exact retry; no page/console errors |

Final-wave results are `/tmp/research-final-wave/result-desktop.json` and
`result-mobile.json`. Screenshots in that directory use these prefixes with
`-desktop.png` and `-mobile.png`: `initial`, `first-holdings-associations`,
`global-trash-market-only`, `manual-version-note`, `manual-version-research`,
`confirmed-decision-links`, and `review-generation-context`. The script waits for
network and loading overlays to settle before exact-viewport screenshots. Key
desktop/mobile screenshots were inspected; controls remain within viewport bounds,
and the script checks horizontal page overflow and command clipping.

Generation results/screenshots are in `/tmp/research-generation-reopen/`.

## Boundaries and Remaining Concerns

No unresolved final-review finding remains. Existing cross-collection crash
atomicity is unchanged: lifecycle/content writes and revision inserts are not a
Mongo transaction. Conditional writes prevent stale content/lifecycle replacement;
they do not add crash recovery or transaction architecture. Live MongoDB parity and
live-model checks remain explicitly opt-in.

Market summaries and optional external fact categories without an existing local
reader are marked unavailable; the fix does not invent market facts or introduce
provider/network calls. Real and paper facts stay separated. Optional decision ID,
exact enabled model selection without fallback, null Codex effort, immutable AI
originals, and draft-only generation boundaries are unchanged. No scheduler,
reminder, migration, evaluation, or dependency expansion was introduced.

Architecture and testing references document the new APIs and behavior. Verification
and scope-control skills kept this wave limited to the ten findings and required
checks. The browser-testing skill supplied the native Playwright, rendered-state,
and screenshot inspection workflow. No subagents were dispatched during this wave.
