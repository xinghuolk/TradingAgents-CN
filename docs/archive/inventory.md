# Documentation Inventory

This measured snapshot is dated 2026-09-08. Counts are Markdown documents only:
the reference count intentionally excludes the Python adapter template, and the
archive count includes this inventory. The categories are disjoint by directory,
except that runtime learning and paper material is reported as one combined
category.

| Category | Boundary | Count |
| --- | --- | ---: |
| Current | `docs/current/` | 6 |
| Reference | `docs/reference/` | 8 |
| Archive | `docs/archive/` | 592 |
| Runtime learning and paper | `docs/learning/` and `docs/paper/` | 11 |
| Retained analysis evidence | `docs/analysis/` | 12 |
| Unresolved archive local links | Every Markdown document under `docs/archive/` | 351 |

## Reproduce

Run these commands from the repository root. The first five commands count only
regular files ending in `.md`; they do not include directories, binary assets, or
the Python reference template.

```bash
find docs/current -type f -name '*.md' | wc -l
find docs/reference -type f -name '*.md' | wc -l
find docs/archive -type f -name '*.md' | wc -l
find docs/learning docs/paper -type f -name '*.md' | wc -l
find docs/analysis -type f -name '*.md' | wc -l
```

The archive-link value is deliberately measured with the repository harness's
structured local-link validator, rather than a separate Markdown parser:

```bash
/home/like/mycode/finanice/TradingAgents-CN/.venv/bin/python -c "from scripts.harness import ROOT, validate_local_links; documents = sorted((ROOT / 'docs/archive').rglob('*.md')); errors = [error for document in documents for error in validate_local_links(document, ROOT)]; print(f'{len(errors)} unresolved local links across {len(documents)} archive Markdown documents')"
```

On 2026-09-08, it prints `351 unresolved local links across 592 archive Markdown`
documents. Archive links are historical evidence and are non-blocking; repair one
only when its document is promoted or substantively edited.
