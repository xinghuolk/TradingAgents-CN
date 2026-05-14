# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAgents-CN is a Chinese-localized multi-agent stock analysis platform built on top of [Tauric Research's TradingAgents](https://github.com/TauricResearch/TradingAgents). It is an educational/research framework — **not** a live trading system. The repository contains v1.0.0-preview, which is a major architectural rewrite from Streamlit (v0.1.x) to **FastAPI + Vue 3 + MongoDB + Redis**.

The codebase is bilingual: identifiers and APIs are mostly English; user-facing strings, log messages, and most code comments are Chinese. Preserve this convention when editing.

## Licensing Split (Important)

The repo ships under a **hybrid license** (see `LICENSE`, `LICENSING.md`):

- **Apache 2.0**: everything *except* `app/` and `frontend/`. The `tradingagents/` core package, `cli/`, `web/`, `scripts/`, `tests/`, docs, etc. are open.
- **Proprietary** (commercial license required): `app/` (FastAPI backend) and `frontend/` (Vue 3 SPA).

When modifying or copying code, respect the license boundary. Files inside `app/` and `frontend/` often carry an explicit proprietary header.

## High-Level Architecture

There are **three runnable surfaces** plus the core library. Knowing which one you're touching is essential.

```
┌─────────────────────────────────────────────────────────────┐
│  frontend/  (Vue 3 + Vite + Element Plus + Pinia)           │  proprietary
│  └──> talks to FastAPI over REST + WebSocket/SSE             │
├─────────────────────────────────────────────────────────────┤
│  app/  (FastAPI backend, port 8000)                          │  proprietary
│  ├── routers/         REST endpoints                         │
│  ├── services/        business logic                         │
│  ├── worker/          sync jobs (Tushare/AKShare/BaoStock)   │
│  ├── core/            config, db, logging, redis             │
│  └── main.py          app entry + APScheduler bootstrap      │
├─────────────────────────────────────────────────────────────┤
│  tradingagents/  (the multi-agent core, LangGraph-based)     │  Apache 2.0
│  ├── graph/         TradingAgentsGraph orchestrator          │
│  ├── agents/        analysts, researchers, trader, risk_mgmt │
│  ├── llm_adapters/  provider-specific LLM wrappers           │
│  ├── dataflows/     market data providers + cache + news     │
│  └── default_config.py                                       │
├─────────────────────────────────────────────────────────────┤
│  cli/  (Typer/Rich CLI)        web/  (legacy Streamlit UI)   │
└─────────────────────────────────────────────────────────────┘
                   │
                   ▼
         MongoDB + Redis + ChromaDB (memory)
```

### How the FastAPI backend invokes the core

`app/` does **not** import `tradingagents/` directly for analysis at request time. Instead:

1. The web UI writes LLM providers, data-source settings, API keys, etc. into MongoDB via `app/services/config_service.py`.
2. On startup (`app/main.py` lifespan) and before each analysis run, `app/core/config_bridge.py::bridge_config_to_env()` projects that DB config into **environment variables** that `tradingagents/` reads (e.g. `DASHSCOPE_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `TRADINGAGENTS_MONGODB_URL`, `TRADINGAGENTS_REDIS_URL`).
3. `app/services/analysis_service.py` (or the worker) constructs a config dict and calls `tradingagents.graph.trading_graph.TradingAgentsGraph(...).propagate(symbol, date, progress_callback)`.

**Implication**: if you change a config key the core relies on, you usually need to update `config_bridge.py`, the `Settings` model in `app/core/config.py`, and `tradingagents/default_config.py` together. Adding a config in only one place silently does nothing.

### The multi-agent graph (`tradingagents/graph/`)

`TradingAgentsGraph` is a LangGraph state machine. Nodes — in execution order — are:

1. **Analysts**: `Market Analyst` → `Fundamentals Analyst` → `News Analyst` → `Social Analyst` (selectable subset via `selected_analysts`). Each has a paired `tools_<name>` node and a `Msg Clear <Name>` node.
2. **Research debate**: `Bull Researcher` ↔ `Bear Researcher` for `max_debate_rounds` rounds, then `Research Manager` makes a call.
3. **Trader**: `Trader` produces an investment plan.
4. **Risk debate**: `Risky Analyst` / `Safe Analyst` / `Neutral Analyst` debate for `max_risk_discuss_rounds` rounds, then `Risk Judge` (Risk Manager) issues the final decision.

The orchestrator records per-node timings into `final_state["performance_metrics"]`. The progress-callback message names in `_send_progress_update` (e.g. `"Market Analyst"`, `"Risk Judge"`) must stay in sync with the LangGraph node names defined in `graph/setup.py` — they are used by the web UI to render progress.

Two LLM roles are instantiated per run: `quick_thinking_llm` and `deep_thinking_llm`. They may come from **different providers** ("mixed mode") when `quick_provider != deep_provider` in the config. The huge `if/elif` ladder in `TradingAgentsGraph.__init__` handles per-provider quirks; the helper `create_llm_by_provider(...)` covers most providers and any unknown provider falls through to a generic OpenAI-compatible client. When adding a new provider, prefer extending `create_llm_by_provider` and `tradingagents/llm_adapters/openai_compatible_base.py` over adding another `elif` branch.

Long-term memory is `FinancialSituationMemory` (ChromaDB-backed), one collection per agent role; disable via `config["memory_enabled"] = False`.

### Data layer (`tradingagents/dataflows/`)

- `interface.py` is the public surface — all market-data calls go through it.
- `data_source_manager.py` decides which provider to use per market and handles failover.
- `providers/{china,hk,us}/` hold per-market adapters (Tushare, AKShare, BaoStock, TDX for A股; Yahoo Finance, Finnhub for US; etc.).
- `cache/` is a layered cache: file → MongoDB → Redis, selected via the `TRADINGAGENTS_CACHE_TYPE` env var (`redis` in Docker, defaults otherwise).
- **A股 data** has scheduled sync jobs (see below). **HK and US data are on-demand + cached** — there is intentionally no scheduled sync for them; do not add one without discussion.

### Scheduled jobs (APScheduler, started in `app/main.py`)

The FastAPI lifespan adds a large set of cron jobs, one matrix per data source × task type:

- `tushare_{basic_info,quotes,historical,financial,status_check}_sync`
- `akshare_{basic_info,quotes,historical,financial,status_check}_sync`
- `baostock_{basic_info,daily_quotes,historical,status_check}_sync` (no realtime quotes — BaoStock doesn't support it)
- `quotes_ingestion_service` (interval, every `QUOTES_INGEST_INTERVAL_SECONDS`)
- `news_sync` (AKShare, **favorites-only** by design)

Each job is added to the scheduler unconditionally but **paused** if its `<SOURCE>_<TASK>_SYNC_ENABLED` flag is false — the corresponding CRON string still has to be a valid cron expression or the app crashes on startup. The scheduler instance is shared with the API via `set_scheduler_instance` so `app/routers/scheduler.py` can pause/resume/run jobs at runtime.

## Commands

### Backend (FastAPI)

```bash
# Install Python deps (Python ≥ 3.10)
pip install -r requirements.txt        # or: uv sync

# Run backend with auto-reload (reads .env from project root)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# or:
python -m app

# OpenAPI docs at http://localhost:8000/docs  (only when DEBUG=true)
```

The backend will **fail to start** if MongoDB or Redis is unreachable, or if `startup_validator` finds critical config missing. Either run the stack via Docker Compose or point `MONGODB_*` / `REDIS_*` env vars at a running instance.

### Frontend (Vue 3)

```bash
cd frontend
yarn install
yarn dev           # vite dev server (default 5173)
yarn build         # vue-tsc + vite build
yarn type-check    # vue-tsc --noEmit
yarn lint          # eslint --fix
yarn format        # prettier
```

`VITE_API_BASE_URL` (defaults to `http://localhost:8000` in Docker) controls the backend address.

### CLI

```bash
python -m cli.main             # interactive CLI (Rich/Typer)
# or after `pip install -e .`:
tradingagents                  # entrypoint defined in pyproject.toml -> main:main
```

### Legacy Streamlit web (v0.1.x UI, still functional)

```bash
python web/run_web.py
```

### Tests

`tests/pytest.ini` pins the test root to `tests/` only (the many `test_*.py` files at the repo root and inside `scripts/` are dev scratch scripts, **not** pytest tests — don't try to run them as a suite).

```bash
pytest                                    # default suite (integration tests excluded)
pytest -m integration                     # run only integration/e2e tests
pytest tests/unit/                        # one subtree
pytest tests/test_unified_news_tool.py    # one file
pytest tests/test_unified_news_tool.py::TestX::test_y   # one case
```

The default `addopts` skips `test_server_config` and `test_stock_codes` by name — they require a live server / network and fail in CI.

### Docker

```bash
docker-compose up -d                                  # backend + frontend + mongo + redis
docker-compose --profile management up -d             # also start mongo-express + redis-commander
docker-compose -f docker-compose.hub.nginx.yml up -d  # use prebuilt Docker Hub images behind nginx
```

Backend health: `curl http://localhost:8000/api/health`. Frontend: `http://localhost:3000`.

### Worker / sync scripts

Most one-off operational scripts live in `scripts/` (e.g. `scripts/akshare_force_sync_all.py`, `scripts/migrate_config_to_db.py`). They are intentionally standalone and not registered as console-entrypoints — invoke with `python scripts/<name>.py`. The same directory contains a large amount of historical debug/diagnose/test scripts; assume any `scripts/test_*.py` or `scripts/debug_*.py` is a one-off, not a regression test.

## Conventions & Gotchas

- **Logging**: always use `from tradingagents.utils.logging_manager import get_logger; logger = get_logger('<module>')` in core code, and `logging.getLogger("app.<name>")` / `"webapi"` / `"worker"` in `app/`. Do not add bare `print()` for production paths — `scripts/convert_prints_to_logs.py` exists because this was a recurring problem.
- **Async/event loop**: the core (`tradingagents/`) is mostly sync; the backend (`app/`) is async. There is a history of event-loop conflicts when invoking the sync graph from the async server (see recent commits "系统性解决事件循环冲突问题"). Avoid wrapping core calls in `asyncio.run` from inside an already-running loop — use `asyncio.to_thread` or the existing `analysis_service` helpers.
- **API key resolution order in the core**: `config["quick_api_key"] / config["deep_api_key"]` (from the DB-driven web config) → provider-specific env var (`DASHSCOPE_API_KEY`, `GOOGLE_API_KEY`, …) → `CUSTOM_OPENAI_API_KEY` fallback. When debugging "wrong key used", check `config_bridge.py` first.
- **Proxy + A股 data**: many A股 endpoints (eastmoney, tushare, baostock) must bypass any HTTP(S) proxy. `NO_PROXY` is preconfigured in `.env.example`; don't strip those domains.
- **Provider strings are case-sensitive in some places**: the core has lots of `.lower()` comparisons but a few exact-match config keys. When in doubt, follow the existing comparison style for the file you're editing.
- **Data files & reports** live under `data/`, `reports/`, `eval_results/`, and `logs/` — these are Docker volume mount points; don't reorganize them lightly.
- **`tradingagents/dataflows/_compat_imports.py`** exists to keep older import paths working after the dataflows refactor. If you rename a provider, update both the new path and this shim.
- **Cursor / Copilot rules**: none present in this repo.
