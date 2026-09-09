# Repository Map

TradingAgents-CN is a Python 3.11 application with four main surfaces:

- `tradingagents/`: the reusable multi-agent analysis and market-data core.
- `app/`: the FastAPI API, services, workers, persistence, and scheduling layer.
- `frontend/`: the Vue 3 and TypeScript browser application.
- `cli/`: the Typer terminal interface.

Use these repository-owned references instead of duplicating guidance here:

- [Architecture](ARCHITECTURE.md)
- [Development](docs/development.md)
- [Testing](docs/testing.md)
- [Technical debt](docs/technical-debt.md)
- [Documentation index](docs/README.md)

Keep changes focused and update the relevant reference when behavior, commands, or
boundaries change. Never commit credentials or local `.env` contents. Integration
checks that use MongoDB, Redis, market-data providers, or LLMs are opt-in; see the
testing guide before running them.
