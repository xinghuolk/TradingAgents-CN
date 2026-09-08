# OAuth Providers as First-Class — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `codex` and `claude_code` first-class entries in `llm_providers` (with `auth_kind="oauth"`) so they flow through standard CRUD/filter/UI paths, eliminating scattered `("claude_code","codex")` hardcoding via a shared constant + the new `auth_kind` field. Bundle the `delete_llm_config` `.value` regression fix.

**Architecture:** A new `LLMProvider.auth_kind` field (default `"api_key"`, backfilled by Pydantic) classifies providers. Backend reads `auth_kind` where it has the DB object; non-DB code paths (`config_bridge.py:161` JSON fallback, `tradingagents/agents/utils/memory.py`) read a shared `OAUTH_SUBSCRIPTION_PROVIDER_NAMES` constant from a new Apache-side module. A new idempotent `POST /api/config/llm/providers/init-subscription` endpoint seeds the two OAuth providers. Frontend reads `auth_kind` from the API response, removes its synthetic `SUBSCRIPTION_PROVIDERS` injection in `LLMConfigDialog.vue`, and adapts `ProviderDialog.vue` to disable api_key/base_url for OAuth rows.

**Tech Stack:** Python 3.10+ (FastAPI / Pydantic v2 / Motor), pytest, Vue 3 + Element Plus + Pinia. No new deps.

**Spec:** `docs/superpowers/specs/2026-05-28-oauth-providers-as-first-class.md`

**Branch:** `feat/oauth-providers-first-class` (already created; spec commit `79699bf`).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tradingagents/utils/oauth_providers.py` | **Create** | Shared constant `OAUTH_SUBSCRIPTION_PROVIDER_NAMES = frozenset({"codex","claude_code"})` |
| `tests/unit/test_oauth_providers_constant.py` | **Create** | Constant shape test |
| `app/models/config.py` | Modify (lines 42-66, 121-143) | Add `auth_kind` to `LLMProvider` + `LLMProviderResponse`; deliberately NOT on `LLMProviderRequest` |
| `tests/unit/test_llm_provider_auth_kind.py` | **Create** | Field tests for the three models |
| `app/services/config_service.py` | Modify (lines 575-617 + new `init_subscription_providers`) | Fix `.value` regression; add seed method |
| `tests/unit/test_config_service_delete.py` | **Create** | `delete_llm_config` regression guard |
| `tests/unit/test_init_subscription_providers.py` | **Create** | Seed idempotency + field protection |
| `app/core/config_bridge.py` | Modify (lines 126, 161) | DB path reads `auth_kind`; JSON fallback reads constant |
| `tests/unit/test_config_bridge_oauth_skip.py` | **Create** | Both paths exempt OAuth |
| `tradingagents/agents/utils/memory.py` | Modify (line 111) | Use shared constant |
| `tests/unit/test_memory_oauth_block.py` | **Create** | Regression guard |
| `app/routers/config.py` | Modify (new `POST /init-subscription` near line 558) | Route the seed service |
| `frontend/src/types/config.ts` | Modify (lines 4-26) | Add `auth_kind?: 'api_key'\|'oauth'` |
| `frontend/src/api/config.ts` | Modify (after line 201) | Add `initSubscriptionProviders()` |
| `frontend/src/views/Settings/ConfigManagement.vue` | Modify (providers tab area + API密钥 column renderer) | Quick-add button + OAuth-row column display |
| `frontend/src/views/Settings/components/ProviderDialog.vue` | Modify (api_key + default_base_url fields) | Disable for `auth_kind=='oauth'`; add hint banner |
| `frontend/src/views/Settings/components/LLMConfigDialog.vue` | Modify (lines 397-418, 423-425, 557, 723, 787-803) | **Remove** synthetic `SUBSCRIPTION_PROVIDERS`; pivot `isSubscriptionProvider` to read `auth_kind` from selected provider |
| `docs/superpowers/specs/2026-05-28-oauth-providers-as-first-class.md` | (existing spec) | Reference |

---

# Part 1 — Backend foundations

## Task 1: Shared OAuth constant module

**Files:**
- Create: `tradingagents/utils/oauth_providers.py`
- Test: `tests/unit/test_oauth_providers_constant.py`

- [ ] **Step 1: Write the failing test**

```python
"""The shared OAuth-subscription-provider constant — single source of truth
for non-DB code paths (JSON fallback in config_bridge, Apache-side memory.py).
DB-backed code paths SHOULD prefer reading LLMProvider.auth_kind directly."""

from tradingagents.utils.oauth_providers import OAUTH_SUBSCRIPTION_PROVIDER_NAMES


def test_constant_is_immutable_frozenset():
    assert isinstance(OAUTH_SUBSCRIPTION_PROVIDER_NAMES, frozenset)


def test_contains_exactly_codex_and_claude_code():
    assert OAUTH_SUBSCRIPTION_PROVIDER_NAMES == frozenset({"codex", "claude_code"})


def test_membership_check_works():
    assert "codex" in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
    assert "claude_code" in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
    assert "openai" not in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
    assert "dashscope" not in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_oauth_providers_constant.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.utils.oauth_providers'`.

- [ ] **Step 3: Create the constant module**

Create `tradingagents/utils/oauth_providers.py` with EXACTLY:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_oauth_providers_constant.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/utils/oauth_providers.py tests/unit/test_oauth_providers_constant.py
git commit -m "feat(constants): shared OAuth subscription provider names"
```

---

## Task 2: Add `auth_kind` to `LLMProvider` + `LLMProviderResponse`; ensure `LLMProviderRequest` doesn't expose it

**Files:**
- Modify: `app/models/config.py` (line 56 area for `LLMProvider`; line 135 area for `LLMProviderResponse`; leave `LLMProviderRequest` untouched)
- Test: `tests/unit/test_llm_provider_auth_kind.py`

- [ ] **Step 1: Write the failing tests**

```python
"""auth_kind field on LLMProvider + LLMProviderResponse.
LLMProviderRequest deliberately does NOT expose this field — server forces
the default "api_key" on client-supplied add/update requests so untrusted
clients cannot create rogue OAuth providers with arbitrary names."""

import pytest

from app.models.config import LLMProvider, LLMProviderRequest, LLMProviderResponse


def test_llm_provider_default_is_api_key():
    p = LLMProvider(name="openai", display_name="OpenAI")
    assert p.auth_kind == "api_key"


def test_llm_provider_accepts_oauth():
    p = LLMProvider(name="codex", display_name="Codex", auth_kind="oauth")
    assert p.auth_kind == "oauth"


def test_llm_provider_rejects_unknown_auth_kind():
    with pytest.raises(Exception):
        LLMProvider(name="x", display_name="X", auth_kind="weird")


def test_legacy_doc_without_auth_kind_loads_with_default():
    # Existing Mongo docs predate this field — Pydantic must backfill default.
    legacy = {"name": "openai", "display_name": "OpenAI"}
    p = LLMProvider(**legacy)
    assert p.auth_kind == "api_key"


def test_llm_provider_response_serializes_auth_kind():
    r = LLMProviderResponse(
        id="000000000000000000000001",
        name="codex",
        display_name="Codex",
        is_active=True,
        supported_features=["chat"],
        auth_kind="oauth",
    )
    assert r.model_dump()["auth_kind"] == "oauth"


def test_llm_provider_response_default_is_api_key():
    r = LLMProviderResponse(
        id="000000000000000000000002",
        name="openai",
        display_name="OpenAI",
        is_active=True,
        supported_features=["chat"],
    )
    assert r.auth_kind == "api_key"


def test_llm_provider_request_does_not_expose_auth_kind():
    """Security guard: a client-side dict with auth_kind must be ignored by
    the request model. The server constructs LLMProvider with the default."""
    # The field must not be a declared attribute on the request model.
    assert "auth_kind" not in LLMProviderRequest.model_fields
    # Even if a client smuggles it in, Pydantic v2 with default config silently
    # drops unknown fields (model_config has no `extra="forbid"`).
    req = LLMProviderRequest(name="evil", display_name="Evil", auth_kind="oauth")
    assert not hasattr(req, "auth_kind")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_llm_provider_auth_kind.py -v`
Expected: FAIL — `LLMProvider` has no `auth_kind` field; the response test fails on construction; the request guard test fails for "auth_kind" not in model_fields.

- [ ] **Step 3: Edit `app/models/config.py`**

In `LLMProvider` (the class starting at line 42), add the new field. Find the line:

```python
    extra_config: Dict[str, Any] = Field(default_factory=dict, description="额外配置参数")
```

Immediately AFTER that line, BEFORE the `# 🆕 聚合渠道支持` comment, insert:

```python
    # 鉴权方式：api_key 走 {PROVIDER}_API_KEY 桥接；oauth 走 oauth_service 注入 token
    auth_kind: Literal["api_key", "oauth"] = Field(
        default="api_key",
        description="鉴权方式：api_key 或 oauth（订阅类）",
    )
```

In `LLMProviderResponse` (the class starting at line 121), add the same field. Find the line:

```python
    extra_config: Dict[str, Any] = Field(default_factory=dict)
```

Immediately AFTER it, BEFORE the `# 🆕 聚合渠道支持` comment, insert:

```python
    auth_kind: Literal["api_key", "oauth"] = "api_key"
```

Verify `from typing import Literal` is imported at the top of the file. If not (search the file's first 10 lines), add it:

```python
from typing import Literal
```

**Do NOT touch `LLMProviderRequest`** — leaving `auth_kind` off it is the security guard.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_llm_provider_auth_kind.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add app/models/config.py tests/unit/test_llm_provider_auth_kind.py
git commit -m "feat(models): add LLMProvider.auth_kind; expose on response; gate request"
```

---

## Task 3: Fix `delete_llm_config` `.value` regression

**Files:**
- Modify: `app/services/config_service.py` (lines 589, 597)
- Test: `tests/unit/test_config_service_delete.py`

- [ ] **Step 1: Write the failing test**

```python
"""delete_llm_config used `llm.provider.value` (legacy enum access). After
the enum→string migration, `provider` is a plain str, so .value raises
AttributeError, the outer try/except swallows it, and the router reports
'大模型配置不存在' for a config GET clearly returns. Guard against the
regression by exercising the str-provider path."""

import pytest

from app.models.config import LLMConfig, SystemConfig
from app.services.config_service import ConfigService


@pytest.mark.asyncio
async def test_delete_llm_config_matches_string_provider(monkeypatch):
    cfg = SystemConfig(
        llm_configs=[
            LLMConfig(provider="ollama", model_name="qwen3-8b", enabled=True),
            LLMConfig(provider="openai", model_name="gpt-4", enabled=True),
        ],
    )
    service = ConfigService.__new__(ConfigService)  # bypass __init__ DB connect

    async def fake_get_system_config():
        return cfg

    async def fake_save_system_config(updated):
        # Capture what was passed; assert post-delete state
        assert len(updated.llm_configs) == 1
        assert updated.llm_configs[0].model_name == "gpt-4"
        return True

    monkeypatch.setattr(service, "get_system_config", fake_get_system_config)
    monkeypatch.setattr(service, "save_system_config", fake_save_system_config)

    result = await service.delete_llm_config("ollama", "qwen3-8b")
    assert result is True


@pytest.mark.asyncio
async def test_delete_llm_config_returns_false_when_not_found(monkeypatch):
    cfg = SystemConfig(
        llm_configs=[LLMConfig(provider="openai", model_name="gpt-4", enabled=True)],
    )
    service = ConfigService.__new__(ConfigService)

    async def fake_get_system_config():
        return cfg

    async def fake_save_system_config(_):
        raise AssertionError("should not save when nothing matched")

    monkeypatch.setattr(service, "get_system_config", fake_get_system_config)
    monkeypatch.setattr(service, "save_system_config", fake_save_system_config)

    result = await service.delete_llm_config("nonexistent", "x")
    assert result is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config_service_delete.py -v`
Expected: FAIL — `AttributeError: 'str' object has no attribute 'value'` inside the loop or filter (swallowed → returns False → first test fails).

- [ ] **Step 3: Edit `app/services/config_service.py`**

Line 589 currently reads:
```python
                print(f"   {i+1}. provider: {llm.provider.value}, model_name: {llm.model_name}")
```
Change to:
```python
                print(f"   {i+1}. provider: {llm.provider}, model_name: {llm.model_name}")
```

Line 597 currently reads:
```python
                if not (str(llm.provider.value).lower() == provider.lower() and llm.model_name == model_name)
```
Change to:
```python
                if not (str(llm.provider).lower() == provider.lower() and llm.model_name == model_name)
```

Verify no other `.value` access on `llm.provider` remains in the file:
```bash
grep -n "llm.provider.value\|llm_config.provider.value" app/services/config_service.py
```
Expected: zero hits.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_config_service_delete.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/config_service.py tests/unit/test_config_service_delete.py
git commit -m "fix(config-service): delete_llm_config matches string provider (drop .value)"
```

---

## Task 4: `config_bridge.py` — DB path reads `auth_kind`; JSON fallback reads the constant

**Files:**
- Modify: `app/core/config_bridge.py` (line 126 and line 161)
- Test: `tests/unit/test_config_bridge_oauth_skip.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Both bridging paths must skip OAuth subscription providers when bridging
api_key env vars. DB path uses LLMProvider.auth_kind; JSON fallback uses
the shared constant (no LLMProvider object available there)."""

import os

from app.models.config import LLMProvider
from app.core.config_bridge import _provider_is_oauth_db, _provider_is_oauth_json


def test_db_path_recognizes_oauth_via_auth_kind():
    p = LLMProvider(name="codex", display_name="Codex", auth_kind="oauth")
    assert _provider_is_oauth_db(p) is True


def test_db_path_treats_api_key_provider_as_non_oauth():
    p = LLMProvider(name="openai", display_name="OpenAI")  # default api_key
    assert _provider_is_oauth_db(p) is False


def test_db_path_ignores_provider_name_when_auth_kind_is_api_key():
    """Defensive: even if a legacy row was named 'codex' but auth_kind defaults
    to api_key (no migration), we trust the field, not the name."""
    p = LLMProvider(name="codex", display_name="Codex Legacy")
    assert p.auth_kind == "api_key"
    assert _provider_is_oauth_db(p) is False


def test_json_path_recognizes_oauth_by_name():
    assert _provider_is_oauth_json("codex") is True
    assert _provider_is_oauth_json("claude_code") is True


def test_json_path_treats_unknown_name_as_non_oauth():
    assert _provider_is_oauth_json("openai") is False
    assert _provider_is_oauth_json("dashscope") is False
    assert _provider_is_oauth_json("") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_config_bridge_oauth_skip.py -v`
Expected: FAIL — `ImportError: cannot import name '_provider_is_oauth_db'` (and `_provider_is_oauth_json`).

- [ ] **Step 3: Edit `app/core/config_bridge.py`**

Near the top of the file (after the `logger = ...` line at line 12), add the two helpers:

```python
def _provider_is_oauth_db(provider) -> bool:
    """DB path: trust LLMProvider.auth_kind."""
    return getattr(provider, "auth_kind", "api_key") == "oauth"


def _provider_is_oauth_json(provider_name: str) -> bool:
    """JSON fallback path: no LLMProvider object available; consult the
    authoritative constant."""
    from tradingagents.utils.oauth_providers import OAUTH_SUBSCRIPTION_PROVIDER_NAMES
    return provider_name in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
```

Then update the two existing call sites:

**Line 126 (DB path)** currently reads:
```python
                if provider.name in ("claude_code", "codex"):
                    logger.info(
                        f"  ⏭️  跳过 OAuth 订阅厂家 {provider.name} 的 api_key 桥接 "
                        f"(token 由 analysis_service 在请求时注入)"
                    )
                    continue
```
Change the `if` condition to:
```python
                if _provider_is_oauth_db(provider):
                    logger.info(
                        f"  ⏭️  跳过 OAuth 订阅厂家 {provider.name} 的 api_key 桥接 "
                        f"(token 由 analysis_service 在请求时注入)"
                    )
                    continue
```

**Line 161 (JSON fallback path)** currently reads:
```python
                if llm_config.provider in ("claude_code", "codex"):
                    logger.info(
                        f"  ⏭️  跳过 OAuth 订阅 provider {llm_config.provider} 的 api_key 桥接 "
                        f"(token 由 analysis_service 在请求时注入)"
                    )
                    continue
```
Change the `if` condition to:
```python
                if _provider_is_oauth_json(llm_config.provider):
                    logger.info(
                        f"  ⏭️  跳过 OAuth 订阅 provider {llm_config.provider} 的 api_key 桥接 "
                        f"(token 由 analysis_service 在请求时注入)"
                    )
                    continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_config_bridge_oauth_skip.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/core/config_bridge.py tests/unit/test_config_bridge_oauth_skip.py
git commit -m "refactor(config-bridge): DB path reads auth_kind; JSON fallback reads constant"
```

---

## Task 5: `memory.py` — use shared constant

**Files:**
- Modify: `tradingagents/agents/utils/memory.py` (line 111)
- Test: `tests/unit/test_memory_oauth_block.py`

- [ ] **Step 1: Write the failing tests**

```python
"""FinancialSituationMemory must refuse to embed for OAuth-subscription
providers and require an explicit EMBEDDING_PROVIDER env var. Source the
list of OAuth providers from the shared constant, not hardcoded literals."""

import pytest

from tradingagents.agents.utils.memory import (
    FinancialSituationMemory,
    UnsupportedEmbeddingError,
)


def test_codex_without_embedding_provider_raises(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    with pytest.raises(UnsupportedEmbeddingError):
        FinancialSituationMemory("test", {"llm_provider": "codex", "memory_enabled": False})


def test_claude_code_without_embedding_provider_raises(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    with pytest.raises(UnsupportedEmbeddingError):
        FinancialSituationMemory("test", {"llm_provider": "claude_code", "memory_enabled": False})


def test_uppercase_codex_also_blocked(monkeypatch):
    """The check normalizes to lowercase — guard the lowercase comparison."""
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    with pytest.raises(UnsupportedEmbeddingError):
        FinancialSituationMemory("test", {"llm_provider": "CODEX"})


def test_api_key_provider_does_not_raise_oauth_error(monkeypatch):
    """openai should NOT trigger the OAuth-embedding block. (May raise other
    errors downstream during embedding-client setup if no key — that's fine,
    we only assert it's not UnsupportedEmbeddingError from the oauth gate.)"""
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    try:
        FinancialSituationMemory("test", {"llm_provider": "openai"})
    except UnsupportedEmbeddingError:
        pytest.fail("openai is api_key, must not hit OAuth embedding gate")
    except Exception:
        pass  # other downstream errors are out of scope for this test
```

- [ ] **Step 2: Run tests to verify they pass** (the original hardcoded check passes them, but we want to confirm the constant-based refactor preserves behavior)

Run: `.venv/bin/pytest tests/unit/test_memory_oauth_block.py -v`
Expected: PASS (4 passed) — the existing hardcoded `("claude_code","codex")` check works for these. This is the regression baseline.

- [ ] **Step 3: Edit `tradingagents/agents/utils/memory.py`**

Line 111 currently reads:
```python
        if self.llm_provider in ("claude_code", "codex"):
```

Change it to:
```python
        from tradingagents.utils.oauth_providers import OAUTH_SUBSCRIPTION_PROVIDER_NAMES
        if self.llm_provider in OAUTH_SUBSCRIPTION_PROVIDER_NAMES:
```

(Lazy import inside the method keeps module load light and avoids any circular import risk; matches the file's existing style of `import os` at top + lazy imports for specifics.)

- [ ] **Step 4: Run tests to verify they STILL pass** (refactor preserves behavior)

Run: `.venv/bin/pytest tests/unit/test_memory_oauth_block.py -v`
Expected: PASS (4 passed) — same outcome, now sourced from the constant.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/utils/memory.py tests/unit/test_memory_oauth_block.py
git commit -m "refactor(memory): source OAuth provider names from shared constant"
```

---

## Task 6: `init_subscription_providers` service + `POST /init-subscription` route

**Files:**
- Modify: `app/services/config_service.py` (add new `init_subscription_providers` method, mirror `init_aggregator_providers` shape at line 3023)
- Modify: `app/routers/config.py` (add route after the existing `init-aggregators` at line 558)
- Test: `tests/unit/test_init_subscription_providers.py`

- [ ] **Step 1: Write the failing tests**

```python
"""init_subscription_providers seeds the two OAuth providers idempotently.
First call: 2 created, 0 updated. Second call: 0 created, 2 updated.
Editable fields (display_name, description, is_active, supported_features)
are preserved on subsequent calls; only structural fields (auth_kind,
default_base_url, updated_at) get refreshed."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.config_service import ConfigService


class _FakeCollection:
    def __init__(self):
        self.docs = {}  # keyed by name

    async def find_one(self, query):
        return self.docs.get(query["name"])

    async def insert_one(self, doc):
        self.docs[doc["name"]] = dict(doc)

    async def update_one(self, query, update):
        existing = self.docs.get(query["name"])
        if existing is None:
            return MagicMock(matched_count=0)
        existing.update(update["$set"])
        return MagicMock(matched_count=1)


@pytest.mark.asyncio
async def test_first_call_creates_both_providers(monkeypatch):
    fake = _FakeCollection()
    fake_db = MagicMock()
    fake_db.llm_providers = fake
    service = ConfigService.__new__(ConfigService)
    monkeypatch.setattr(service, "_get_db", AsyncMock(return_value=fake_db))

    result = await service.init_subscription_providers()

    assert sorted(result["created"]) == ["claude_code", "codex"]
    assert result["updated"] == []
    assert fake.docs["codex"]["auth_kind"] == "oauth"
    assert fake.docs["claude_code"]["auth_kind"] == "oauth"
    assert fake.docs["codex"]["default_base_url"] == "https://chatgpt.com/backend-api/codex"
    assert fake.docs["claude_code"]["default_base_url"] == "https://api.anthropic.com"
    assert fake.docs["codex"]["supported_features"] == ["chat"]


@pytest.mark.asyncio
async def test_second_call_only_updates_structural_fields(monkeypatch):
    fake = _FakeCollection()
    fake_db = MagicMock()
    fake_db.llm_providers = fake
    service = ConfigService.__new__(ConfigService)
    monkeypatch.setattr(service, "_get_db", AsyncMock(return_value=fake_db))

    await service.init_subscription_providers()
    # Simulate user edits between calls
    fake.docs["codex"]["display_name"] = "My Custom Codex Name"
    fake.docs["codex"]["description"] = "edited by user"
    fake.docs["codex"]["is_active"] = False
    fake.docs["codex"]["supported_features"] = ["chat", "vision"]

    result = await service.init_subscription_providers()

    assert result["created"] == []
    assert sorted(result["updated"]) == ["claude_code", "codex"]
    # Editable fields preserved
    assert fake.docs["codex"]["display_name"] == "My Custom Codex Name"
    assert fake.docs["codex"]["description"] == "edited by user"
    assert fake.docs["codex"]["is_active"] is False
    assert fake.docs["codex"]["supported_features"] == ["chat", "vision"]
    # Structural fields still refreshed
    assert fake.docs["codex"]["auth_kind"] == "oauth"
    assert fake.docs["codex"]["default_base_url"] == "https://chatgpt.com/backend-api/codex"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_init_subscription_providers.py -v`
Expected: FAIL — `AttributeError: ConfigService has no attribute 'init_subscription_providers'`.

- [ ] **Step 3: Implement `init_subscription_providers` in `app/services/config_service.py`**

Add this method directly after `init_aggregator_providers` (around line 3127, before `migrate_env_to_providers`):

```python
    async def init_subscription_providers(self) -> Dict[str, list]:
        """Idempotently seed the two OAuth subscription providers (codex,
        claude_code) into `llm_providers`. First call creates both; subsequent
        calls only refresh structural fields (auth_kind, default_base_url,
        updated_at), preserving any user edits to display_name, description,
        is_active, supported_features.

        Returns {"created": [names...], "updated": [names...]}.
        """
        _SEEDS = [
            {
                "name": "codex",
                "display_name": "OpenAI Codex (订阅)",
                "auth_kind": "oauth",
                "default_base_url": "https://chatgpt.com/backend-api/codex",
                "is_active": True,
                "description": "ChatGPT 订阅式 Codex 模型，通过 OAuth 设备码授权使用",
                "supported_features": ["chat"],
            },
            {
                "name": "claude_code",
                "display_name": "Claude Code (订阅)",
                "auth_kind": "oauth",
                "default_base_url": "https://api.anthropic.com",
                "is_active": True,
                "description": "Anthropic Claude Code 订阅，通过 OAuth PKCE 授权使用",
                "supported_features": ["chat"],
            },
        ]

        db = await self._get_db()
        coll = db.llm_providers
        created: list = []
        updated: list = []

        for seed in _SEEDS:
            existing = await coll.find_one({"name": seed["name"]})
            if existing is None:
                full_doc = {
                    **seed,
                    "api_key": "",
                    "api_secret": "",
                    "extra_config": {},
                    "is_aggregator": False,
                    "aggregator_type": None,
                    "model_name_format": None,
                    "created_at": now_tz(),
                    "updated_at": now_tz(),
                }
                await coll.insert_one(full_doc)
                created.append(seed["name"])
            else:
                # Only refresh structural fields; preserve user-editable ones.
                await coll.update_one(
                    {"name": seed["name"]},
                    {"$set": {
                        "auth_kind": seed["auth_kind"],
                        "default_base_url": seed["default_base_url"],
                        "updated_at": now_tz(),
                    }},
                )
                updated.append(seed["name"])

        return {"created": created, "updated": updated}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_init_subscription_providers.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Add the route in `app/routers/config.py`**

Immediately AFTER the `init_aggregator_providers` endpoint (which ends at line 558), add:

```python
@router.post("/llm/providers/init-subscription", response_model=dict)
async def init_subscription_providers(
    current_user: User = Depends(get_current_user),
):
    """Idempotently seed OAuth subscription providers (codex, claude_code).

    Note: gated on `get_current_user` (any authenticated user). The repo has
    no admin dependency anywhere; all /api/config writes use the same gate.
    """
    try:
        result = await config_service.init_subscription_providers()
        try:
            await log_operation(
                user_id=str(getattr(current_user, "id", "")),
                username=getattr(current_user, "username", "unknown"),
                action_type=ActionType.CONFIG_MANAGEMENT,
                action="init_subscription_providers",
                details={
                    "created_count": len(result.get("created", [])),
                    "updated_count": len(result.get("updated", [])),
                },
                success=True,
            )
        except Exception:
            pass
        return {
            "success": True,
            "message": f"已创建 {len(result['created'])} 个，已更新 {len(result['updated'])} 个",
            "data": result,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"初始化订阅厂家失败: {str(e)}",
        )
```

- [ ] **Step 6: Smoke-test the import**

Run: `.venv/bin/python -c "import app.routers.config; print('ok')"`
Expected: prints `ok` (MongoDB connection failures from module-load are pre-existing and acceptable here — confirm there are no SYNTAX errors).

- [ ] **Step 7: Commit**

```bash
git add app/services/config_service.py app/routers/config.py tests/unit/test_init_subscription_providers.py
git commit -m "feat(config): init_subscription_providers service + POST /init-subscription"
```

---

# Part 2 — Frontend

> **Frontend testing**: the repo has no Jest/Vitest configured (verified by spec). Frontend tasks are TDD-less; verification is manual per the spec's §6 手测脚本 — captured as a final smoke checklist in Task 10.

## Task 7: Frontend type + API method + ConfigManagement quick-add button + OAuth-row column display

**Files:**
- Modify: `frontend/src/types/config.ts`
- Modify: `frontend/src/api/config.ts`
- Modify: `frontend/src/views/Settings/ConfigManagement.vue`

- [ ] **Step 1: Add `auth_kind` to `LLMProvider` type**

In `frontend/src/types/config.ts`, find the `LLMProvider` interface (lines 4-26). Add `auth_kind` as an optional field. The current `extra_config?: Record<string, any>` line is around line 19. AFTER it, BEFORE the `// 🆕 聚合渠道支持` comment, add:

```typescript
  auth_kind?: 'api_key' | 'oauth'
```

- [ ] **Step 2: Add `initSubscriptionProviders` API method**

In `frontend/src/api/config.ts`, find the existing `initAggregators` method (around line 200). Immediately AFTER it, add:

```typescript
  initSubscriptionProviders(): Promise<{ success: boolean; message: string; data: { created: string[]; updated: string[] } }> {
    return ApiClient.post('/api/config/llm/providers/init-subscription')
  },
```

- [ ] **Step 3: ConfigManagement.vue — Add quick-add button + OAuth column rendering**

Open `frontend/src/views/Settings/ConfigManagement.vue` and find the providers tab area (the section starting around line 88 with `<el-card v-show="activeTab === 'providers'" ...>`).

**(a)** Above the `<el-table :data="providers" ...>` (around line 100), add a button row. The exact insertion: just after `<div v-loading="providersLoading">` (line 99) and BEFORE `<el-table ...>` (line 100), insert:

```vue
            <div style="margin-bottom: 12px;">
              <el-button
                type="primary"
                size="small"
                @click="handleInitSubscriptionProviders"
                :loading="initSubscriptionLoading"
              >
                <el-icon><Plus /></el-icon>
                快速添加订阅厂家 (Codex / Claude Code)
              </el-button>
            </div>
```

If `Plus` isn't already imported from `@element-plus/icons-vue`, add it to the icon import line near the top of `<script setup>` (search for `from '@element-plus/icons-vue'`).

**(b)** Find the existing "API密钥" `<el-table-column>` (around line 109-120). It currently renders the api_key display. Wrap its rendered content in a `v-if`/`v-else` based on `row.auth_kind`. Replace the entire `<el-table-column label="API密钥" width="120">` block with:

```vue
              <el-table-column label="API密钥" width="120">
                <template #default="{ row }">
                  <span v-if="row.auth_kind === 'oauth'" style="color: var(--el-text-color-secondary);">—</span>
                  <el-tag
                    v-else
                    :type="row.extra_config?.has_api_key ? 'success' : 'danger'"
                    size="small"
                  >
                    {{ row.extra_config?.has_api_key ? '已配置' : '未配置' }}
                  </el-tag>
                </template>
              </el-table-column>
```

(If the current cell renders differently — e.g. shows a masked value or a different tag shape — preserve whatever the existing `v-else` branch logic was; only the `v-if="row.auth_kind === 'oauth'"` branch is new.)

**(c)** In the `<script setup>` of the same file, add the loading ref and the handler. Near the other `ref(false)` declarations (search for `providersLoading`), add:

```typescript
const initSubscriptionLoading = ref(false)
```

In the same script section, add this handler function (place it near other `handle*` functions; search for `handleReloadConfig` ~line 2034 for a reference location):

```typescript
const handleInitSubscriptionProviders = async () => {
  initSubscriptionLoading.value = true
  try {
    const result = await configApi.initSubscriptionProviders()
    const { created, updated } = result.data
    if (created.length > 0) {
      ElMessage.success(`已添加 ${created.length} 个订阅厂家：${created.join(', ')}`)
    } else if (updated.length > 0) {
      ElMessage.info(`订阅厂家已存在，已刷新 ${updated.length} 个`)
    } else {
      ElMessage.info('订阅厂家无变更')
    }
    await loadProviders()
  } catch (error) {
    console.error('❌ 初始化订阅厂家失败:', error)
    ElMessage.error('初始化订阅厂家失败')
  } finally {
    initSubscriptionLoading.value = false
  }
}
```

(`loadProviders` already exists — verify by grep; it reloads the providers table after a successful init.)

- [ ] **Step 4: Lint**

Run: `cd frontend && yarn type-check` (if available) OR `cd frontend && yarn lint`.
Expected: no NEW errors. Pre-existing warnings are acceptable; only flag new ones.
If neither script exists, skip — manual verification covers it.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/config.ts frontend/src/api/config.ts frontend/src/views/Settings/ConfigManagement.vue
git commit -m "feat(frontend): auth_kind type + initSubscriptionProviders API + quick-add UI"
```

---

## Task 8: `ProviderDialog.vue` — disable api_key + default_base_url for OAuth rows

**Files:**
- Modify: `frontend/src/views/Settings/components/ProviderDialog.vue`

- [ ] **Step 1: Compute `isOauthProvider`**

Open the file. In the `<script setup>` section, near the existing `needsApiSecret` computed (around line 215), add a new computed:

```typescript
const isOauthProvider = computed(() => formData.value.auth_kind === 'oauth')
```

(`formData` already exists. The `auth_kind` field needs to flow into it — see Step 4 below for the watcher that copies `auth_kind` from the prop into formData on edit.)

- [ ] **Step 2: Disable `api_key` input for OAuth rows**

Find the `<el-form-item label="API Key" prop="api_key">` (around line 120). Replace the inner `<el-input v-model="formData.api_key" ...>` element so it gains `:disabled="isOauthProvider"`. The minimal change: add `:disabled="isOauthProvider"` to the existing `<el-input>` props. Also add a brief help text. Approximate result:

```vue
      <el-form-item label="API Key" prop="api_key">
        <el-input
          v-model="formData.api_key"
          type="password"
          placeholder="请输入API密钥"
          show-password
          clearable
          :disabled="isOauthProvider"
        />
        <div v-if="isOauthProvider" style="font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px;">
          订阅类厂家无需 API Key，请前往"订阅授权"完成 OAuth 绑定。
        </div>
      </el-form-item>
```

- [ ] **Step 3: Disable `default_base_url` input for OAuth rows**

Find `<el-form-item label="默认API地址" prop="default_base_url">` (around line 95). Add `:disabled="isOauthProvider"` to the underlying input. Approximate result:

```vue
      <el-form-item label="默认API地址" prop="default_base_url">
        <el-input
          v-model="formData.default_base_url"
          placeholder="默认API地址"
          clearable
          :disabled="isOauthProvider"
        />
      </el-form-item>
```

- [ ] **Step 4: Ensure `auth_kind` flows from prop to formData**

Find the watcher/initializer that copies `props.provider` into `formData` (typically inside a `watchEffect` or a `watch(() => props.visible, ...)` or `onMounted`). Add `auth_kind` to the copied fields. Approximate addition wherever the formData is reset/seeded from props.provider:

```typescript
formData.value.auth_kind = props.provider?.auth_kind ?? 'api_key'
```

Also ensure the `FormData` interface near line 185-195 includes the field. Find the `interface FormData { ... }` (or the type used for `formData`) and add:

```typescript
  auth_kind?: 'api_key' | 'oauth'
```

- [ ] **Step 5: Lint**

Run: `cd frontend && yarn type-check` (if available).
Expected: no NEW errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Settings/components/ProviderDialog.vue
git commit -m "feat(frontend): ProviderDialog disables api_key/base_url for OAuth providers"
```

---

## Task 9: `LLMConfigDialog.vue` — remove synthetic `SUBSCRIPTION_PROVIDERS`; pivot to `auth_kind`

**Files:**
- Modify: `frontend/src/views/Settings/components/LLMConfigDialog.vue`

This is the most substantial frontend change. The file currently injects two synthetic providers (`claude_code`, `codex`) into the dropdown via `SUBSCRIPTION_PROVIDERS` (lines 401-416), uses a `SUBSCRIPTION_PROVIDER_NAMES` set (line 418) at five sites, and prepends the synthetic list when loading providers (line 787). After this task, real providers come from the DB (seeded via Task 6), so the synthetic list must be removed to avoid duplicates in the dropdown.

- [ ] **Step 1: Delete the synthetic provider constants**

Remove lines 400-418 entirely. The block to delete is:

```typescript
// 订阅类（OAuth）供应商不存在于 providers 表中——以合成项注入下拉
const SUBSCRIPTION_PROVIDERS: LLMProvider[] = [
  {
    id: 'claude_code',
    name: 'claude_code',
    display_name: 'Claude Code (订阅)',
    is_active: true,
    supported_features: ['chat'],
  } as LLMProvider,
  {
    id: 'codex',
    name: 'codex',
    display_name: 'Codex / ChatGPT (订阅)',
    is_active: true,
    supported_features: ['chat'],
  } as LLMProvider,
]

const SUBSCRIPTION_PROVIDER_NAMES = new Set(SUBSCRIPTION_PROVIDERS.map(p => p.name))
```

- [ ] **Step 2: Pivot `isSubscriptionProvider` to read `auth_kind`**

The current computed (lines 423-425) reads:
```typescript
const isSubscriptionProvider = computed(() =>
  SUBSCRIPTION_PROVIDER_NAMES.has(formData.value.provider),
)
```
Replace with:
```typescript
const selectedProvider = computed(() =>
  availableProviders.value.find(p => p.name === formData.value.provider),
)
const isSubscriptionProvider = computed(() =>
  selectedProvider.value?.auth_kind === 'oauth',
)
```

- [ ] **Step 3: Replace remaining `SUBSCRIPTION_PROVIDER_NAMES.has(...)` call sites**

There are three more usages in the file. For each:

**(a) Line ~557** — currently `if (SUBSCRIPTION_PROVIDER_NAMES.has(provider)) { ... }`. Replace with:
```typescript
const providerObj = availableProviders.value.find(p => p.name === provider)
if (providerObj?.auth_kind === 'oauth') { ... }
```
(Preserve the inner block exactly as it is.)

**(b) Line ~723** — currently `if (SUBSCRIPTION_PROVIDER_NAMES.has(formData.value.provider)) { ... }`. Replace with:
```typescript
if (isSubscriptionProvider.value) { ... }
```
(Re-uses the existing computed; cleaner.)

**(c) Line ~798** — currently `const firstReal = availableProviders.value.find(p => !SUBSCRIPTION_PROVIDER_NAMES.has(p.name))`. After this task, ALL providers in `availableProviders` come from the DB (no synthetic injection). The "skip subscription" logic is no longer relevant for default selection — pick the first active api_key provider:
```typescript
const firstReal = availableProviders.value.find(p => p.auth_kind !== 'oauth')
```

- [ ] **Step 4: Remove the synthetic prepend in `loadProviders`**

Find the block at lines 780-803. The current implementation merges `SUBSCRIPTION_PROVIDERS` with the DB result:

```typescript
const providers = await configApi.getLLMProviders()
availableProviders.value = [
  ...SUBSCRIPTION_PROVIDERS,
  ...providers.filter(p => p.is_active),
]
```

Replace with (just use the DB result, no prepend):

```typescript
const providers = await configApi.getLLMProviders()
availableProviders.value = providers.filter(p => p.is_active)
```

(After Task 6 seed runs, codex/claude_code are real DB rows and will appear in this list naturally.)

- [ ] **Step 5: Verify no stale references remain**

Run: `grep -n "SUBSCRIPTION_PROVIDERS\|SUBSCRIPTION_PROVIDER_NAMES" frontend/src/views/Settings/components/LLMConfigDialog.vue`
Expected: zero hits.

- [ ] **Step 6: Lint**

Run: `cd frontend && yarn type-check` (if available).
Expected: no NEW errors. Specifically: `LLMProvider` type now requires `auth_kind` to be optional (done in Task 7), so `p.auth_kind === 'oauth'` checks compile cleanly.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/Settings/components/LLMConfigDialog.vue
git commit -m "refactor(frontend): LLMConfigDialog reads auth_kind from DB providers (no synthetic list)"
```

---

## Task 10: Full backend verification + manual smoke checklist + docs

**Files:**
- (Optional) Modify: `README.md` or `docs/` — only if there's a clear documentation site for this feature; otherwise skip.

- [ ] **Step 1: Run the full new-tests subset**

Run:
```bash
.venv/bin/pytest tests/unit/test_oauth_providers_constant.py \
                 tests/unit/test_llm_provider_auth_kind.py \
                 tests/unit/test_config_service_delete.py \
                 tests/unit/test_config_bridge_oauth_skip.py \
                 tests/unit/test_memory_oauth_block.py \
                 tests/unit/test_init_subscription_providers.py -v
```
Expected: ALL PASS. Report exact count (should be approximately: 3 + 7 + 2 + 5 + 4 + 2 = 23).

- [ ] **Step 2: Run the broader unit suite (regression guard)**

Run: `.venv/bin/pytest tests/unit/ -q --ignore=tests/unit/test_unified_dataframe.py --ignore=tests/unit/test_stocks_kline_news_api.py 2>&1 | tail -5`
(The two ignores are pre-existing collection failures unrelated to this PR — confirm by running without ignores too: if those two fail and nothing else does, regression-clean.)
Expected: only pre-existing failures remain; no NEW failures introduced.

- [ ] **Step 3: Lint backend changes**

Run: `.venv/bin/ruff check app/models/config.py app/core/config_bridge.py app/services/config_service.py app/routers/config.py tradingagents/agents/utils/memory.py tradingagents/utils/oauth_providers.py`
Expected: no NEW findings (pre-existing findings in these files are acceptable; verify by running `git stash && ruff check ... && git stash pop` if uncertain).

- [ ] **Step 4: Manual smoke test checklist** (perform once the backend is running; this is the spec's §6 手测脚本)

```
[ ] Restart backend, log in as any user
[ ] Settings → 配置管理 → 厂家管理 tab
    → Click "快速添加订阅厂家 (Codex / Claude Code)"
    → Expect toast "已添加 2 个订阅厂家：codex, claude_code"
    → Table shows two new rows; their "API密钥" column shows "—"
[ ] Click the Edit (✏️) icon on the codex row
    → Dialog opens; "API Key" and "默认API地址" inputs are disabled
    → Hint text "订阅类厂家无需 API Key..." visible under API Key
    → "显示名称" is editable; "是否启用" toggle works
[ ] 配置管理 → 大模型配置 tab → click "添加配置"
    → provider dropdown shows "OpenAI Codex (订阅)" exactly ONCE (no duplicate)
    → Select it → api_key field is hidden (or hidden via isSubscriptionProvider branch)
    → Save a model (e.g. model_name = "gpt-5.5-codex")
    → It appears in the codex group in the LLM config list (i.e. /api/config/llm now returns it)
[ ] Try deleting a NON-codex LLM config (e.g. an existing ollama qwen3-8b)
    → Confirm the delete succeeds (regression fix from Task 3); no "大模型配置不存在" error
[ ] Click "快速添加订阅厂家" again
    → Toast "订阅厂家已存在，已刷新 2 个" (idempotency)
[ ] Open codex row again, set is_active = false
    → codex models in 大模型配置 disappear from list
    → Toggle back to active → models reappear
```

- [ ] **Step 5: Final commit** (only if there are remaining tracked-file changes)

```bash
git status --short  # should be clean after Tasks 1-9 committed
```

If clean, no further commit. If something accumulated (e.g. linter auto-fix), stage and commit with `style:` prefix.

---

## Self-Review (completed at authoring)

- **Spec coverage:**
  - §1 (Data model: LLMProvider/Response/Request auth_kind handling) → Task 2 ✅
  - §2.1 (shared constant module) → Task 1 ✅
  - §2.2 (init_subscription_providers service + endpoint with idempotent upsert preserving editable fields) → Task 6 ✅
  - §2.3 (config_bridge double-path refactor) → Task 4 ✅
  - §2.4 (memory.py refactor) → Task 5 ✅
  - §2.5 (analysis_service intentionally untouched — confirmed by absence in file structure)
  - §2.6 (get_llm_configs filter unchanged — confirmed; no task touches it, by design)
  - §2.7 (delete_llm_config .value fix) → Task 3 ✅
  - §3.1 (frontend types + API) → Task 7 (steps 1-2) ✅
  - §3.2 (ConfigManagement quick-add + col differentiation) → Task 7 (step 3) ✅
  - §3.3 (ProviderDialog adapt) → Task 8 ✅
  - §3.4 ("添加厂家" stays api_key-only) → no code change; the dialog doesn't expose auth_kind anyway → covered by Task 2's Request-model guard
  - §3.5 (LLMConfigDialog refactor) → Task 9 ✅
  - §6 (test plan) → Tasks 1-6 each include the spec'd tests; manual frontend checklist in Task 10
  - §5 (error handling/edges) → covered by tests in Task 2 (security guard), Task 6 (idempotency), Task 3 (delete regression); manual checklist confirms toggle/delete-restore
  - §8 (OOS) → respected; no task touches analysis_service, trading_graph, oauth_service, third tab consolidation

- **Placeholder scan:** No "TBD" / "implement later" / "handle edge cases". Every code step has full code blocks. Verified.

- **Type/name consistency:** `auth_kind` literal `'api_key'|'oauth'` is identical across `LLMProvider`, `LLMProviderResponse`, the helper functions `_provider_is_oauth_db`, `_provider_is_oauth_json`, the constant `OAUTH_SUBSCRIPTION_PROVIDER_NAMES`, the frontend `LLMProvider` interface, and the `isOauthProvider`/`isSubscriptionProvider` computeds. Seed payload fields (`name`, `display_name`, `auth_kind`, `default_base_url`, `is_active`, `description`, `supported_features`) match between Task 6's service and the manual checklist expectations.

- **Known plan risk** (intentional): Task 7-9 frontend steps describe edits relative to the current line numbers in the source files. If the file has drifted between plan-write and execution, the line numbers may need re-locating. The plan provides enough context (surrounding code blocks, grep commands) to recover. No automated frontend tests means line-drift won't be caught by CI; the manual checklist in Task 10 is the safety net.
