# Final Breakers Report

Date: 2026-09-09 (Asia/Shanghai)

Implementation commit: `ff6241f6` (`fix(research): preserve decision review defaults`)

## RED Evidence

1. `node frontend/scripts/check-research-reviews.mjs` failed the confirm-then-save regression before the production change:
   `confirming then saving retains unchanged trade links in the dedicated replacement payload`; actual replacement payload was `[]`, expected `real_trade:trade-1`.
2. The same focused command, with the first new regression isolated, failed the decision-review creation regression before the production change:
   `decision-review creation must preserve the backend real-holdings/trades default`; actual key presence was `true`, expected `false`.

## GREEN Evidence

- `node frontend/scripts/check-research-reviews.mjs`: passed the compiled-component review checks, including both new regressions.
- `frontend/node_modules/.bin/eslint frontend/src/components/Research/ReviewEditor.vue frontend/src/components/Research/DecisionEditor.vue frontend/scripts/check-research-reviews.mjs`: passed.
- `frontend/node_modules/.bin/prettier --check frontend/src/components/Research/ReviewEditor.vue frontend/src/components/Research/DecisionEditor.vue frontend/scripts/check-research-reviews.mjs`: passed.
- `npm --prefix frontend run bundle`: passed.
- `frontend/scripts/check-research-final-browser.py` via local Vite and fixture-backed API: passed at `1440x900` and `390x844`, with no page or console errors.
- `git diff --check`: passed.

## Files Changed

- `frontend/src/components/Research/ReviewEditor.vue`
- `frontend/src/components/Research/DecisionEditor.vue`
- `frontend/scripts/check-research-reviews.mjs`

## Concerns

No behavioral concerns remain. The production bundle retains pre-existing Sass deprecation, Rollup annotation, and large-chunk warnings. The browser matrix requires a local listener permission in this sandbox and uses only intercepted/in-memory fixtures.

## Fix Round 1: Effective Decision-Review Context

Implementation commit: `5de9d935` (`fix(research): show effective decision review context`)

### RED Evidence

1. `node frontend/scripts/check-research-reviews.mjs` failed before the production change because `app.contextValue` did not exist; the new behavior requires separately evaluating effective checkbox defaults from persisted metadata.
2. The fixture-backed browser matrix failed its new decision-review assertion before the production change: `真实持仓` was `unchecked`, while the backend-effective default is enabled.

### GREEN Evidence

- `node frontend/scripts/check-research-reviews.mjs`: passed effective real default, paper default derived from a paper-account decision reference, omitted creation keys, and explicit one-click false override behavior.
- `frontend/node_modules/.bin/eslint frontend/src/components/Research/ReviewEditor.vue frontend/scripts/check-research-reviews.mjs`: passed.
- `frontend/node_modules/.bin/prettier --check frontend/src/components/Research/ReviewEditor.vue frontend/scripts/check-research-reviews.mjs`: passed.
- `/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python -m py_compile frontend/scripts/check-research-final-browser.py`: passed.
- `npm --prefix frontend run bundle`: passed.
- The fixture-backed Playwright browser path passed separately at `1440x900` and `390x844`, each with 74 API requests and no page or console errors. It confirms a draft decision with real/paper links, unchanged post-confirm replacement save, decision-review effective checkbox defaults, omitted persisted holding keys, and responsive layout checks.
- `git diff --check`: passed.

### Files Changed

- `frontend/src/components/Research/ReviewEditor.vue`
- `frontend/scripts/check-research-reviews.mjs`
- `frontend/scripts/check-research-final-browser.py`

### Concerns

No behavioral concerns remain. The optional Python `ruff` command is unavailable in the project environment; direct Python compilation passed. Existing Vite warnings remain unchanged.

## Fix Round 2: Effective Decision Holdings Readiness

Implementation commit: `f355a565` (`fix(research): wait for effective review holdings`)

### RED Evidence

`node frontend/scripts/check-research-reviews.mjs` failed before the production change with `effective decision holdings must delay creation and confirmation until sources resolve`; it made 3 API calls before the deferred holdings source resolved, expected 0.

### GREEN Evidence

- `node frontend/scripts/check-research-reviews.mjs`: passed after `associationsReady` began using the existing effective-context accessor.
- Fixture-backed browser matrix: passed independently at `1440x900` and `390x844`, each with 74 API requests and no page or console errors. It covers decision links introduced after editor mount and retained by an unchanged association save, plus effective decision-review defaults.
- `frontend/node_modules/.bin/eslint frontend/src/components/Research/ReviewEditor.vue frontend/scripts/check-research-reviews.mjs`: passed.
- `frontend/node_modules/.bin/prettier --check frontend/src/components/Research/ReviewEditor.vue frontend/scripts/check-research-reviews.mjs`: passed.
- `/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python -m py_compile frontend/scripts/check-research-final-browser.py`: passed.
- `npm --prefix frontend run bundle`: passed.
- `git diff --check`: passed.

### Files Changed

- `frontend/src/components/Research/ReviewEditor.vue`
- `frontend/scripts/check-research-reviews.mjs`
- `frontend/scripts/check-research-final-browser.py`

### Concerns

No behavioral concerns remain. The bundle retains existing Sass deprecation, Rollup annotation, and large-chunk warnings.

## Fix Round 3: Decision Link Injection Mount Gate

Implementation commit: `d65f8c57` (`test(research): gate decision links on editor mount`)

### Evidence

- The initially unlinked decision fixture now asserts `.decision-editor .markdown-editor textarea` is visible immediately after its list row is selected and before `service.set_decision_trade_links(...)` injects real/paper links. This makes the confirmation-response synchronization path load-bearing.
- `/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python -m py_compile frontend/scripts/check-research-final-browser.py`: passed.
- `git diff --check`: passed; no repository Python formatter is configured for this script.
- Fixture-backed browser matrix: passed independently at `1440x900` and `390x844`, each with 74 API requests and no page or console errors. Both runs covered `confirmed decision links`, including unchanged post-confirm link save.

### Files Changed

- `frontend/scripts/check-research-final-browser.py`

### Concerns

No behavioral concerns remain.
