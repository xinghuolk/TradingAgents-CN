# Technical Debt

This list keeps accepted legacy failures visible without making the personal quick
gate permanently red. Counts are snapshots from 2026-09-07 and should be re-measured
when the related area changes.

| Priority | Area | Current evidence | Impact | Revisit trigger |
| --- | --- | --- | --- | --- |
| High | Broad Python tests | Collection from a temporary working directory reaches 16 errors, including removed imports and MongoDB work during import. Running in the repository also encounters log-file permission failures. | `pytest tests/` cannot be the normal gate. | A touched failing module becomes service-free or its external setup moves out of collection. |
| High | Frontend type safety | `vue-tsc --noEmit` reports about 240 errors across 41 files. | Type checking cannot block ordinary changes yet. | Fix errors by feature area and make type-check blocking only at zero. |
| Medium | Frontend lint and format | Non-mutating lint reports 57 source errors; Prettier reports 103 files with drift. | These diagnostics cannot block ordinary changes yet. | Clean only touched areas until lint and formatting diagnostics are green. |
| Medium | Package boundary | 11 import sites under `tradingagents/` load proprietary `app/` modules, often dynamically. | The reusable core is coupled to the application layer. | When one import site is touched, move the interface toward the core or inject the application dependency; enforce only after violations are removed or baselined. |
| Medium | Documentation links | A repository-wide Markdown scan reports roughly 334 unresolved local links, primarily in historical documents. | Older guidance is difficult to navigate and cannot all be structurally gated. | Repair links in any historical document being edited; gate only the canonical map initially. |
| Low | Large source files | 24 production files exceed 1,000 lines. | Review and testing are harder in those areas, but line count alone does not prove a defect. | Split a file only when feature work identifies a coherent boundary; do not add a global size gate. |

## Maintenance Rule

When work touches an entry, update its evidence and status in the same commit. Remove
an entry only after the relevant deterministic check is green, and add that check to
the quick harness when it is inexpensive and service-free.
