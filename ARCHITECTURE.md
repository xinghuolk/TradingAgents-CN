# Architecture

This document describes the current v1.0.0-preview runtime. Historical design and
release documents under `docs/` may describe the former Streamlit application and
must not be treated as the current system map.

## Runtime Components

| Area | Responsibility | Main entry point |
| --- | --- | --- |
| `frontend/` | Vue 3 single-page UI, Pinia state, API clients, and user workflows | `frontend/src/main.ts` |
| `app/` | FastAPI routes, application services, authentication, persistence, scheduled work, and worker processes | `app/main.py` |
| `tradingagents/` | LangGraph analysis workflow, LLM adapters, market-data providers, caching, and reusable domain logic | `tradingagents/graph/trading_graph.py` |
| `cli/` | Interactive terminal workflow over the analysis core | `cli/main.py` |

The browser calls the FastAPI API. API services validate requests, load persisted
configuration, and dispatch analysis or synchronization work. The application uses
MongoDB for durable state and Redis for caching, queues, and transient coordination.
Both services are required for the normal full application but are excluded from the
quick validation path.

## Analysis Flow

`TradingAgentsGraph` builds a LangGraph state machine for each analysis. Selected
market, fundamentals, news, and social analysts gather evidence; bull and bear
researchers debate it; a research manager produces a recommendation; a trader creates
the plan; and risk analysts plus the risk manager produce the final decision.

Node names and progress callback names are a UI contract. Changes in
`tradingagents/graph/` must remain aligned with the progress events consumed by
`app/services/analysis/` and `frontend/`.

## Configuration Flow

The web application stores provider, data-source, and API-key configuration through
`app/services/config_service.py`. Before analysis, `app/core/config_bridge.py`
projects applicable values into the environment expected by `tradingagents/`.
Configuration changes commonly need coordinated updates in:

- `app/core/config.py` for application settings;
- `app/core/config_bridge.py` for application-to-core mapping;
- `tradingagents/default_config.py` for core defaults;
- frontend settings forms and API types when users can edit the value.

Secrets belong in `.env` or the configured database, never in tracked files.

## Data And Background Work

The public market-data surface is `tradingagents/dataflows/interface.py`.
`data_source_manager.py` selects providers and failover behavior, while provider
implementations live under `tradingagents/dataflows/providers/`. Compatibility
imports in `_compat_imports.py` preserve older import paths.

APScheduler jobs are registered during the FastAPI lifespan in `app/main.py`.
A-share synchronization is scheduled by provider and task type. Hong Kong and US
market data are intentionally fetched on demand and cached. Worker entry points under
`app/worker/` execute queued analysis outside request handling.

Research owns `stock_research_workspaces`, `stock_research_entries`,
`stock_research_revisions`, and `stock_research_generation_tasks`. These collections
own thesis documents, manual entries, revisions, and links; analysis reports, real
portfolio facts, paper trades, and watchlists are read-only sources. References are
user-scoped display snapshots with source IDs. Research deletion never cascades to
source data, and real-account and paper-account facts remain separately labelled.
One trade can link to at most one decision; unlinked trades remain valid references.
`/research` is a searchable directory, and `/research/:code?market=...` is a document
workspace. Stock detail reads only directory summaries until the explicit workspace
command is used; this read does not create a workspace.

Entry content and lifecycle commands use conditional field updates with a write
version; stale saves return a conflict and cannot undo confirmation, archive, or
deletion. Revision pointers advance independently and never replace entry bodies.
Notes and research documents support labelled manual versions. Review confirmation
freezes the relevant current theses and creates workspace versions for existing
workspaces; absent workspaces are explicit in the review snapshot. Global reviews
remain manageable through directory archive and trash commands even without any
associated security.

Report ownership is read from `analysis_reports.user_id` when present, otherwise
from the report's `task_id` and an owned `analysis_tasks` record. Current real and
paper holding identities are read through `/api/research/references/holdings`,
independently of whether research workspaces exist. The generation context preview
and submission share a builder for scope, period, current theses, recent documents,
selected decisions, holdings, trades, and report summaries. Source failures and
unavailable market summaries are explicit; preview does not fetch external data.
The dialog saves pending edits before preview, and task submission freezes the
selected inputs and necessary display snapshots without copying full source facts.

Research AI drafts use `POST /api/research/generation-tasks` and a user-scoped GET
by task ID. The service freezes selected reference display snapshots and current
research inputs, persists `pending`, then schedules an in-process asyncio task.
Only active draft entries accept generated originals; human bodies are never
changed by generation. An atomic pending-to-running claim prevents duplicate runs.
Claim and terminal writes settle before cancellation is propagated; only a run
that acquired the claim can change its terminal state. Completion appends the
original and marks the task completed. If a write reports failure after applying,
the service preserves persisted completion or removes that task's append before
marking failure. This iteration does not provide
cross-collection crash atomicity, restart recovery, or a separate research worker.

Research generation reads the exact enabled model from the latest saved active
configuration and uses the shared LLM factory, without model fallback. OAuth
credentials are resolved for the submitting user at invocation time and are never
stored with tasks. Unsupported reasoning effort fails explicitly. The current
Codex adapter's effort path requests encrypted reasoning replay, so research drafts
accept Codex only with no explicit effort until that adapter supports effort alone.
Prompts and stored results contain public draft text, not internal reasoning.
The generation dialog uses the existing configured-model API and validates the
user's local last selection against enabled models. It never substitutes a model.
Only an open dialog polls, with one two-second timer and a 150-request bound;
terminal states, close, and unmount stop polling. A failed task is retried by an
explicit new submission. Originals retain model, effort, time, prompt, task, and
reference metadata independently of human text. Adopting an original requires a
confirmation and copies only its content into the human editor.

## Deployment Boundaries

Docker Compose runs the frontend, backend, MongoDB, and Redis. `Dockerfile.backend`
packages the Python application; `Dockerfile.frontend` builds static Vue assets and
serves them through Nginx. GitHub Actions publishes multi-architecture images on
version tags.

The repository has mixed licensing. `app/` and `frontend/` are proprietary components;
the remaining repository is governed by the root license. Cross-boundary imports that
currently point from `tradingagents/` into `app/` are recorded debt, not a new pattern
to copy.

## Change Rules

- Keep synchronous core work out of the FastAPI event loop; use existing service or
  thread boundaries.
- Keep graph node names and progress events aligned.
- Update every layer participating in a configuration key.
- Preserve the on-demand design for Hong Kong and US market data unless the
  architecture is intentionally revised.
- Update this document in the same commit when a component boundary or runtime flow
  changes.
