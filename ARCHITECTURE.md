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
