"""Authoritative list of OAuth subscription provider names.

DB-driven code paths SHOULD prefer reading `LLMProvider.auth_kind` directly
(it's the source of truth in MongoDB after the OAuth-first-class refactor).

Non-DB code paths consult this constant:
  - `app/core/config_bridge.py:161` (JSON fallback path; no LLMProvider object)
  - `tradingagents/agents/utils/memory.py` (Apache core; cannot import from `app/`)

Keep this set in sync with the `auth_kind="oauth"` rows in `llm_providers`.
"""

from __future__ import annotations

OAUTH_SUBSCRIPTION_PROVIDER_NAMES: frozenset[str] = frozenset({"codex", "claude_code"})
