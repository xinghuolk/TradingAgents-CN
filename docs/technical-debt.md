# Technical Debt

This list keeps accepted legacy failures visible without making the personal quick
gate permanently red. Counts are snapshots from 2026-09-08 and should be re-measured
when the related area changes.

| Priority | Area | Current evidence | Impact | Revisit trigger |
| --- | --- | --- | --- | --- |
| High | Broad Python tests | Collection from a temporary working directory with isolated logs does not complete within 60 seconds. Earlier collection also exposed removed imports and database work during import. | `pytest tests/` cannot be the normal gate. | A touched failing module becomes service-free or its external setup moves out of collection. |
| High | Frontend type safety | `vue-tsc --noEmit` reports about 240 errors across 41 files. | Type checking cannot block ordinary changes yet. | Fix errors by feature area and make type-check blocking only at zero. |
| Medium | Python lock artifacts | `uv lock --check` exits 2. `uv.lock` still resolves OpenAI 1.x/LangChain OpenAI 0.x and an older financial-report extractor commit, while `requirements-lock.txt` is also behind current metadata. | Frozen installs can silently produce an environment that conflicts with declared application requirements. | Refresh both artifacts together when a reproducible full application environment is needed; verify their frozen install commands before documenting them again. |
| Medium | Frontend lint and format | Non-mutating lint reports 57 errors in 22 source files; Prettier reports 103 files with drift. | These diagnostics cannot block ordinary changes yet. | Clean only touched areas until lint and formatting diagnostics are green. |
| Medium | Package boundary | 26 executable import sites across 12 files under `tradingagents/` load proprietary `app/` modules. | The reusable core is coupled to the application layer. | When one import site is touched, move the interface toward the core or inject the application dependency; enforce only after violations are removed or baselined. |
| Medium | Documentation links | The harness-controlled current set has zero unresolved links; the harness parser reports 351 unresolved local links across 592 archive Markdown documents. | Older guidance is difficult to navigate and cannot all be structurally gated. | Repair an archive link only when its historical document is promoted or substantively edited; keep the harness-controlled current set at zero. |
| Low | Large source files | 26 production Python, Vue, and TypeScript files exceed 1,000 lines. | Review and testing are harder in those areas, but line count alone does not prove a defect. | Split a file only when feature work identifies a coherent boundary; do not add a global size gate. |

## Reproduce The Baseline

Run these diagnostics deliberately; non-zero status is expected until the matching
entry is cleared:

```bash
uv lock --check
npm --prefix frontend run type-check
npm --prefix frontend run lint
frontend/node_modules/.bin/prettier --check frontend/src
rg -n '^\s*(from app|import app)' tradingagents -g '*.py'
```

Run broad pytest collection only with an explicit timeout and isolated log directory.
It may initialize legacy external dependencies during import, so it is not part of
the normal personal workflow.

## Maintenance Rule

When work touches an entry, update its evidence and status in the same commit. Remove
an entry only after the relevant deterministic check is green, and add that check to
the quick harness when it is inexpensive and service-free.
