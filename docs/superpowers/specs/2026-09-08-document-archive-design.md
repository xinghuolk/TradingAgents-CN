# Documentation Archive Design

## Goal

Reduce the current documentation surface to a small, trustworthy set for a personal
maintainer while preserving useful project history. Current instructions must be easy
to find and validate; historical notes must not be mistaken for current behavior.

## Current Evidence

- `docs/` contains 635 tracked files, including 630 Markdown documents.
- The largest areas are `fixes/` (73), `guides/` (53), `superpowers/` (50),
  `design/` (41), and `features/` (38).
- About 286 paths contain process-oriented terms such as analysis, plan, report,
  summary, completion, migration, or fix.
- 65 Markdown documents mention Streamlit or port 8501 even though the current
  runtime is FastAPI plus Vue.
- The accepted baseline contains 307 unresolved local links across 114 historical
  documents.
- Root and GitHub workflow templates still contain links to missing legacy guides.

Dates are supporting evidence only. A document is archived because it describes
historical behavior or a completed change, not merely because it is old.

## Information Model

The documentation has four statuses. Directory placement should make the status
obvious without requiring front matter in hundreds of files.

| Status | Purpose | Treatment |
| --- | --- | --- |
| Current | Instructions that describe the checked-out application | Listed from `docs/README.md` and checked by the harness |
| Reference | Stable technical detail that remains true but is not a normal workflow | Linked from a current document when relevant |
| Historical | Releases, decisions, investigations, completed plans, and incident records | Moved below `docs/archive/` and excluded from current-document validation |
| Redundant | Exact duplicates or transient reports with no unique durable information | Removed after confirming the useful information exists elsewhere; Git retains history |

## Target Layout

```text
docs/
|-- README.md
|-- development.md
|-- testing.md
|-- technical-debt.md
|-- current/
|   |-- getting-started.md
|   |-- configuration.md
|   |-- usage.md
|   |-- deployment.md
|   `-- troubleshooting.md
|-- reference/
|   |-- api/
|   |-- data-sources/
|   `-- llm/
|-- releases/
|   `-- CHANGELOG.md
`-- archive/
    |-- README.md
    |-- versions/
    |-- engineering-plans/
    |-- fixes-and-incidents/
    |-- implementation-reports/
    `-- announcements/
```

`ARCHITECTURE.md` remains the canonical current architecture document at the
repository root. Binary papers and documentation assets remain in their existing
dedicated directories unless a later audit finds they are unused.

## Classification Rules

A document is a high-confidence archive candidate when at least two of these signals
apply:

- it describes Streamlit, port 8501, removed scripts, or another retired runtime;
- its commands, supported Python version, configuration keys, or deployment model
  conflict with canonical current documentation and checked-in code;
- it is explicitly a completed fix, implementation summary, phase report, migration
  record, meeting note, review, or old-version design;
- it is not reachable from the current documentation index and does not provide a
  stable reference needed by current code or operations.

Versioned release notes and blog posts are historical by definition, but may remain
in clearly labeled history directories when moving them would add no navigational
value. A single weak signal, such as age or lack of inbound links, is not enough to
delete a document.

## Migration Strategy

The migration is deliberately staged so each commit is understandable and easy to
reverse.

1. Establish the current documentation index, archive policy, and a measured
   inventory. Repair missing links in active repository templates.
2. Consolidate one current guide each for getting started, configuration, usage,
   deployment, and troubleshooting. A legacy guide is not archived until its useful
   current instructions have been retained in the replacement.
3. Move high-confidence historical groups by concern. Start with `fixes/`, `bugfix/`,
   `summary/`, `changes/`, `migration/`, `improvements/`, and `tech_reviews/`.
4. Review mixed directories such as `guides/`, `configuration/`, `deployment/`,
   `features/`, `llm/`, and `integration/` topic by topic. Do not bulk-classify these
   directories.
5. Archive completed versioned designs and agent plans only after confirming their
   implementation status. Active or uncertain plans stay in place.
6. Remove confirmed duplicates in small, separately reviewed commits.

Moves use `git mv` so repository history remains easy to follow. Each move commit
updates active inbound links and records the classification rationale in its commit
message. Archive documents are not rewritten merely to modernize their wording.

## Validation

The harness should validate:

- canonical repository documents;
- every Markdown document under `docs/current/`;
- current documents linked directly from `docs/README.md`;
- active links in README and GitHub contribution templates that point into `docs/`;
- the existence of `docs/archive/README.md` and the documented archive boundary.

Historical archive links remain non-blocking. The existing 307-link legacy baseline
should fall as documents are consolidated, but this project will not spend effort
repairing links inside frozen historical records.

The full `python scripts/harness.py` command remains the final gate after each batch.
Documentation-only commits may run the structural phase first for fast feedback.

## Maintenance Rules

- One current task has one canonical guide; related documents link to it rather than
  copy its commands.
- Behavior, configuration, or deployment changes update their canonical document in
  the same commit.
- New fix summaries and completion reports are not added by default. Durable lessons
  belong in current docs, architecture, tests, or technical debt; the commit history
  records the completed work.
- When a historical document becomes relevant again, verify it against code before
  promoting it back into the current set.

## Non-Goals

- Repairing every historical broken link.
- Adding a documentation site generator, search service, ownership system, or review
  bot.
- Rewriting all archived content for consistent style.
- Deciding implementation status from filenames alone.
- Turning documentation cleanup into application refactoring.

