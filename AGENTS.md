# Repository Guidelines

## Project Structure & Module Organization
- `tradingagents/`: core multi-agent trading logic, dataflows, adapters, and tools.
- `app/`: FastAPI backend (routers, services, middleware, worker tasks).
- `frontend/`: Vue 3 + TypeScript UI (views, components, stores, api modules).
- `cli/`: Typer-based CLI entrypoints and terminal UX.
- `tests/`: pytest suite (unit/integration/debug helpers) with config in `tests/pytest.ini`.
- `scripts/`: operational and maintenance scripts (deployment, validation, diagnostics).
- `docs/`, `assets/`, `data/`: documentation, media, and local data artifacts.

## Build, Test, and Development Commands
- Install Python deps: `pip install -r requirements.txt`
- Run backend locally: `python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- Run CLI: `python -m cli.main`
- Run tests (project default filters apply): `python -m pytest tests/`
- Frontend setup: `cd frontend && npm install`
- Frontend dev server: `cd frontend && npm run dev`
- Frontend production build: `cd frontend && npm run build`
- Full stack via containers: `docker compose up -d`

## Coding Style & Naming Conventions
- Python: PEP 8 style, 4-space indentation, `snake_case` for functions/files, `PascalCase` for classes.
- Frontend: TypeScript + Vue SFCs, 2-space indentation, single quotes, no semicolons (see `frontend/.prettierrc.json`).
- Vue component files use `PascalCase` (for example, `TaskCenter.vue`); view folders may group by domain (for example, `views/Analysis/`).
- Run frontend quality checks before PRs: `npm run lint`, `npm run type-check`, `npm run format`.

## Testing Guidelines
- Framework: `pytest` (with `pytest-asyncio` for async flows).
- Test files: `test_*.py`; test functions: `test_*`.
- Default pytest config skips `integration` marker and a few heavy cases; see `tests/pytest.ini`.
- Add focused tests near related modules (for example, `tests/tradingagents/` for core logic changes).

## Commit & Pull Request Guidelines
- Follow existing commit style: Conventional prefixes such as `fix:`, `feat:`, `refactor:`, `chore:` (Chinese/English descriptions are both used).
- Keep commits scoped to one concern and include impacted module names when helpful.
- PRs should use `.github/pull_request_template.md`: include change summary, linked issue, test steps/results, impact scope, and screenshots for UI changes.
- For config/adapter changes, document required `.env` keys and migration steps in the PR description.

## Security & Configuration Tips
- Never commit secrets; use `.env` (copy from `.env.example`).
- Validate key services (MongoDB/Redis/API keys) before integration tests or Docker runs.
