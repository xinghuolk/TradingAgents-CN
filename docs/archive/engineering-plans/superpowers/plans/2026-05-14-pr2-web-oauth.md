# PR-2: Web OAuth Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add browser-based OAuth authorization (Anthropic PKCE + Codex device code) for Claude Code / Codex subscriptions, with per-user encrypted token storage in MongoDB, and refactor the LLM provider plumbing to use a single unified entry point.

**Architecture:** Three layers built bottom-up. (1) `oauth_crypto.py` provides AES-256-GCM encrypt/decrypt with startup-validated key. (2) `oauth_service.py` orchestrates PKCE/device-code flows, persists encrypted tokens to MongoDB `user_oauth_credentials` collection, and exposes `resolve(user_id, provider)` with lazy refresh. (3) `routers/oauth.py` is the REST surface; `analysis_service` calls `resolve()` and injects the token via `config["quick_api_key"]` so the existing `ChatClaudeCodeOAuth` / `ChatCodexOAuth` adapters (modified to accept `access_token=`) authenticate against the upstream API. Same-PR cleanup: `TradingAgentsGraph.__init__` collapses ~400 lines of per-provider elif chain into a single `create_llm_by_provider` call; `create_llm_by_provider` retains 6 native provider branches plus the two OAuth branches, routing dashscope/qianfan/zhipu/siliconflow through the generic OpenAI-compatible fallback; `memory.py` disables itself when the LLM provider is OAuth and no independent embedding is configured.

**Tech Stack:** Python 3.10, FastAPI, Motor (MongoDB async), redis-py async, httpx (async HTTP for OAuth flows), `cryptography` (AES-256-GCM), Pydantic v2, pytest + pytest-asyncio + httpx mock transports.

**Spec:** `docs/superpowers/specs/2026-05-14-pr2-web-oauth-design.md`
**Builds on:** PR-1 (commits `e6ad1bb..13ba1d9` on `feat/codex-claude-subscription-analysis`, HEAD `58bc402`).

**Branch:** `feat/codex-claude-subscription-analysis` (current). Continue committing on this branch.

---

## Pre-flight

```bash
cd /Users/like/source/TradingAgents-CN

# Verify .venv from PR-1 still works
.venv/bin/python -c "import langchain_anthropic, langchain_openai, anthropic, httpx, pytest; print('OK')"

# Add cryptography to project deps and install (new dep for PR-2)
# Edit pyproject.toml: add `"cryptography>=42.0",` to the dependencies list.
# Then:
.venv/bin/python -m pip install "cryptography>=42.0" pytest-asyncio

# Confirm imports work
.venv/bin/python -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; print('AESGCM OK')"

# Baseline pytest (should be 63 from PR-1)
.venv/bin/python -m pytest tests/unit/ -q --ignore=tests/unit/dataflows --ignore=tests/unit/test_stocks_kline_news_api.py 2>&1 | tail -3
```

If `pytest-asyncio` isn't already in the venv, `pip install` it now. PR-2 has many async functions (`oauth_service.resolve`, router handlers) and async tests need `pytest-asyncio` mode.

## File Structure

**New files (production):**
- `app/services/oauth_crypto.py` — AES-256-GCM encrypt/decrypt + startup validation
- `app/models/oauth.py` — Pydantic schemas (request/response models, MongoDB document model)
- `app/services/oauth_service.py` — flow orchestration + `resolve()`
- `app/routers/oauth.py` — REST endpoints
- `scripts/smoke_test_oauth_pkce.py` — manual end-to-end smoke

**New files (tests):**
- `tests/unit/test_oauth_crypto.py`
- `tests/unit/test_oauth_models.py`
- `tests/unit/test_oauth_service_pkce.py`
- `tests/unit/test_oauth_service_device_code.py`
- `tests/unit/test_oauth_service_resolve.py`
- `tests/unit/test_oauth_router.py`
- `tests/unit/test_config_bridge_oauth_skip.py`
- `tests/unit/test_trading_graph_init_refactor.py`
- `tests/unit/test_memory_subscription_fallback.py`

**Modified files (production):**
- `pyproject.toml` — add `cryptography>=42.0` to deps
- `.env.example` — document `OAUTH_ENCRYPTION_KEY`
- `app/core/startup_validator.py` — call crypto-key validator
- `app/core/config_bridge.py` — skip OAuth providers in API-key bridging
- `app/services/analysis_service.py` — inject OAuth token before `TradingAgentsGraph`
- `app/main.py` — include `oauth` router
- `tradingagents/llm_adapters/claude_code_adapter.py` — accept `access_token=` kwarg
- `tradingagents/llm_adapters/codex_adapter.py` — accept `access_token=` kwarg
- `tradingagents/llm_adapters/subscription_credentials.py` — add `force_refresh` to `resolve()`
- `tradingagents/graph/trading_graph.py` — two changes: `create_llm_by_provider` routes `api_key` to OAuth adapters as `access_token`; remove DashScope/Qianfan/Zhipu/SiliconFlow/custom_openai elif branches; `__init__` collapses to two `create_llm_by_provider` calls
- `tradingagents/agents/utils/memory.py` — `UnsupportedEmbeddingError` for OAuth providers without independent embedding

**Modified files (tests):**
- `tests/conftest.py` — add `stub_optional_llm_deps` session fixture
- `tests/unit/test_create_llm_by_provider_subscription.py` — use the new fixture instead of module-level stubs
- `tests/unit/test_subscription_credentials.py` — add `force_refresh` test

**Out of scope:** `frontend/`, all UI work (PR-3), Keychain writer (deferred), key-rotation utility, multi-instance distributed locks.

---

## Task ordering

The plan has 23 tasks grouped by layer. Dependencies flow strictly downward — Task N can assume Tasks 1..N−1 are merged. Tasks within the same layer can be done in any order but for subagent-driven execution stick to numerical order.

```
Layer A — Encryption (foundation)         T1–T2
Layer B — Models + PR-1 cleanup            T3–T5
Layer C — Adapter modifications            T6–T8
Layer D — OAuth service                    T9–T13
Layer E — REST router                      T14–T17
Layer F — Graph refactor                   T18–T20
Layer G — Integration                      T21–T22
Layer H — Manual smoke                     T23
```

---

### Task 1: AES-256-GCM crypto module

**Files:**
- Create: `app/services/oauth_crypto.py`
- Test: `tests/unit/test_oauth_crypto.py`

- [ ] **Step 1: Verify `cryptography` is installed**

```bash
.venv/bin/python -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; print('OK')"
```

If FAIL, run pre-flight `pip install "cryptography>=42.0"` and add to pyproject.toml deps now.

- [ ] **Step 2: Write failing tests for crypto module**

Create `tests/unit/test_oauth_crypto.py`:

```python
"""Unit tests for app.services.oauth_crypto."""
import base64
import os
import secrets

import pytest

from app.services import oauth_crypto as oc


@pytest.fixture
def valid_key(monkeypatch):
    """A valid base64-encoded 32-byte key, exported via env var."""
    raw = secrets.token_bytes(32)
    b64 = base64.b64encode(raw).decode()
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", b64)
    return raw


class TestEncryptDecryptRoundTrip:
    def test_round_trip_preserves_dict(self, valid_key):
        payload = {"access_token": "at-x", "refresh_token": "rt-y"}
        ciphertext, nonce, tag = oc.encrypt_token_payload(payload)
        assert oc.decrypt_token_payload(ciphertext, nonce, tag) == payload

    def test_nonce_is_unique_per_call(self, valid_key):
        payload = {"access_token": "at"}
        _, n1, _ = oc.encrypt_token_payload(payload)
        _, n2, _ = oc.encrypt_token_payload(payload)
        assert n1 != n2

    def test_nonce_length_is_12_bytes(self, valid_key):
        _, nonce, _ = oc.encrypt_token_payload({"access_token": "at"})
        assert len(nonce) == 12

    def test_tag_length_is_16_bytes(self, valid_key):
        _, _, tag = oc.encrypt_token_payload({"access_token": "at"})
        assert len(tag) == 16

    def test_tampered_ciphertext_raises(self, valid_key):
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at"})
        tampered = bytes([ct[0] ^ 1]) + ct[1:]
        with pytest.raises(oc.OAuthCryptoError):
            oc.decrypt_token_payload(tampered, nonce, tag)

    def test_tampered_tag_raises(self, valid_key):
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at"})
        tampered_tag = bytes([tag[0] ^ 1]) + tag[1:]
        with pytest.raises(oc.OAuthCryptoError):
            oc.decrypt_token_payload(ct, nonce, tampered_tag)


class TestStartupValidation:
    def test_missing_key_when_collection_empty_warns_only(self, monkeypatch, caplog):
        monkeypatch.delenv("OAUTH_ENCRYPTION_KEY", raising=False)
        # Caller passes has_existing_credentials=False
        oc.validate_encryption_key_at_startup(has_existing_credentials=False)
        assert any("OAUTH_ENCRYPTION_KEY" in r.message for r in caplog.records)

    def test_missing_key_when_collection_has_records_raises(self, monkeypatch):
        monkeypatch.delenv("OAUTH_ENCRYPTION_KEY", raising=False)
        with pytest.raises(oc.OAuthCryptoError):
            oc.validate_encryption_key_at_startup(has_existing_credentials=True)

    def test_key_wrong_length_raises(self, monkeypatch):
        # 16-byte key (too short)
        monkeypatch.setenv(
            "OAUTH_ENCRYPTION_KEY",
            base64.b64encode(secrets.token_bytes(16)).decode(),
        )
        with pytest.raises(oc.OAuthCryptoError):
            oc.validate_encryption_key_at_startup(has_existing_credentials=False)

    def test_key_not_base64_raises(self, monkeypatch):
        monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", "not-base64!@#$")
        with pytest.raises(oc.OAuthCryptoError):
            oc.validate_encryption_key_at_startup(has_existing_credentials=False)

    def test_valid_key_passes(self, valid_key):
        # Should not raise
        oc.validate_encryption_key_at_startup(has_existing_credentials=True)
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_crypto.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.oauth_crypto'`.

- [ ] **Step 4: Implement the module**

Create `app/services/oauth_crypto.py`:

```python
"""AES-256-GCM encryption for OAuth tokens at rest.

Key is loaded from the OAUTH_ENCRYPTION_KEY environment variable as base64.
Validation is called from startup_validator at app boot.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_KEY_BYTES = 32
_NONCE_BYTES = 12


class OAuthCryptoError(RuntimeError):
    """Raised when encryption/decryption or key validation fails."""


def _load_key() -> bytes:
    """Read OAUTH_ENCRYPTION_KEY from env, validate, return raw 32-byte key."""
    encoded = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip()
    if not encoded:
        raise OAuthCryptoError(
            "OAUTH_ENCRYPTION_KEY environment variable is not set. "
            "Generate one with: "
            "python -c \"import secrets, base64; "
            "print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise OAuthCryptoError(
            f"OAUTH_ENCRYPTION_KEY is not valid base64: {exc}"
        ) from exc
    if len(raw) != _KEY_BYTES:
        raise OAuthCryptoError(
            f"OAUTH_ENCRYPTION_KEY must decode to exactly {_KEY_BYTES} bytes; "
            f"got {len(raw)}."
        )
    return raw


def encrypt_token_payload(payload: dict) -> Tuple[bytes, bytes, bytes]:
    """Encrypt a JSON-serializable dict; return (ciphertext, nonce, tag).

    The GCM authentication tag is split off the ciphertext (last 16 bytes) so
    callers can persist the fields separately in MongoDB.
    """
    key = _load_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    ciphertext, tag = ct_with_tag[:-16], ct_with_tag[-16:]
    return ciphertext, nonce, tag


def decrypt_token_payload(ciphertext: bytes, nonce: bytes, tag: bytes) -> dict:
    """Decrypt a (ciphertext, nonce, tag) triple back to the original dict."""
    key = _load_key()
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext + tag, associated_data=None)
    except InvalidTag as exc:
        raise OAuthCryptoError(
            "OAuth token decryption failed authentication "
            "(tampered ciphertext, wrong key, or corrupted nonce/tag)."
        ) from exc
    return json.loads(plaintext.decode("utf-8"))


def validate_encryption_key_at_startup(*, has_existing_credentials: bool) -> None:
    """Verify the env var. Called from startup_validator.

    - Missing key + no existing data: warn only (feature unused)
    - Missing key + existing data: fail fast (can't decrypt existing tokens)
    - Wrong format/length: always fail fast
    """
    try:
        _load_key()
    except OAuthCryptoError as exc:
        if "not set" in str(exc) and not has_existing_credentials:
            logger.warning(
                "OAUTH_ENCRYPTION_KEY not set; OAuth subscription auth is "
                "disabled. Set the env var to enable Claude Code / Codex "
                "subscription support."
            )
            return
        raise
```

- [ ] **Step 5: Run tests — verify all pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_crypto.py -v
```

Expected: 11 passed.

- [ ] **Step 6: Commit**

```bash
git add app/services/oauth_crypto.py tests/unit/test_oauth_crypto.py
git commit -m "feat(oauth): AES-256-GCM encrypt/decrypt for OAuth tokens at rest"
```

---

### Task 2: Wire OAUTH_ENCRYPTION_KEY into startup + .env.example

**Files:**
- Modify: `.env.example` (add OAUTH_ENCRYPTION_KEY block)
- Modify: `app/core/startup_validator.py` (call `validate_encryption_key_at_startup`)
- Add: `pyproject.toml` `cryptography>=42.0` dependency (if not already there from Task 1 pre-flight)
- Test: `tests/unit/test_oauth_crypto.py` (already has startup tests from Task 1)

- [ ] **Step 1: Add cryptography to pyproject.toml dependencies**

Open `pyproject.toml`. Locate the `dependencies = [` list (around line 11–88). Add the new line in the "认证和安全" section (after the `bcrypt>=4.0.0` line):

```toml
    "PyJWT>=2.0.0",
    "bcrypt>=4.0.0",
    "cryptography>=42.0",          # OAuth token encryption (PR-2)
```

- [ ] **Step 2: Append OAUTH_ENCRYPTION_KEY block to .env.example**

Locate the "JWT 安全配置" block in `.env.example` (it ends with `CSRF_SECRET=...`). Append after it:

```bash
# [REQUIRED for OAuth subscription auth] OAuth 凭据加密 key
# 用于加密 MongoDB user_oauth_credentials 集合中的 OAuth token (Claude Code / Codex).
# 生成命令: python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
# 若未设置且未启用订阅鉴权，可留空（启动只会警告）。一旦有用户绑定订阅，此值不可丢失，
# 否则所有已存的 token 都无法解密，用户需要重新走 OAuth 授权流。
OAUTH_ENCRYPTION_KEY=
```

- [ ] **Step 3: Read existing startup_validator.py**

```bash
cat app/core/startup_validator.py
```

Read it once to understand the existing structure — it's the function `validate_startup_config()` invoked from `app/main.py`'s lifespan.

- [ ] **Step 4: Add the crypto-key check to startup_validator**

Open `app/core/startup_validator.py`. Find `validate_startup_config()`. Add at the bottom of the function (before the closing `logger.info("✅ Configuration validated")` or equivalent):

```python
    # OAuth encryption key validation (PR-2)
    try:
        from app.services.oauth_crypto import validate_encryption_key_at_startup
        # We can't query MongoDB at startup_validator time (database may not be
        # initialized yet); default to has_existing_credentials=False so missing
        # key is a warning, not a fail. If a misconfigured server has tokens in
        # the DB, the first oauth call will fail loudly via OAuthCryptoError
        # anyway.
        validate_encryption_key_at_startup(has_existing_credentials=False)
    except Exception as exc:
        # Wrong key length / wrong base64 always fails
        raise RuntimeError(f"OAuth encryption key invalid: {exc}") from exc
```

If you find a different existing pattern in `startup_validator.py`, adapt to match.

- [ ] **Step 5: Run the existing crypto tests + a smoke import**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_crypto.py -v
.venv/bin/python -c "from app.core.startup_validator import validate_startup_config; print('OK')"
```

Expected: 11 crypto tests pass; startup_validator imports without error.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .env.example app/core/startup_validator.py
git commit -m "feat(oauth): wire OAUTH_ENCRYPTION_KEY into startup validation and .env.example"
```

---

### Task 3: OAuth Pydantic models

**Files:**
- Create: `app/models/oauth.py`
- Test: `tests/unit/test_oauth_models.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_oauth_models.py`:

```python
"""Unit tests for app.models.oauth."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import oauth as m


class TestOAuthCredentialDoc:
    def test_construct_minimal(self):
        doc = m.OAuthCredentialDoc(
            user_id="u1",
            provider="claude_code",
            ciphertext=b"\x01\x02",
            nonce=b"\x00" * 12,
            tag=b"\x00" * 16,
            access_token_expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            refresh_token_present=True,
            created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            last_refresh_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            last_used_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        )
        assert doc.provider == "claude_code"
        assert doc.refresh_token_present is True

    def test_provider_must_be_known(self):
        with pytest.raises(ValidationError):
            m.OAuthCredentialDoc(
                user_id="u1",
                provider="anthropic",  # wrong name (should be claude_code)
                ciphertext=b"x", nonce=b"\x00" * 12, tag=b"\x00" * 16,
                access_token_expires_at=datetime.now(timezone.utc),
                refresh_token_present=False,
                created_at=datetime.now(timezone.utc),
                last_refresh_at=datetime.now(timezone.utc),
                last_used_at=datetime.now(timezone.utc),
            )


class TestResponseModels:
    def test_authorize_anthropic_response(self):
        resp = m.AuthorizeClaudeCodeResponse(
            authorize_url="https://claude.ai/oauth/authorize?state=x",
            state="x",
        )
        assert resp.expires_in == 600

    def test_authorize_codex_response(self):
        resp = m.AuthorizeCodexResponse(
            user_code="ABCD-EFGH",
            verification_uri="https://chatgpt.com/device",
            expires_in=300,
            interval=5,
        )
        assert resp.user_code == "ABCD-EFGH"

    def test_poll_codex_response_status_enum(self):
        for s in ("pending", "bound", "expired", "denied"):
            r = m.PollCodexResponse(status=s)
            assert r.status == s
        with pytest.raises(ValidationError):
            m.PollCodexResponse(status="nonsense")

    def test_status_response_unbound(self):
        r = m.OAuthStatusResponse(bound=False, provider="claude_code")
        assert r.expires_at is None
        assert r.last_refresh_at is None

    def test_status_response_bound(self):
        r = m.OAuthStatusResponse(
            bound=True,
            provider="codex",
            expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            last_refresh_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
        )
        assert r.bound is True
        assert r.provider == "codex"
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.models.oauth'`.

- [ ] **Step 3: Implement the models**

Create `app/models/oauth.py`:

```python
"""Pydantic models for OAuth subscription auth (PR-2)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

OAuthProvider = Literal["claude_code", "codex"]


class OAuthCredentialDoc(BaseModel):
    """MongoDB document shape for user_oauth_credentials.

    Stored fields exactly mirror this model — Motor returns dicts; we
    construct this Pydantic model for type-safe service-layer code.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str
    provider: OAuthProvider
    ciphertext: bytes
    nonce: bytes
    tag: bytes
    access_token_expires_at: datetime
    refresh_token_present: bool
    created_at: datetime
    last_refresh_at: datetime
    last_used_at: datetime


class AuthorizeClaudeCodeResponse(BaseModel):
    """Response from POST/GET /api/oauth/authorize/claude_code (PKCE start)."""
    authorize_url: str       # not HttpUrl: query-string is long, Pydantic v2 strict
    state: str
    expires_in: int = 600


class AuthorizeCodexResponse(BaseModel):
    """Response from POST /api/oauth/authorize/codex (device code start)."""
    user_code: str
    verification_uri: HttpUrl
    expires_in: int
    interval: int


PollStatus = Literal["pending", "bound", "expired", "denied"]


class PollCodexResponse(BaseModel):
    """Response from POST /api/oauth/poll/codex."""
    status: PollStatus
    increment_interval: bool = False


class OAuthStatusResponse(BaseModel):
    """Response from GET /api/oauth/status/{provider}."""
    bound: bool
    provider: OAuthProvider
    expires_at: Optional[datetime] = None
    last_refresh_at: Optional[datetime] = None


class OAuthCredentialError(RuntimeError):
    """Raised by oauth_service.resolve when no usable credential is available."""
```

- [ ] **Step 4: Run — verify pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_models.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/models/oauth.py tests/unit/test_oauth_models.py
git commit -m "feat(oauth): Pydantic models for credential doc and API responses"
```

---

### Task 4: Add `force_refresh` to subscription_credentials.resolve()

This is one of the PR-1 follow-ups. Pure addition, no breaking change.

**Files:**
- Modify: `tradingagents/llm_adapters/subscription_credentials.py` (the `resolve` function)
- Test: `tests/unit/test_subscription_credentials.py` (append new test)

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_subscription_credentials.py` (inside `class TestResolve`):

```python
    def test_force_refresh_bypasses_expiry_check(self, monkeypatch, tmp_path):
        """force_refresh=True must call refresh_claude_code even on a fresh token."""
        fresh = sc.SubscriptionCredential(
            access_token="fresh-at",
            refresh_token="rt-1",
            expires_at_ms=int(time.time() * 1000) + 3600_000,  # 1h away — not expiring
            provider="claude_code",
            source="claude_code_file",
        )
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: fresh)

        called = {}
        def fake_refresh(rt, **_):
            called["rt"] = rt
            return ("forced-at", "rt-2", int(time.time() * 1000) + 3600_000)
        monkeypatch.setattr(sc, "refresh_claude_code", fake_refresh)
        monkeypatch.setattr(sc, "_claude_code_credentials_path", lambda: tmp_path / "creds.json")

        result = sc.resolve("claude_code", force_refresh=True)
        assert result.access_token == "forced-at"
        assert called["rt"] == "rt-1"

    def test_force_refresh_default_is_false(self, monkeypatch):
        """Default behavior unchanged: fresh token is returned without refresh."""
        fresh = sc.SubscriptionCredential(
            access_token="fresh-at",
            refresh_token="rt-1",
            expires_at_ms=int(time.time() * 1000) + 3600_000,
            provider="claude_code",
            source="claude_code_file",
        )
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: fresh)
        # If refresh is accidentally called, fail
        monkeypatch.setattr(sc, "refresh_claude_code",
                            lambda *_, **__: pytest.fail("should not refresh"))

        result = sc.resolve("claude_code")  # default force_refresh=False
        assert result.access_token == "fresh-at"
```

- [ ] **Step 2: Run — verify the force-refresh test fails**

```bash
.venv/bin/python -m pytest tests/unit/test_subscription_credentials.py::TestResolve::test_force_refresh_bypasses_expiry_check -v
```

Expected: `TypeError: resolve() got an unexpected keyword argument 'force_refresh'`.

- [ ] **Step 3: Add `force_refresh` parameter to `resolve()`**

Open `tradingagents/llm_adapters/subscription_credentials.py`. Find `def resolve(...)` (around line 350). Modify the signature and the two `is_expiring(cred)` checks:

```python
def resolve(
    provider: Literal["claude_code", "codex"],
    *,
    force_refresh: bool = False,
) -> SubscriptionCredential:
    """Locate and (if needed) refresh subscription credentials for `provider`.

    ... existing docstring ...

    If ``force_refresh=True``, refreshes the token even if it is not yet
    expiring. Useful when a caller knows the token has been revoked (e.g.
    401 from upstream).
    """
    if provider == "claude_code":
        cred = read_claude_code_from_keychain() or read_claude_code_from_file()
        if cred is None:
            raise SubscriptionCredentialError(
                "No Claude Code credentials found. Run `claude login` first, "
                "then retry. Looked in: macOS Keychain ('Claude Code-credentials') "
                f"and {_claude_code_credentials_path()}."
            )
        if not force_refresh and not is_expiring(cred):
            return cred
        # ... rest of the claude_code refresh path unchanged ...
```

Same change for the `codex` branch — replace `if not is_expiring(cred):` with `if not force_refresh and not is_expiring(cred):`.

- [ ] **Step 4: Run — verify both new tests pass**

```bash
.venv/bin/python -m pytest tests/unit/test_subscription_credentials.py -v
```

Expected: 65 passed (63 from PR-1 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/subscription_credentials.py tests/unit/test_subscription_credentials.py
git commit -m "feat(llm): subscription_credentials.resolve accepts force_refresh"
```

---

### Task 5: Move test stubs to conftest fixture (PR-1 follow-up)

**Files:**
- Modify: `tests/conftest.py` (add session-scoped fixture)
- Modify: `tests/unit/test_create_llm_by_provider_subscription.py` (use fixture instead of module-level stubs)

- [ ] **Step 1: Read current conftest.py**

```bash
cat tests/conftest.py
```

Confirm the existing `toml`/`tomli` shim is still there.

- [ ] **Step 2: Append the new fixture to conftest.py**

```python
# ----- begin PR-2 added fixture -----
import sys
import types
from unittest.mock import MagicMock
import pytest as _pytest


@_pytest.fixture(scope="session")
def stub_optional_llm_deps():
    """Stub heavy optional LLM deps for tests that import trading_graph.

    Tests that exercise the import chain through trading_graph.py (which
    transitively pulls in tradingagents.agents.utils.memory which imports
    `dashscope` and `chromadb` at module load time) but don't need real
    LLM behavior. Opt-in: request the fixture explicitly or wrap with an
    autouse fixture in the test file.
    """
    if "dashscope" not in sys.modules:
        m = types.ModuleType("dashscope")
        m.TextEmbedding = MagicMock()  # type: ignore[attr-defined]
        sys.modules["dashscope"] = m
    if "chromadb" not in sys.modules:
        sys.modules["chromadb"] = types.ModuleType("chromadb")
    if "chromadb.config" not in sys.modules:
        cc = types.ModuleType("chromadb.config")
        cc.Settings = MagicMock()  # type: ignore[attr-defined]
        sys.modules["chromadb.config"] = cc
    yield
    # Don't tear down — same process, leaving stubs in place for other tests
    # is safer than ripping them out (other tests may have cached imports
    # against them).
# ----- end PR-2 added fixture -----
```

- [ ] **Step 3: Read current test file**

```bash
cat tests/unit/test_create_llm_by_provider_subscription.py
```

- [ ] **Step 4: Replace module-level stubs with fixture request**

Replace the top of `tests/unit/test_create_llm_by_provider_subscription.py` (the module-level `sys.modules["dashscope"] = ...` block, roughly lines 1–28) with:

```python
"""Unit tests for the claude_code / codex branches in create_llm_by_provider."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _use_stubs(stub_optional_llm_deps):
    """Auto-apply the session-scoped LLM dep stubs from conftest."""
    pass


# Import deferred until after the fixture activates
def _import_target():
    from tradingagents.graph.trading_graph import create_llm_by_provider
    return create_llm_by_provider
```

Then every `create_llm_by_provider(...)` call in the file becomes `_import_target()(...)`. Or, simpler: import inside each test function body. Either pattern works.

- [ ] **Step 5: Run the tests — all 3 should still pass**

```bash
.venv/bin/python -m pytest tests/unit/test_create_llm_by_provider_subscription.py -v
```

Expected: 3 passed (unchanged from PR-1).

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/unit/test_create_llm_by_provider_subscription.py
git commit -m "test(oauth): move LLM dep stubs from module-level to session fixture"
```

---

### Task 6: ChatClaudeCodeOAuth accepts `access_token=` kwarg

**Files:**
- Modify: `tradingagents/llm_adapters/claude_code_adapter.py`
- Modify: `tests/unit/test_claude_code_adapter.py`

- [ ] **Step 1: Add failing test for the new kwarg**

Append to `tests/unit/test_claude_code_adapter.py` (inside `class TestChatClaudeCodeOAuth`):

```python
    def test_explicit_access_token_skips_local_resolve(self):
        """When access_token is passed in, do not call subscription_credentials.resolve."""
        fake_sync = MagicMock(name="anthropic.Anthropic")
        fake_async = MagicMock(name="anthropic.AsyncAnthropic")
        with patch.object(sc, "resolve",
                          side_effect=AssertionError("should not be called")), \
             patch("anthropic.Anthropic", return_value=fake_sync) as sync_ctor, \
             patch("anthropic.AsyncAnthropic", return_value=fake_async):
            ChatClaudeCodeOAuth(
                model="claude-opus-4-7",
                access_token="explicit-token-from-web",
            )
        # The explicit token must reach the underlying client constructor
        assert sync_ctor.call_args.kwargs["auth_token"] == "explicit-token-from-web"
```

- [ ] **Step 2: Run — verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_claude_code_adapter.py::TestChatClaudeCodeOAuth::test_explicit_access_token_skips_local_resolve -v
```

Expected fail: either `AssertionError("should not be called")` (since current ctor always calls `sc.resolve`) or `TypeError` (if `access_token` isn't accepted yet).

- [ ] **Step 3: Modify `__init__` to accept `access_token=`**

Open `tradingagents/llm_adapters/claude_code_adapter.py`. Modify `ChatClaudeCodeOAuth.__init__`:

```python
class ChatClaudeCodeOAuth(ChatAnthropic):
    """ChatAnthropic that authenticates via Claude Code OAuth instead of an API key.

    ... (existing class docstring) ...
    """

    def __init__(
        self,
        model: str,
        *,
        access_token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if access_token is None:
            # CLI / local-dev path: resolve from local credentials
            cred = sc.resolve("claude_code")
            access_token = cred.access_token
            source = cred.source
            expires_at_ms = cred.expires_at_ms
        else:
            # Web path: caller (oauth_service.resolve) provided the token
            source = "web_oauth"
            expires_at_ms = None

        super().__init__(model=model, anthropic_api_key="placeholder-oauth", **kwargs)

        default_headers = _oauth_default_headers()
        sync_client = anthropic.Anthropic(
            auth_token=access_token,
            default_headers=default_headers,
        )
        async_client = anthropic.AsyncAnthropic(
            auth_token=access_token,
            default_headers=default_headers,
        )
        object.__setattr__(self, "_client", sync_client)
        object.__setattr__(self, "_async_client", async_client)
        logger.info(
            "ChatClaudeCodeOAuth initialized: model=%s source=%s expires_at_ms=%s",
            model, source, expires_at_ms,
        )
```

Add `from typing import Optional` to the imports if not already there.

- [ ] **Step 4: Run all 5 tests — all pass**

```bash
.venv/bin/python -m pytest tests/unit/test_claude_code_adapter.py -v
```

Expected: 5 passed (4 from PR-1 + 1 new).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/claude_code_adapter.py tests/unit/test_claude_code_adapter.py
git commit -m "feat(llm): ChatClaudeCodeOAuth accepts explicit access_token (Web path)"
```

---

### Task 7: ChatCodexOAuth accepts `access_token=` kwarg

**Files:**
- Modify: `tradingagents/llm_adapters/codex_adapter.py`
- Modify: `tests/unit/test_codex_adapter.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_codex_adapter.py` (inside `class TestChatCodexOAuth`):

```python
    def test_explicit_access_token_skips_local_resolve(self):
        """When access_token is passed in, skip subscription_credentials.resolve."""
        with patch.object(sc, "resolve",
                          side_effect=AssertionError("should not be called")):
            chat = ChatCodexOAuth(
                model="gpt-5",
                access_token="web-cx-token",
            )
        api_key_field = getattr(chat, "openai_api_key", None) or getattr(chat, "api_key", None)
        raw = api_key_field.get_secret_value() if hasattr(api_key_field, "get_secret_value") else api_key_field
        assert raw == "web-cx-token"
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_codex_adapter.py::TestChatCodexOAuth::test_explicit_access_token_skips_local_resolve -v
```

Expected: `AssertionError("should not be called")`.

- [ ] **Step 3: Modify `__init__`**

Open `tradingagents/llm_adapters/codex_adapter.py`. Modify `ChatCodexOAuth.__init__`:

```python
class ChatCodexOAuth(ChatOpenAI):
    """... (existing docstring) ..."""

    def __init__(
        self,
        model: str,
        *,
        access_token: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if access_token is None:
            cred = sc.resolve("codex")
            access_token = cred.access_token
            source = cred.source
            expires_at_ms = cred.expires_at_ms
        else:
            source = "web_oauth"
            expires_at_ms = None

        kwargs.setdefault("base_url", CODEX_BASE_URL)
        super().__init__(
            model=model,
            api_key=access_token,
            **kwargs,
        )
        logger.info(
            "ChatCodexOAuth initialized: model=%s source=%s expires_at_ms=%s",
            model, source, expires_at_ms,
        )
```

Add `from typing import Optional` if not present.

- [ ] **Step 4: Run — all 6 codex tests pass**

```bash
.venv/bin/python -m pytest tests/unit/test_codex_adapter.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/codex_adapter.py tests/unit/test_codex_adapter.py
git commit -m "feat(llm): ChatCodexOAuth accepts explicit access_token (Web path)"
```

---

### Task 8: `create_llm_by_provider` routes api_key to OAuth adapters as access_token

**Files:**
- Modify: `tradingagents/graph/trading_graph.py` (the two new OAuth branches in `create_llm_by_provider`)
- Modify: `tests/unit/test_create_llm_by_provider_subscription.py` (new test)

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_create_llm_by_provider_subscription.py` (inside the existing test class):

```python
    def test_claude_code_api_key_routes_to_access_token(self):
        """api_key passed in (from oauth_service.resolve) becomes access_token."""
        from tradingagents.graph.trading_graph import create_llm_by_provider
        with patch(
            "tradingagents.llm_adapters.claude_code_adapter.ChatClaudeCodeOAuth",
        ) as ctor:
            create_llm_by_provider(
                provider="claude_code",
                model="claude-opus-4-7",
                backend_url="",
                temperature=0.4,
                max_tokens=4000,
                timeout=180,
                api_key="web-supplied-token",
            )
        assert ctor.call_args.kwargs.get("access_token") == "web-supplied-token"

    def test_codex_api_key_routes_to_access_token(self):
        from tradingagents.graph.trading_graph import create_llm_by_provider
        with patch(
            "tradingagents.llm_adapters.codex_adapter.ChatCodexOAuth",
        ) as ctor:
            create_llm_by_provider(
                provider="codex",
                model="gpt-5",
                backend_url="",
                temperature=0.5,
                max_tokens=2000,
                timeout=180,
                api_key="web-cx-token",
            )
        assert ctor.call_args.kwargs.get("access_token") == "web-cx-token"
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_create_llm_by_provider_subscription.py -v
```

Expected: 2 new tests fail (the current OAuth branches don't pass `access_token`).

- [ ] **Step 3: Modify the OAuth branches in `create_llm_by_provider`**

Open `tradingagents/graph/trading_graph.py`. Find the claude_code/codex branches in `create_llm_by_provider` (PR-1 13ba1d9 added them, around lines 60–82). Update both:

```python
    if provider.lower() == "claude_code":
        from tradingagents.llm_adapters.claude_code_adapter import ChatClaudeCodeOAuth
        return ChatClaudeCodeOAuth(
            model=model,
            access_token=api_key,  # None on CLI path → adapter falls back to local resolve
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    if provider.lower() == "codex":
        from tradingagents.llm_adapters.codex_adapter import ChatCodexOAuth
        return ChatCodexOAuth(
            model=model,
            access_token=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
```

- [ ] **Step 4: Run — all tests pass**

```bash
.venv/bin/python -m pytest tests/unit/test_create_llm_by_provider_subscription.py -v
```

Expected: 5 passed (3 from PR-1 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/graph/trading_graph.py tests/unit/test_create_llm_by_provider_subscription.py
git commit -m "feat(graph): create_llm_by_provider routes api_key to OAuth adapters"
```

---

### Task 9: OAuth service — credential CRUD (no flow yet)

**Files:**
- Create: `app/services/oauth_service.py` (initial skeleton: store / load / delete only)
- Test: `tests/unit/test_oauth_service_resolve.py`

- [ ] **Step 1: Verify Motor and pymongo are in the venv**

```bash
.venv/bin/python -c "from motor.motor_asyncio import AsyncIOMotorClient; print('OK')"
```

If fails, `pip install motor` (should already be installed from PR-1 pre-flight).

- [ ] **Step 2: Add `pytest-asyncio` mode to pytest.ini**

Open `tests/pytest.ini`. Confirm or add the line:

```ini
asyncio_mode = auto
```

This lets pytest discover `async def test_...` functions without per-test `@pytest.mark.asyncio` decorators.

- [ ] **Step 3: Write failing tests**

Create `tests/unit/test_oauth_service_resolve.py`:

```python
"""Unit tests for oauth_service.resolve / store / delete (no flow yet)."""
import base64
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import oauth_crypto as oc
from app.services import oauth_service as svc
from app.models.oauth import OAuthCredentialError


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    """Provide a valid encryption key for the whole module."""
    raw = secrets.token_bytes(32)
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", base64.b64encode(raw).decode())


@pytest.fixture
def fake_collection():
    """Async mock for the Motor collection."""
    coll = AsyncMock()
    return coll


class TestStoreCredentials:
    async def test_store_upserts_with_encryption(self, fake_collection):
        await svc.store_credentials(
            collection=fake_collection,
            user_id="u1",
            provider="claude_code",
            access_token="at-1",
            refresh_token="rt-1",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        fake_collection.update_one.assert_called_once()
        call_kwargs = fake_collection.update_one.call_args
        # Filter targets (user_id, provider)
        filter_doc = call_kwargs.args[0]
        assert filter_doc == {"user_id": "u1", "provider": "claude_code"}
        # Update doc has ciphertext, nonce, tag
        update_doc = call_kwargs.args[1]
        set_doc = update_doc["$set"]
        assert "ciphertext" in set_doc
        assert len(set_doc["nonce"]) == 12
        assert len(set_doc["tag"]) == 16
        assert set_doc["refresh_token_present"] is True
        # upsert=True kwarg
        assert call_kwargs.kwargs.get("upsert") is True


class TestResolve:
    async def test_resolve_no_doc_raises(self, fake_collection):
        fake_collection.find_one.return_value = None
        with pytest.raises(OAuthCredentialError) as exc:
            await svc.resolve(fake_collection, "u1", "claude_code")
        assert "not bound" in str(exc.value).lower() or "未绑定" in str(exc.value)

    async def test_resolve_fresh_token_returned_directly(self, fake_collection, monkeypatch):
        # Construct a doc encrypted with the same key
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at-good", "refresh_token": "rt-1"})
        fake_collection.find_one.return_value = {
            "user_id": "u1",
            "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "refresh_token_present": True,
            "last_used_at": datetime.now(timezone.utc),
            "last_refresh_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
        token = await svc.resolve(fake_collection, "u1", "claude_code")
        assert token == "at-good"
        # Should not call refresh_claude_code
        # Should update last_used_at
        fake_collection.update_one.assert_called_once()
        update_doc = fake_collection.update_one.call_args.args[1]
        assert "last_used_at" in update_doc.get("$set", {})

    async def test_resolve_expiring_token_refreshes(self, fake_collection, monkeypatch):
        # Token that expires in 30s — within 60s skew
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at-old", "refresh_token": "rt-1"})
        fake_collection.find_one.return_value = {
            "user_id": "u1",
            "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime.now(timezone.utc) + timedelta(seconds=30),
            "refresh_token_present": True,
            "last_used_at": datetime.now(timezone.utc),
            "last_refresh_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }

        def fake_refresh(rt, **_):
            return ("at-new", "rt-2", int(datetime.now(timezone.utc).timestamp() * 1000) + 3600_000)
        monkeypatch.setattr(
            "tradingagents.llm_adapters.subscription_credentials.refresh_claude_code",
            fake_refresh,
        )
        token = await svc.resolve(fake_collection, "u1", "claude_code")
        assert token == "at-new"

    async def test_resolve_expiring_no_refresh_token_raises(self, fake_collection):
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at-old", "refresh_token": ""})
        fake_collection.find_one.return_value = {
            "user_id": "u1",
            "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime.now(timezone.utc) - timedelta(minutes=5),
            "refresh_token_present": False,
            "last_used_at": datetime.now(timezone.utc),
            "last_refresh_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
        with pytest.raises(OAuthCredentialError) as exc:
            await svc.resolve(fake_collection, "u1", "claude_code")
        assert "expired" in str(exc.value).lower() or "expired" in str(exc.value)

    async def test_resolve_force_refresh(self, fake_collection, monkeypatch):
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at-old", "refresh_token": "rt-1"})
        fake_collection.find_one.return_value = {
            "user_id": "u1",
            "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),  # not expiring
            "refresh_token_present": True,
            "last_used_at": datetime.now(timezone.utc),
            "last_refresh_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
        monkeypatch.setattr(
            "tradingagents.llm_adapters.subscription_credentials.refresh_claude_code",
            lambda rt, **_: ("at-forced", "rt-2", int(datetime.now(timezone.utc).timestamp() * 1000) + 3600_000),
        )
        token = await svc.resolve(fake_collection, "u1", "claude_code", force_refresh=True)
        assert token == "at-forced"


class TestDelete:
    async def test_delete_calls_delete_one(self, fake_collection):
        await svc.delete_credentials(fake_collection, "u1", "claude_code")
        fake_collection.delete_one.assert_called_once_with(
            {"user_id": "u1", "provider": "claude_code"}
        )
```

- [ ] **Step 4: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_resolve.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.oauth_service'`.

- [ ] **Step 5: Implement the service skeleton**

Create `app/services/oauth_service.py`:

```python
"""OAuth subscription service: storage, refresh, retrieval.

PKCE and device-code flow logic live in this module too (added in later tasks);
this file currently has only the CRUD + resolve layer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from motor.motor_asyncio import AsyncIOMotorCollection

from app.models.oauth import OAuthCredentialError
from app.services import oauth_crypto as oc

logger = logging.getLogger(__name__)

# 60-second skew window: refresh when token expires within this margin.
_REFRESH_SKEW_SECONDS = 60


async def store_credentials(
    *,
    collection: AsyncIOMotorCollection,
    user_id: str,
    provider: Literal["claude_code", "codex"],
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
) -> None:
    """Encrypt and upsert OAuth credentials for (user_id, provider)."""
    payload = {"access_token": access_token, "refresh_token": refresh_token}
    ciphertext, nonce, tag = oc.encrypt_token_payload(payload)
    now = datetime.now(timezone.utc)
    await collection.update_one(
        {"user_id": user_id, "provider": provider},
        {
            "$set": {
                "ciphertext": ciphertext,
                "nonce": nonce,
                "tag": tag,
                "access_token_expires_at": expires_at,
                "refresh_token_present": bool(refresh_token),
                "last_refresh_at": now,
                "last_used_at": now,
            },
            "$setOnInsert": {
                "user_id": user_id,
                "provider": provider,
                "created_at": now,
            },
        },
        upsert=True,
    )


async def delete_credentials(
    collection: AsyncIOMotorCollection,
    user_id: str,
    provider: Literal["claude_code", "codex"],
) -> None:
    """Remove the binding entirely."""
    await collection.delete_one({"user_id": user_id, "provider": provider})


async def resolve(
    collection: AsyncIOMotorCollection,
    user_id: str,
    provider: Literal["claude_code", "codex"],
    *,
    force_refresh: bool = False,
) -> str:
    """Return a fresh access_token, refreshing transparently when needed.

    Raises:
        OAuthCredentialError: no binding, expired with no refresh token, or
                              refresh call failed.
    """
    doc = await collection.find_one({"user_id": user_id, "provider": provider})
    if doc is None:
        raise OAuthCredentialError(
            f"User {user_id} is not bound to {provider}. "
            f"Run the OAuth authorize flow at /api/oauth/authorize/{provider} first."
        )

    payload = oc.decrypt_token_payload(doc["ciphertext"], doc["nonce"], doc["tag"])
    access_token = payload["access_token"]
    refresh_token = payload.get("refresh_token") or ""

    expires_at = doc["access_token_expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    skew = timedelta(seconds=_REFRESH_SKEW_SECONDS)
    is_expiring = now >= (expires_at - skew)

    if not force_refresh and not is_expiring:
        await collection.update_one(
            {"user_id": user_id, "provider": provider},
            {"$set": {"last_used_at": now}},
        )
        return access_token

    if not refresh_token:
        raise OAuthCredentialError(
            f"Token for ({user_id}, {provider}) is expired and no refresh token "
            f"is available. Re-authorize at /api/oauth/authorize/{provider}."
        )

    # Refresh — call into PR-1's refresh primitives
    try:
        if provider == "claude_code":
            from tradingagents.llm_adapters.subscription_credentials import refresh_claude_code
            new_at, new_rt, new_exp_ms = refresh_claude_code(refresh_token)
        else:  # codex
            from tradingagents.llm_adapters.subscription_credentials import refresh_codex
            new_at, new_rt, new_exp_ms = refresh_codex(refresh_token)
    except Exception as exc:
        raise OAuthCredentialError(
            f"OAuth refresh failed for ({user_id}, {provider}): {exc}"
        ) from exc

    new_expires_at = datetime.fromtimestamp(new_exp_ms / 1000, tz=timezone.utc)
    await store_credentials(
        collection=collection,
        user_id=user_id,
        provider=provider,
        access_token=new_at,
        refresh_token=new_rt,
        expires_at=new_expires_at,
    )
    return new_at
```

- [ ] **Step 6: Run — all 7 tests pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_resolve.py -v
```

Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add app/services/oauth_service.py tests/unit/test_oauth_service_resolve.py tests/pytest.ini
git commit -m "feat(oauth): oauth_service.store/resolve/delete with encrypted MongoDB"
```

---

### Task 10: PKCE flow (Anthropic) — authorize step

**Files:**
- Modify: `app/services/oauth_service.py` (add `start_pkce_flow`)
- Test: `tests/unit/test_oauth_service_pkce.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_oauth_service_pkce.py`:

```python
"""Unit tests for oauth_service PKCE flow (Anthropic Claude Code)."""
import base64
import secrets
from unittest.mock import AsyncMock

import pytest

from app.services import oauth_service as svc


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    raw = secrets.token_bytes(32)
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", base64.b64encode(raw).decode())


@pytest.fixture
def fake_redis():
    r = AsyncMock()
    return r


class TestStartPkceFlow:
    async def test_returns_authorize_url_and_state(self, fake_redis):
        result = await svc.start_pkce_flow(
            redis_client=fake_redis,
            user_id="u1",
            redirect_uri="https://my.host/api/oauth/callback/claude_code",
        )
        assert "authorize_url" in result
        assert "state" in result
        # state is a base64url string at least 32 chars
        assert len(result["state"]) >= 32
        # authorize_url is the Anthropic OAuth endpoint with the right params
        assert "claude.ai/oauth/authorize" in result["authorize_url"]
        assert f"state={result['state']}" in result["authorize_url"]
        assert "code_challenge=" in result["authorize_url"]
        assert "code_challenge_method=S256" in result["authorize_url"]
        assert "client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e" in result["authorize_url"]
        # redirect_uri appears URL-encoded
        import urllib.parse
        assert urllib.parse.quote("https://my.host/api/oauth/callback/claude_code",
                                   safe="") in result["authorize_url"]

    async def test_stores_state_in_redis_with_ttl(self, fake_redis):
        await svc.start_pkce_flow(
            redis_client=fake_redis,
            user_id="u1",
            redirect_uri="https://my.host/api/oauth/callback/claude_code",
        )
        fake_redis.setex.assert_called_once()
        # Args: (key, ttl, value)
        key, ttl, value = fake_redis.setex.call_args.args
        assert key.startswith("oauth:state:claude_code:")
        assert ttl == 600
        # value should be JSON containing user_id and code_verifier
        import json
        parsed = json.loads(value)
        assert parsed["user_id"] == "u1"
        assert len(parsed["code_verifier"]) >= 43

    async def test_each_call_generates_distinct_state(self, fake_redis):
        r1 = await svc.start_pkce_flow(fake_redis, user_id="u", redirect_uri="https://h")
        r2 = await svc.start_pkce_flow(fake_redis, user_id="u", redirect_uri="https://h")
        assert r1["state"] != r2["state"]
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_pkce.py -v
```

Expected: `AttributeError: module ... has no attribute 'start_pkce_flow'`.

- [ ] **Step 3: Implement `start_pkce_flow`**

Append to `app/services/oauth_service.py`:

```python
import hashlib
import json
import secrets as _secrets
import urllib.parse


_ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_ANTHROPIC_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_ANTHROPIC_SCOPES = "org:create_api_key user:profile user:inference"
_PKCE_TTL_SECONDS = 600


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def start_pkce_flow(
    redis_client,
    *,
    user_id: str,
    redirect_uri: str,
) -> dict:
    """Begin the PKCE flow for Claude Code.

    Generates state + code_verifier + code_challenge, stores them in Redis,
    returns the authorize_url and state to redirect the browser to.
    """
    code_verifier = _b64url(_secrets.token_bytes(32))
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
    state = _b64url(_secrets.token_bytes(32))

    redis_key = f"oauth:state:claude_code:{state}"
    redis_value = json.dumps({
        "user_id": user_id,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    })
    await redis_client.setex(redis_key, _PKCE_TTL_SECONDS, redis_value)

    params = {
        "response_type": "code",
        "client_id": _ANTHROPIC_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": _ANTHROPIC_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{_ANTHROPIC_AUTHORIZE_URL}?" + urllib.parse.urlencode(params)
    return {"authorize_url": authorize_url, "state": state}
```

Also add `import base64` at the top if not already there (oauth_crypto already imports it, but oauth_service may not).

- [ ] **Step 4: Run — all 3 tests pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_pkce.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/oauth_service.py tests/unit/test_oauth_service_pkce.py
git commit -m "feat(oauth): PKCE flow start (Anthropic Claude Code authorize)"
```

---

### Task 11: PKCE flow — callback (token exchange)

**Files:**
- Modify: `app/services/oauth_service.py` (add `complete_pkce_flow`)
- Modify: `tests/unit/test_oauth_service_pkce.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_oauth_service_pkce.py`:

```python
class TestCompletePkceFlow:
    async def test_state_not_in_redis_raises(self, fake_redis):
        from app.models.oauth import OAuthCredentialError
        fake_redis.get.return_value = None
        with pytest.raises(OAuthCredentialError) as exc:
            await svc.complete_pkce_flow(
                redis_client=fake_redis,
                collection=AsyncMock(),
                state="bogus-state",
                code="any-code",
                redirect_uri="https://h",
                http_client=AsyncMock(),
            )
        assert "state" in str(exc.value).lower()

    async def test_successful_exchange_stores_credentials(self, fake_redis):
        import json
        from datetime import datetime, timezone
        fake_redis.get.return_value = json.dumps({
            "user_id": "u1",
            "code_verifier": "verifier-abc",
            "redirect_uri": "https://my.host/api/oauth/callback/claude_code",
        })
        fake_redis.delete = AsyncMock()
        fake_collection = AsyncMock()
        # Mock httpx response
        http_resp = AsyncMock()
        http_resp.status_code = 200
        http_resp.json = lambda: {
            "access_token": "at-anthropic",
            "refresh_token": "rt-anthropic",
            "expires_in": 3600,
        }
        fake_http = AsyncMock()
        fake_http.post.return_value = http_resp

        user_id = await svc.complete_pkce_flow(
            redis_client=fake_redis,
            collection=fake_collection,
            state="valid-state",
            code="auth-code-xyz",
            redirect_uri="https://my.host/api/oauth/callback/claude_code",
            http_client=fake_http,
        )

        assert user_id == "u1"
        # POST to platform.claude.com/v1/oauth/token
        fake_http.post.assert_called_once()
        url = fake_http.post.call_args.args[0]
        assert url == "https://platform.claude.com/v1/oauth/token"
        form = fake_http.post.call_args.kwargs.get("data") or fake_http.post.call_args.kwargs.get("json", {})
        # Body must include grant_type, code, redirect_uri, client_id, code_verifier
        body_str = str(form)
        assert "authorization_code" in body_str
        assert "auth-code-xyz" in body_str
        assert "verifier-abc" in body_str
        # Credentials stored
        fake_collection.update_one.assert_called_once()
        # State key deleted
        fake_redis.delete.assert_called_once_with("oauth:state:claude_code:valid-state")
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_pkce.py -v
```

Expected: new tests fail with `AttributeError: complete_pkce_flow`.

- [ ] **Step 3: Implement `complete_pkce_flow`**

Append to `app/services/oauth_service.py`:

```python
_ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"


async def complete_pkce_flow(
    *,
    redis_client,
    collection: AsyncIOMotorCollection,
    state: str,
    code: str,
    redirect_uri: str,
    http_client,
) -> str:
    """Complete the PKCE callback: validate state, exchange code, store creds.

    Returns the user_id whose binding was established (so the router can
    decide what to redirect to / which postMessage to emit).

    Raises:
        OAuthCredentialError: state missing/expired, token exchange failed.
    """
    redis_key = f"oauth:state:claude_code:{state}"
    raw = await redis_client.get(redis_key)
    if raw is None:
        raise OAuthCredentialError("OAuth state expired or invalid. Re-start authorize.")

    state_data = json.loads(raw if isinstance(raw, str) else raw.decode())
    user_id = state_data["user_id"]
    code_verifier = state_data["code_verifier"]
    # Sanity-check redirect_uri matches what we issued the authorize with
    if state_data.get("redirect_uri") != redirect_uri:
        raise OAuthCredentialError(
            "redirect_uri mismatch between authorize and callback"
        )

    resp = await http_client.post(
        _ANTHROPIC_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": _ANTHROPIC_CLIENT_ID,
            "code_verifier": code_verifier,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TradingAgents-CN/1.0 (oauth-pkce)",
        },
    )
    if resp.status_code != 200:
        raise OAuthCredentialError(
            f"Anthropic token exchange failed: HTTP {resp.status_code}"
        )
    payload = resp.json()
    access_token = payload.get("access_token") or ""
    refresh_token = payload.get("refresh_token") or ""
    expires_in = int(payload.get("expires_in") or 3600)
    if not access_token:
        raise OAuthCredentialError(
            f"Anthropic token exchange returned no access_token: {payload!r}"
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    await store_credentials(
        collection=collection,
        user_id=user_id,
        provider="claude_code",
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )
    await redis_client.delete(redis_key)
    return user_id
```

- [ ] **Step 4: Run — 5 PKCE tests pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_pkce.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/oauth_service.py tests/unit/test_oauth_service_pkce.py
git commit -m "feat(oauth): PKCE callback exchanges authorization code for tokens"
```

---

### Task 12: Codex device-code flow — start

**Files:**
- Modify: `app/services/oauth_service.py` (`start_device_code_flow`)
- Test: `tests/unit/test_oauth_service_device_code.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_oauth_service_device_code.py`:

```python
"""Unit tests for oauth_service Codex device-code flow."""
import base64
import secrets
from unittest.mock import AsyncMock

import pytest

from app.services import oauth_service as svc


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    raw = secrets.token_bytes(32)
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", base64.b64encode(raw).decode())


@pytest.fixture
def fake_redis():
    return AsyncMock()


@pytest.fixture
def fake_http_with_device_response():
    """httpx mock that returns a valid device-code start response."""
    resp = AsyncMock()
    resp.status_code = 200
    resp.json = lambda: {
        "device_code": "dev-code-xyz",
        "user_code": "ABCD-EFGH",
        "verification_uri_complete": "https://chatgpt.com/device?code=ABCD-EFGH",
        "expires_in": 600,
        "interval": 5,
    }
    http = AsyncMock()
    http.post.return_value = resp
    return http


class TestStartDeviceCodeFlow:
    async def test_returns_user_code_and_uri(self, fake_redis, fake_http_with_device_response):
        result = await svc.start_device_code_flow(
            redis_client=fake_redis,
            user_id="u1",
            http_client=fake_http_with_device_response,
        )
        assert result["user_code"] == "ABCD-EFGH"
        assert "chatgpt.com" in result["verification_uri"]
        assert result["interval"] == 5
        assert result["expires_in"] == 600

    async def test_stores_device_code_in_redis(self, fake_redis, fake_http_with_device_response):
        await svc.start_device_code_flow(
            redis_client=fake_redis,
            user_id="u1",
            http_client=fake_http_with_device_response,
        )
        fake_redis.setex.assert_called_once()
        key, ttl, value = fake_redis.setex.call_args.args
        assert key == "oauth:device:u1:codex"
        assert ttl == 600
        import json
        parsed = json.loads(value)
        assert parsed["device_code"] == "dev-code-xyz"
        assert parsed["interval"] == 5

    async def test_posts_to_correct_endpoint(self, fake_redis, fake_http_with_device_response):
        await svc.start_device_code_flow(
            redis_client=fake_redis,
            user_id="u1",
            http_client=fake_http_with_device_response,
        )
        url = fake_http_with_device_response.post.call_args.args[0]
        assert url == "https://auth.openai.com/oauth/device/code"
        body = fake_http_with_device_response.post.call_args.kwargs.get("data", {})
        assert body.get("client_id") == "app_EMoamEEZ73f0CkXaXp7hrann"
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_device_code.py -v
```

Expected: `AttributeError: ... start_device_code_flow`.

- [ ] **Step 3: Implement**

Append to `app/services/oauth_service.py`:

```python
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_DEVICE_CODE_URL = "https://auth.openai.com/oauth/device/code"
_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"


async def start_device_code_flow(
    *,
    redis_client,
    user_id: str,
    http_client,
) -> dict:
    """Begin Codex device-code flow. Returns user-facing code + URL."""
    resp = await http_client.post(
        _CODEX_DEVICE_CODE_URL,
        data={
            "client_id": _CODEX_CLIENT_ID,
            "scope": "openid profile email",
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TradingAgents-CN/1.0 (oauth-device)",
        },
    )
    if resp.status_code != 200:
        raise OAuthCredentialError(
            f"Codex device-code request failed: HTTP {resp.status_code}"
        )
    payload = resp.json()
    device_code = payload["device_code"]
    expires_in = int(payload.get("expires_in") or 600)
    interval = int(payload.get("interval") or 5)
    redis_key = f"oauth:device:{user_id}:codex"
    redis_value = json.dumps({
        "device_code": device_code,
        "interval": interval,
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
    })
    await redis_client.setex(redis_key, expires_in, redis_value)
    return {
        "user_code": payload["user_code"],
        "verification_uri": payload.get("verification_uri_complete") or payload.get("verification_uri"),
        "expires_in": expires_in,
        "interval": interval,
    }
```

- [ ] **Step 4: Run — all pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_device_code.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/oauth_service.py tests/unit/test_oauth_service_device_code.py
git commit -m "feat(oauth): Codex device-code flow start"
```

---

### Task 13: Codex device-code flow — poll

**Files:**
- Modify: `app/services/oauth_service.py` (`poll_device_code_flow`)
- Modify: `tests/unit/test_oauth_service_device_code.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_oauth_service_device_code.py`:

```python
class TestPollDeviceCodeFlow:
    async def test_no_device_code_in_redis_returns_expired(self, fake_redis):
        fake_redis.get.return_value = None
        fake_http = AsyncMock()
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=fake_http,
        )
        assert result["status"] == "expired"

    async def test_pending_returns_pending(self, fake_redis):
        import json
        fake_redis.get.return_value = json.dumps({
            "device_code": "dev-x",
            "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        # Mock upstream "authorization_pending"
        resp = AsyncMock()
        resp.status_code = 400
        resp.json = lambda: {"error": "authorization_pending"}
        http = AsyncMock()
        http.post.return_value = resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "pending"
        assert result["increment_interval"] is False

    async def test_slow_down_returns_increment(self, fake_redis):
        import json
        fake_redis.get.return_value = json.dumps({
            "device_code": "dev-x", "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        resp = AsyncMock()
        resp.status_code = 400
        resp.json = lambda: {"error": "slow_down"}
        http = AsyncMock()
        http.post.return_value = resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "pending"
        assert result["increment_interval"] is True

    async def test_expired_token_clears_redis(self, fake_redis):
        import json
        fake_redis.get.return_value = json.dumps({
            "device_code": "dev-x", "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        resp = AsyncMock()
        resp.status_code = 400
        resp.json = lambda: {"error": "expired_token"}
        http = AsyncMock()
        http.post.return_value = resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "expired"
        fake_redis.delete.assert_called_once()

    async def test_success_stores_credentials_and_returns_bound(self, fake_redis):
        import json
        fake_redis.get.return_value = json.dumps({
            "device_code": "dev-x", "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        resp = AsyncMock()
        resp.status_code = 200
        resp.json = lambda: {
            "access_token": "cx-at-final",
            "refresh_token": "cx-rt-final",
            "expires_in": 1800,
        }
        http = AsyncMock()
        http.post.return_value = resp
        fake_collection = AsyncMock()
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=fake_collection,
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "bound"
        fake_collection.update_one.assert_called_once()
        fake_redis.delete.assert_called_once()
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_device_code.py -v
```

Expected: 5 new tests fail with `AttributeError: poll_device_code_flow`.

- [ ] **Step 3: Implement `poll_device_code_flow`**

Append to `app/services/oauth_service.py`:

```python
async def poll_device_code_flow(
    *,
    redis_client,
    collection: AsyncIOMotorCollection,
    user_id: str,
    http_client,
) -> dict:
    """Poll Codex for completion of the device authorization.

    Returns a dict with `status` ∈ {pending, bound, expired, denied} and an
    optional `increment_interval` hint.
    """
    redis_key = f"oauth:device:{user_id}:codex"
    raw = await redis_client.get(redis_key)
    if raw is None:
        return {"status": "expired", "increment_interval": False}

    state = json.loads(raw if isinstance(raw, str) else raw.decode())
    device_code = state["device_code"]

    resp = await http_client.post(
        _CODEX_TOKEN_URL,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": _CODEX_CLIENT_ID,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TradingAgents-CN/1.0 (oauth-device-poll)",
        },
    )

    if resp.status_code == 200:
        payload = resp.json()
        access_token = payload.get("access_token") or ""
        if not access_token:
            return {"status": "denied", "increment_interval": False}
        refresh_token = payload.get("refresh_token") or ""
        expires_in = int(payload.get("expires_in") or 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        await store_credentials(
            collection=collection,
            user_id=user_id,
            provider="codex",
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        await redis_client.delete(redis_key)
        return {"status": "bound", "increment_interval": False}

    # Non-200 → check error code
    try:
        err_body = resp.json()
    except Exception:
        err_body = {}
    err = err_body.get("error", "")
    if err == "authorization_pending":
        return {"status": "pending", "increment_interval": False}
    if err == "slow_down":
        return {"status": "pending", "increment_interval": True}
    if err in ("expired_token", "access_denied"):
        await redis_client.delete(redis_key)
        return {"status": "expired" if err == "expired_token" else "denied",
                "increment_interval": False}
    # Unknown error — surface as denied so the UI stops polling
    logger.warning("Unknown Codex poll error: %s (status %s)", err, resp.status_code)
    return {"status": "denied", "increment_interval": False}
```

- [ ] **Step 4: Run — all pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_service_device_code.py -v
```

Expected: 8 passed (3 from Task 12 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add app/services/oauth_service.py tests/unit/test_oauth_service_device_code.py
git commit -m "feat(oauth): Codex device-code flow poll with state machine"
```

---

### Task 14: OAuth router — status + unbind endpoints (simple two first)

**Files:**
- Create: `app/routers/oauth.py`
- Test: `tests/unit/test_oauth_router.py`

- [ ] **Step 1: Write failing tests for the two simple endpoints**

Create `tests/unit/test_oauth_router.py`:

```python
"""Unit tests for app.routers.oauth — status + unbind first."""
import base64
import secrets
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import oauth as oauth_router


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    raw = secrets.token_bytes(32)
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", base64.b64encode(raw).decode())


@pytest.fixture
def app_client(monkeypatch):
    """Construct a FastAPI app with the oauth router and stubbed dependencies."""
    app = FastAPI()
    app.include_router(oauth_router.router, prefix="/api/oauth")

    # Stub auth dependency to always return a fixed test user
    def _fake_current_user():
        return {"_id": "test-user-1", "is_admin": False}
    app.dependency_overrides[oauth_router.get_current_user] = _fake_current_user

    return TestClient(app)


class TestStatusEndpoint:
    def test_unbound_provider_returns_bound_false(self, app_client, monkeypatch):
        fake_coll = AsyncMock()
        fake_coll.find_one.return_value = None
        monkeypatch.setattr(oauth_router, "get_credentials_collection",
                            lambda: fake_coll)
        r = app_client.get("/api/oauth/status/claude_code")
        assert r.status_code == 200
        body = r.json()
        assert body == {"bound": False, "provider": "claude_code",
                        "expires_at": None, "last_refresh_at": None}

    def test_bound_provider_returns_expiry(self, app_client, monkeypatch):
        from app.services import oauth_crypto as oc
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at", "refresh_token": "rt"})
        fake_coll = AsyncMock()
        fake_coll.find_one.return_value = {
            "user_id": "test-user-1", "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime(2026, 12, 31, tzinfo=timezone.utc),
            "refresh_token_present": True,
            "last_used_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "last_refresh_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
            "created_at": datetime(2026, 5, 14, tzinfo=timezone.utc),
        }
        monkeypatch.setattr(oauth_router, "get_credentials_collection",
                            lambda: fake_coll)
        r = app_client.get("/api/oauth/status/claude_code")
        assert r.status_code == 200
        body = r.json()
        assert body["bound"] is True
        assert body["expires_at"] is not None

    def test_unknown_provider_returns_422(self, app_client, monkeypatch):
        monkeypatch.setattr(oauth_router, "get_credentials_collection",
                            lambda: AsyncMock())
        r = app_client.get("/api/oauth/status/garbage")
        assert r.status_code == 422


class TestUnbindEndpoint:
    def test_unbind_calls_delete(self, app_client, monkeypatch):
        fake_coll = AsyncMock()
        monkeypatch.setattr(oauth_router, "get_credentials_collection",
                            lambda: fake_coll)
        r = app_client.delete("/api/oauth/unbind/codex")
        assert r.status_code == 204
        fake_coll.delete_one.assert_called_once_with(
            {"user_id": "test-user-1", "provider": "codex"}
        )
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_router.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.routers.oauth'`.

- [ ] **Step 3: Implement the router with the two endpoints**

Create `app/routers/oauth.py`:

```python
"""REST endpoints for OAuth subscription auth (PR-2)."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.models.oauth import (
    OAuthCredentialError,
    OAuthProvider,
    OAuthStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])


# --- dependency seams (overridable in tests) ---

def get_credentials_collection():
    """Return the Motor collection. Overridable in tests; wired up in app.main."""
    from app.core.database import get_database
    return get_database()["user_oauth_credentials"]


def get_redis_client():
    from app.core.redis_client import get_redis
    return get_redis()


def get_http_client():
    import httpx
    return httpx.AsyncClient(timeout=10.0)


def get_current_user():
    """Inject the JWT-authenticated user. Reuses existing auth dep if present."""
    from app.routers.auth_db import get_current_user as _real
    return _real()


# --- routes ---

@router.get("/status/{provider}", response_model=OAuthStatusResponse)
async def status_endpoint(
    provider: OAuthProvider,
    user=Depends(get_current_user),
):
    """Return the binding status of (user, provider)."""
    collection = get_credentials_collection()
    doc = await collection.find_one(
        {"user_id": user["_id"], "provider": provider}
    )
    if doc is None:
        return OAuthStatusResponse(bound=False, provider=provider)
    return OAuthStatusResponse(
        bound=True,
        provider=provider,
        expires_at=doc.get("access_token_expires_at"),
        last_refresh_at=doc.get("last_refresh_at"),
    )


@router.delete("/unbind/{provider}", status_code=204)
async def unbind_endpoint(
    provider: OAuthProvider,
    user=Depends(get_current_user),
):
    """Delete the binding for (user, provider)."""
    from app.services import oauth_service
    collection = get_credentials_collection()
    await oauth_service.delete_credentials(collection, user["_id"], provider)
```

- [ ] **Step 4: Run — verify status + unbind tests pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_router.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/routers/oauth.py tests/unit/test_oauth_router.py
git commit -m "feat(oauth): router with /status and /unbind endpoints"
```

---

### Task 15: OAuth router — PKCE authorize + callback endpoints

**Files:**
- Modify: `app/routers/oauth.py`
- Modify: `tests/unit/test_oauth_router.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_oauth_router.py`:

```python
class TestPkceAuthorizeEndpoint:
    def test_returns_authorize_url(self, app_client, monkeypatch):
        async def fake_start(redis_client, *, user_id, redirect_uri):
            assert user_id == "test-user-1"
            assert "localhost" in redirect_uri or "testserver" in redirect_uri
            return {"authorize_url": "https://claude.ai/oauth/authorize?state=abc",
                    "state": "abc"}
        monkeypatch.setattr("app.services.oauth_service.start_pkce_flow", fake_start)
        monkeypatch.setattr(oauth_router, "get_redis_client", lambda: AsyncMock())

        r = app_client.get("/api/oauth/authorize/claude_code")
        assert r.status_code == 200
        body = r.json()
        assert body["authorize_url"].startswith("https://claude.ai/")
        assert body["state"] == "abc"


class TestPkceCallbackEndpoint:
    def test_callback_completes_flow(self, app_client, monkeypatch):
        from app.services import oauth_service
        async def fake_complete(*, redis_client, collection, state, code,
                                redirect_uri, http_client):
            assert state == "valid-state"
            assert code == "auth-code"
            return "test-user-1"
        monkeypatch.setattr(oauth_service, "complete_pkce_flow", fake_complete)
        monkeypatch.setattr(oauth_router, "get_redis_client", lambda: AsyncMock())
        monkeypatch.setattr(oauth_router, "get_http_client", lambda: AsyncMock())

        r = app_client.get(
            "/api/oauth/callback/claude_code"
            "?state=valid-state&code=auth-code",
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # Response body must include the postMessage JS
        assert "postMessage" in r.text
        assert "oauth-success" in r.text

    def test_callback_with_error_returns_error_html(self, app_client, monkeypatch):
        # User denied authorization
        r = app_client.get(
            "/api/oauth/callback/claude_code?error=access_denied",
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert "access_denied" in r.text or "error" in r.text.lower()
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_router.py -v
```

Expected: 3 new tests fail (404).

- [ ] **Step 3: Add the two endpoints**

Append to `app/routers/oauth.py`:

```python
from app.models.oauth import AuthorizeClaudeCodeResponse


def _derive_redirect_uri(request: Request, provider: str) -> str:
    """Construct https://<host>/api/oauth/callback/<provider> from request headers."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    return f"{scheme}://{host}/api/oauth/callback/{provider}"


@router.get("/authorize/claude_code", response_model=AuthorizeClaudeCodeResponse)
async def authorize_claude_code(
    request: Request,
    user=Depends(get_current_user),
):
    """Start the Anthropic PKCE flow."""
    from app.services import oauth_service
    redirect_uri = _derive_redirect_uri(request, "claude_code")
    redis_client = get_redis_client()
    result = await oauth_service.start_pkce_flow(
        redis_client=redis_client,
        user_id=user["_id"],
        redirect_uri=redirect_uri,
    )
    return AuthorizeClaudeCodeResponse(**result)


_CALLBACK_HTML_SUCCESS = """<!doctype html><html><body>
<h2>Authorization complete</h2>
<p>You can close this window.</p>
<script>
  if (window.opener) {
    window.opener.postMessage({type: "oauth-success", provider: "claude_code"}, "*");
    setTimeout(() => window.close(), 1000);
  }
</script>
</body></html>"""

_CALLBACK_HTML_ERROR = """<!doctype html><html><body>
<h2>Authorization failed</h2>
<p>Error: %s</p>
<p>You can close this window.</p>
<script>
  if (window.opener) {
    window.opener.postMessage({type: "oauth-error", provider: "claude_code", error: "%s"}, "*");
  }
</script>
</body></html>"""


@router.get("/callback/claude_code", response_class=HTMLResponse)
async def callback_claude_code(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
):
    """PKCE callback. Note: no JWT requirement — auth flow's own state is the bearer."""
    if error or not state or not code:
        err = error or "missing_parameters"
        return HTMLResponse(_CALLBACK_HTML_ERROR % (err, err))
    from app.services import oauth_service
    redirect_uri = _derive_redirect_uri(request, "claude_code")
    try:
        await oauth_service.complete_pkce_flow(
            redis_client=get_redis_client(),
            collection=get_credentials_collection(),
            state=state,
            code=code,
            redirect_uri=redirect_uri,
            http_client=get_http_client(),
        )
    except OAuthCredentialError as exc:
        return HTMLResponse(_CALLBACK_HTML_ERROR % (str(exc), str(exc)))
    return HTMLResponse(_CALLBACK_HTML_SUCCESS)
```

- [ ] **Step 4: Run — all pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_router.py -v
```

Expected: 7 passed (4 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add app/routers/oauth.py tests/unit/test_oauth_router.py
git commit -m "feat(oauth): /authorize/claude_code and /callback/claude_code endpoints"
```

---

### Task 16: OAuth router — Codex authorize + poll endpoints

**Files:**
- Modify: `app/routers/oauth.py`
- Modify: `tests/unit/test_oauth_router.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_oauth_router.py`:

```python
class TestCodexAuthorize:
    def test_codex_authorize_returns_user_code(self, app_client, monkeypatch):
        async def fake_start(*, redis_client, user_id, http_client):
            return {"user_code": "ABCD-EFGH",
                    "verification_uri": "https://chatgpt.com/device",
                    "expires_in": 600, "interval": 5}
        monkeypatch.setattr("app.services.oauth_service.start_device_code_flow", fake_start)
        monkeypatch.setattr(oauth_router, "get_redis_client", lambda: AsyncMock())
        monkeypatch.setattr(oauth_router, "get_http_client", lambda: AsyncMock())

        r = app_client.post("/api/oauth/authorize/codex")
        assert r.status_code == 200
        assert r.json()["user_code"] == "ABCD-EFGH"


class TestCodexPoll:
    def test_poll_returns_pending(self, app_client, monkeypatch):
        async def fake_poll(*, redis_client, collection, user_id, http_client):
            return {"status": "pending", "increment_interval": False}
        monkeypatch.setattr("app.services.oauth_service.poll_device_code_flow", fake_poll)
        monkeypatch.setattr(oauth_router, "get_redis_client", lambda: AsyncMock())
        monkeypatch.setattr(oauth_router, "get_credentials_collection", lambda: AsyncMock())
        monkeypatch.setattr(oauth_router, "get_http_client", lambda: AsyncMock())

        r = app_client.post("/api/oauth/poll/codex")
        assert r.status_code == 200
        assert r.json() == {"status": "pending", "increment_interval": False}

    def test_poll_returns_bound(self, app_client, monkeypatch):
        async def fake_poll(*, redis_client, collection, user_id, http_client):
            return {"status": "bound", "increment_interval": False}
        monkeypatch.setattr("app.services.oauth_service.poll_device_code_flow", fake_poll)
        monkeypatch.setattr(oauth_router, "get_redis_client", lambda: AsyncMock())
        monkeypatch.setattr(oauth_router, "get_credentials_collection", lambda: AsyncMock())
        monkeypatch.setattr(oauth_router, "get_http_client", lambda: AsyncMock())

        r = app_client.post("/api/oauth/poll/codex")
        assert r.json()["status"] == "bound"
```

- [ ] **Step 2: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_router.py -v
```

Expected: 3 new tests fail (404).

- [ ] **Step 3: Add the endpoints**

Append to `app/routers/oauth.py`:

```python
from app.models.oauth import AuthorizeCodexResponse, PollCodexResponse


@router.post("/authorize/codex", response_model=AuthorizeCodexResponse)
async def authorize_codex(user=Depends(get_current_user)):
    """Start the Codex device-code flow."""
    from app.services import oauth_service
    result = await oauth_service.start_device_code_flow(
        redis_client=get_redis_client(),
        user_id=user["_id"],
        http_client=get_http_client(),
    )
    return AuthorizeCodexResponse(**result)


@router.post("/poll/codex", response_model=PollCodexResponse)
async def poll_codex(user=Depends(get_current_user)):
    """Poll Codex device-code flow for completion."""
    from app.services import oauth_service
    result = await oauth_service.poll_device_code_flow(
        redis_client=get_redis_client(),
        collection=get_credentials_collection(),
        user_id=user["_id"],
        http_client=get_http_client(),
    )
    return PollCodexResponse(**result)
```

- [ ] **Step 4: Run — all pass**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_router.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add app/routers/oauth.py tests/unit/test_oauth_router.py
git commit -m "feat(oauth): /authorize/codex and /poll/codex endpoints"
```

---

### Task 17: OAuth router — refresh endpoint + wire into app.main

**Files:**
- Modify: `app/routers/oauth.py` (add `/refresh/{provider}`)
- Modify: `app/main.py` (include router)
- Modify: `tests/unit/test_oauth_router.py`

- [ ] **Step 1: Add failing test for refresh**

Append to `tests/unit/test_oauth_router.py`:

```python
class TestRefreshEndpoint:
    def test_refresh_calls_resolve_with_force(self, app_client, monkeypatch):
        from app.services import oauth_service
        called = {}
        async def fake_resolve(coll, user_id, provider, *, force_refresh=False):
            called["force_refresh"] = force_refresh
            return "new-token-xyz"
        monkeypatch.setattr(oauth_service, "resolve", fake_resolve)
        monkeypatch.setattr(oauth_router, "get_credentials_collection", lambda: AsyncMock())

        r = app_client.post("/api/oauth/refresh/claude_code")
        assert r.status_code == 200
        assert called["force_refresh"] is True

    def test_refresh_propagates_credential_error(self, app_client, monkeypatch):
        from app.services import oauth_service
        from app.models.oauth import OAuthCredentialError
        async def fake_resolve(*args, **kwargs):
            raise OAuthCredentialError("not bound")
        monkeypatch.setattr(oauth_service, "resolve", fake_resolve)
        monkeypatch.setattr(oauth_router, "get_credentials_collection", lambda: AsyncMock())

        r = app_client.post("/api/oauth/refresh/claude_code")
        assert r.status_code == 400
        assert "not bound" in r.json().get("detail", "")
```

- [ ] **Step 2: Run — fails**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_router.py -v
```

Expected: 2 new fail (404).

- [ ] **Step 3: Add the refresh endpoint**

Append to `app/routers/oauth.py`:

```python
@router.post("/refresh/{provider}")
async def refresh_endpoint(
    provider: OAuthProvider,
    user=Depends(get_current_user),
):
    """Force-refresh the token. Returns the new expiry."""
    from app.services import oauth_service
    try:
        await oauth_service.resolve(
            get_credentials_collection(),
            user["_id"],
            provider,
            force_refresh=True,
        )
    except OAuthCredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "refreshed"}
```

- [ ] **Step 4: Wire into app.main**

Open `app/main.py`. After the existing `app.include_router(...)` block (search for the area around line 686–730), add:

```python
from app.routers import oauth as oauth_router
app.include_router(oauth_router.router, prefix="/api/oauth", tags=["oauth"])
```

- [ ] **Step 5: Run — all pass + smoke import**

```bash
.venv/bin/python -m pytest tests/unit/test_oauth_router.py -v
.venv/bin/python -c "from app.main import app; print('OK', len(app.routes))"
```

Expected: 12 passed; main imports OK.

- [ ] **Step 6: Commit**

```bash
git add app/routers/oauth.py app/main.py tests/unit/test_oauth_router.py
git commit -m "feat(oauth): /refresh endpoint and wire router into app.main"
```

---

### Task 18: TradingAgentsGraph.__init__ refactor (collapse elif chain)

**Risk: this is the highest-risk task in PR-2.** Reads 400+ lines of existing code and replaces with ~20 lines. Validate carefully.

**Files:**
- Modify: `tradingagents/graph/trading_graph.py:204-756` (the `__init__` body up to `self.toolkit = ...`)
- Test: `tests/unit/test_trading_graph_init_refactor.py`

- [ ] **Step 1: Write failing smoke tests for every retained provider**

Create `tests/unit/test_trading_graph_init_refactor.py`:

```python
"""Smoke tests for TradingAgentsGraph.__init__ after the elif-chain refactor.

Each retained provider must construct without errors.
"""
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _stubs(stub_optional_llm_deps):
    pass


def _make_config(provider: str, *, with_api_key: bool = True) -> dict:
    cfg = {
        "llm_provider": provider,
        "deep_think_llm": "test-deep",
        "quick_think_llm": "test-quick",
        "backend_url": "https://example.test",
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "memory_enabled": False,  # disable memory to avoid embedding side-effects
        "project_dir": "/tmp",
        "quick_model_config": {"max_tokens": 100, "temperature": 0.0, "timeout": 10},
        "deep_model_config": {"max_tokens": 100, "temperature": 0.0, "timeout": 10},
    }
    if with_api_key:
        cfg["quick_api_key"] = "test-key"
        cfg["deep_api_key"] = "test-key"
    return cfg


class TestRefactorPreservesRetainedProviders:
    def test_openai_provider(self, monkeypatch):
        # Disable memory so we don't construct ChromaDB
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("openai"))
            assert g.quick_thinking_llm is not None

    def test_anthropic_provider(self):
        with patch("langchain_anthropic.ChatAnthropic"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("anthropic"))
            assert g.quick_thinking_llm is not None

    def test_google_provider(self):
        with patch("tradingagents.llm_adapters.google_openai_adapter.ChatGoogleOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("google"))

    def test_deepseek_provider(self):
        with patch("tradingagents.llm_adapters.deepseek_adapter.ChatDeepSeek"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("deepseek"))

    def test_openrouter_provider(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("openrouter"))

    def test_ollama_provider(self):
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("ollama"))

    def test_claude_code_oauth(self):
        # Provide access_token via api_key so adapter doesn't call sc.resolve
        cfg = _make_config("claude_code")
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=cfg)

    def test_codex_oauth(self):
        cfg = _make_config("codex")
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        g = TradingAgentsGraph(config=cfg)


class TestRefactorRoutesUnknownToFallback:
    def test_dashscope_via_fallback(self, monkeypatch):
        """Now-removed dashscope native branch should land on generic OpenAI-compat."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
        cfg = _make_config("dashscope")
        cfg["backend_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=cfg)
            assert g.quick_thinking_llm is not None

    def test_qianfan_via_fallback(self, monkeypatch):
        monkeypatch.setenv("QIANFAN_API_KEY", "test")
        cfg = _make_config("qianfan")
        cfg["backend_url"] = "https://qianfan.baidubce.com/v2"
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=cfg)

    def test_zhipu_via_fallback(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "test")
        cfg = _make_config("zhipu")
        cfg["backend_url"] = "https://open.bigmodel.cn/api/paas/v4"
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=cfg)
```

- [ ] **Step 2: Run — verify which tests pass/fail with current code**

```bash
.venv/bin/python -m pytest tests/unit/test_trading_graph_init_refactor.py -v
```

Tests for retained providers (openai, anthropic, google, deepseek, openrouter, ollama, claude_code, codex) should pass against current code since the current elif chain has those branches. Tests for dashscope/qianfan/zhipu may fail because the current code has special handling that hits the generic fallback differently. **This is the baseline.** Capture the result to compare against after refactoring.

- [ ] **Step 3: Read current __init__ to understand the existing elif chain**

```bash
sed -n '200,760p' tradingagents/graph/trading_graph.py | head -300
```

The chain is roughly:
- Lines 263-290: mixed-mode (different quick/deep providers)
- Lines 292-300: claude_code/codex (PR-1 fix)
- Lines 314-758: per-provider elif (openai, siliconflow, openrouter, ollama, anthropic, google, dashscope+aliases, deepseek+aliases, custom_openai, qianfan, zhipu, final else)

- [ ] **Step 4: Replace the elif chain**

Open `tradingagents/graph/trading_graph.py`. **Carefully** replace lines 263 through the end of the elif/else block (just before `self.toolkit = Toolkit(config=self.config)`) with:

```python
        # --- Unified LLM construction (PR-2 refactor) ---
        # All providers (native, OAuth subscription, and OpenAI-compatible
        # fallback) flow through create_llm_by_provider. Per-role provider
        # / backend_url / api_key allow mixed-mode without a separate branch.
        quick_provider = self.config.get("quick_provider") or self.config["llm_provider"]
        deep_provider = self.config.get("deep_provider") or self.config["llm_provider"]
        quick_backend_url = (
            self.config.get("quick_backend_url")
            or self.config.get("backend_url", "")
        )
        deep_backend_url = (
            self.config.get("deep_backend_url")
            or self.config.get("backend_url", "")
        )

        self.quick_thinking_llm = create_llm_by_provider(
            provider=quick_provider,
            model=self.config["quick_think_llm"],
            backend_url=quick_backend_url,
            temperature=quick_temperature,
            max_tokens=quick_max_tokens,
            timeout=quick_timeout,
            api_key=self.config.get("quick_api_key"),
        )
        self.deep_thinking_llm = create_llm_by_provider(
            provider=deep_provider,
            model=self.config["deep_think_llm"],
            backend_url=deep_backend_url,
            temperature=deep_temperature,
            max_tokens=deep_max_tokens,
            timeout=deep_timeout,
            api_key=self.config.get("deep_api_key"),
        )
        logger.info(
            f"✅ LLM 实例创建完成: quick={quick_provider}, deep={deep_provider}"
        )
```

Keep `self.toolkit = Toolkit(config=self.config)` and everything after it (memory init, graph setup) **unchanged**.

- [ ] **Step 5: Run the smoke tests — all 11 retained providers + 3 fallback should pass**

```bash
.venv/bin/python -m pytest tests/unit/test_trading_graph_init_refactor.py -v
```

Expected: 11 passed. If any fail, **stop** and inspect — the refactor may have lost a per-provider behavior. Don't proceed until all pass.

- [ ] **Step 6: Run the full unit test suite — confirm no regressions**

```bash
.venv/bin/python -m pytest tests/unit/ -q --ignore=tests/unit/dataflows --ignore=tests/unit/test_stocks_kline_news_api.py 2>&1 | tail -5
```

Expected: ≥ 90 passed (63 from PR-1 + new tests).

- [ ] **Step 7: Commit**

```bash
git add tradingagents/graph/trading_graph.py tests/unit/test_trading_graph_init_refactor.py
git commit -m "refactor(graph): collapse TradingAgentsGraph.__init__ elif chain into create_llm_by_provider"
```

---

### Task 19: Remove dashscope/qianfan/zhipu/siliconflow/custom_openai branches from `create_llm_by_provider`

After Task 18 they're already unreachable from `__init__`, but they're still in `create_llm_by_provider`. Per spec § 8.1, route them to the generic OpenAI-compatible fallback.

**Files:**
- Modify: `tradingagents/graph/trading_graph.py` (the `create_llm_by_provider` function body, ~lines 60-200)
- Test: `tests/unit/test_create_llm_by_provider_subscription.py` (add tests for the now-fallback providers)

- [ ] **Step 1: Add failing tests confirming the new routing**

Append to `tests/unit/test_create_llm_by_provider_subscription.py`:

```python
class TestNativeBranchesReduced:
    """Verify dashscope/qianfan/zhipu/siliconflow no longer have native branches.

    Each should construct via the generic OpenAI-compatible fallback.
    """
    def test_dashscope_uses_generic_fallback(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
        from tradingagents.graph.trading_graph import create_llm_by_provider
        with patch("langchain_openai.ChatOpenAI") as ctor:
            create_llm_by_provider(
                "dashscope", "qwen-test",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                0.1, 100, 10,
            )
        ctor.assert_called_once()
        kwargs = ctor.call_args.kwargs
        assert kwargs["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_qianfan_uses_generic_fallback(self, monkeypatch):
        monkeypatch.setenv("QIANFAN_API_KEY", "test")
        from tradingagents.graph.trading_graph import create_llm_by_provider
        with patch("langchain_openai.ChatOpenAI") as ctor:
            create_llm_by_provider(
                "qianfan", "ernie-test",
                "https://qianfan.baidubce.com/v2",
                0.1, 100, 10,
            )
        kwargs = ctor.call_args.kwargs
        assert kwargs["base_url"] == "https://qianfan.baidubce.com/v2"

    def test_zhipu_uses_generic_fallback(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "test")
        from tradingagents.graph.trading_graph import create_llm_by_provider
        with patch("langchain_openai.ChatOpenAI") as ctor:
            create_llm_by_provider(
                "zhipu", "glm-test",
                "https://open.bigmodel.cn/api/paas/v4",
                0.1, 100, 10,
            )
        kwargs = ctor.call_args.kwargs
        assert kwargs["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
```

- [ ] **Step 2: Run — these likely PASS already if the current native branches happen to use ChatOpenAI internally. Confirm with output.**

```bash
.venv/bin/python -m pytest tests/unit/test_create_llm_by_provider_subscription.py::TestNativeBranchesReduced -v
```

Expected: depending on existing code, some may pass (because dashscope adapter uses ChatOpenAI under the hood) and some may fail (because they use specialized adapters). Note which.

- [ ] **Step 3: Locate the branches to remove**

In `tradingagents/graph/trading_graph.py::create_llm_by_provider`, the branches to **remove** (per spec § 8.1):
- `dashscope` (and `alibaba`, "阿里百炼" aliases) — currently calls `ChatDashScopeOpenAI`
- `zhipu` — currently uses `create_openai_compatible_llm(provider="zhipu", ...)`
- `qianfan` / `custom_openai` — same
- Any branch that's currently routing to `openai_compatible_base.create_openai_compatible_llm(...)`

Keep:
- `claude_code` / `codex` (OAuth)
- `google`
- `dashscope`/`anthropic` — wait, scrutinize. Look at the current `create_llm_by_provider` (NOT the `__init__` chain) — it's already much smaller. The branches present are: google, dashscope, deepseek, zhipu, openai/siliconflow/openrouter/ollama (grouped), anthropic, qianfan/custom_openai, else (custom fallback).

Replace the following with no-op (i.e. let them fall through to the generic `else`):
- `dashscope`
- `zhipu`
- `qianfan` / `custom_openai`

Keep:
- `google` (uses ChatGoogleOpenAI adapter for tool-call compatibility)
- `deepseek` (uses ChatDeepSeek with token tracking)
- `anthropic` (uses ChatAnthropic)
- `openai`/`siliconflow`/`openrouter`/`ollama` (use ChatOpenAI directly)
- `claude_code`/`codex` (OAuth)

Make these edits inside `tradingagents/graph/trading_graph.py::create_llm_by_provider`:

Remove the `elif provider.lower() == "dashscope":` block (currently around lines 79-92).

Remove the `elif provider.lower() == "zhipu":` block (currently lines 108-122).

Remove the `elif provider.lower() in ["qianfan", "custom_openai"]:` block (currently lines 152-160).

Leave the final `else:` block intact — it already handles unknown providers via the generic OpenAI-compatible fallback.

- [ ] **Step 4: Run the new tests + the smoke suite**

```bash
.venv/bin/python -m pytest tests/unit/test_create_llm_by_provider_subscription.py tests/unit/test_trading_graph_init_refactor.py -v
```

Expected: all pass. The retained native providers still go through their adapters; the removed ones land on the OpenAI-compatible fallback.

- [ ] **Step 5: Full suite regression check**

```bash
.venv/bin/python -m pytest tests/unit/ -q --ignore=tests/unit/dataflows --ignore=tests/unit/test_stocks_kline_news_api.py 2>&1 | tail -5
```

Expected: no regressions vs Task 18 baseline.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/graph/trading_graph.py tests/unit/test_create_llm_by_provider_subscription.py
git commit -m "refactor(graph): route dashscope/qianfan/zhipu through OpenAI-compatible fallback"
```

---

### Task 20: memory.py subscription-mode fallback

**Files:**
- Modify: `tradingagents/agents/utils/memory.py`
- Test: `tests/unit/test_memory_subscription_fallback.py`

- [ ] **Step 1: Read current memory.py to understand the embedding selection**

```bash
sed -n '50,160p' tradingagents/agents/utils/memory.py
```

Identify where it selects embedding based on `llm_provider`.

- [ ] **Step 2: Write failing test**

Create `tests/unit/test_memory_subscription_fallback.py`:

```python
"""Test memory.py's subscription-mode embedding fallback."""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stubs(stub_optional_llm_deps):
    pass


class TestSubscriptionModeEmbeddingFallback:
    def test_claude_code_without_embedding_provider_raises(self, monkeypatch):
        """OAuth provider + no EMBEDDING_PROVIDER → raise UnsupportedEmbeddingError."""
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        from tradingagents.agents.utils.memory import (
            FinancialSituationMemory, UnsupportedEmbeddingError,
        )
        config = {"llm_provider": "claude_code"}
        with pytest.raises(UnsupportedEmbeddingError):
            FinancialSituationMemory("test_mem", config)

    def test_codex_without_embedding_provider_raises(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        from tradingagents.agents.utils.memory import (
            FinancialSituationMemory, UnsupportedEmbeddingError,
        )
        with pytest.raises(UnsupportedEmbeddingError):
            FinancialSituationMemory("test_mem", {"llm_provider": "codex"})

    def test_claude_code_with_embedding_provider_uses_it(self, monkeypatch):
        """Setting EMBEDDING_PROVIDER=dashscope allows memory to construct."""
        monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
        # The construction may still fail for other reasons, but it must NOT
        # raise UnsupportedEmbeddingError.
        from tradingagents.agents.utils.memory import (
            FinancialSituationMemory, UnsupportedEmbeddingError,
        )
        try:
            FinancialSituationMemory("test_mem", {"llm_provider": "claude_code"})
        except UnsupportedEmbeddingError:
            pytest.fail("Should not raise UnsupportedEmbeddingError when EMBEDDING_PROVIDER set")
        except Exception:
            pass  # other init failures (e.g. ChromaDB) are not our concern
```

- [ ] **Step 3: Run — verify fails**

```bash
.venv/bin/python -m pytest tests/unit/test_memory_subscription_fallback.py -v
```

Expected: `ImportError: cannot import name 'UnsupportedEmbeddingError'`.

- [ ] **Step 4: Modify memory.py**

Open `tradingagents/agents/utils/memory.py`. Near the top, add the new exception:

```python
class UnsupportedEmbeddingError(RuntimeError):
    """Raised when LLM provider cannot supply embeddings and no fallback is configured."""
```

In `FinancialSituationMemory.__init__`, near the top (just after the `llm_provider` is read), add:

```python
        llm_provider = config.get("llm_provider", "").lower()
        if llm_provider in ("claude_code", "codex"):
            import os
            embedding_provider = os.environ.get("EMBEDDING_PROVIDER", "").strip()
            if not embedding_provider:
                raise UnsupportedEmbeddingError(
                    f"LLM provider '{llm_provider}' uses OAuth and cannot supply "
                    f"embeddings. Set EMBEDDING_PROVIDER=dashscope (or openai) "
                    f"with the corresponding API key, or set "
                    f"memory_enabled=False in config."
                )
            # Use EMBEDDING_PROVIDER as the effective embedding source
            llm_provider = embedding_provider
        # ... existing provider-based embedding selection logic continues using
        # the (possibly substituted) llm_provider value ...
```

The existing code in `__init__` should be modified to use the local `llm_provider` variable (which may have been substituted) rather than re-reading `config["llm_provider"]`. Inspect the actual existing code first; the integration point depends on its structure.

- [ ] **Step 5: Run the tests — pass**

```bash
.venv/bin/python -m pytest tests/unit/test_memory_subscription_fallback.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Update TradingAgentsGraph to catch UnsupportedEmbeddingError**

Open `tradingagents/graph/trading_graph.py`. Find the memory init block (the lines that construct `bull_memory`, `bear_memory`, etc.). Wrap them:

```python
        memory_enabled = self.config.get("memory_enabled", True)
        if memory_enabled:
            try:
                from tradingagents.agents.utils.memory import (
                    FinancialSituationMemory, UnsupportedEmbeddingError,
                )
                self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
                self.bear_memory = FinancialSituationMemory("bear_memory", self.config)
                self.trader_memory = FinancialSituationMemory("trader_memory", self.config)
                self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config)
                self.risk_manager_memory = FinancialSituationMemory("risk_manager_memory", self.config)
            except UnsupportedEmbeddingError as exc:
                logger.warning("Memory disabled (no embedding available): %s", exc)
                memory_enabled = False
        if not memory_enabled:
            self.bull_memory = None
            self.bear_memory = None
            self.trader_memory = None
            self.invest_judge_memory = None
            self.risk_manager_memory = None
```

Find and replace the equivalent existing block.

- [ ] **Step 7: Full regression**

```bash
.venv/bin/python -m pytest tests/unit/ -q --ignore=tests/unit/dataflows --ignore=tests/unit/test_stocks_kline_news_api.py 2>&1 | tail -3
```

Expected: no regressions.

- [ ] **Step 8: Commit**

```bash
git add tradingagents/agents/utils/memory.py tradingagents/graph/trading_graph.py tests/unit/test_memory_subscription_fallback.py
git commit -m "feat(memory): subscription-mode fallback — UnsupportedEmbeddingError + graph degrades cleanly"
```

---

### Task 21: config_bridge skips OAuth providers; analysis_service injects token

**Files:**
- Modify: `app/core/config_bridge.py`
- Modify: `app/services/analysis_service.py`
- Test: `tests/unit/test_config_bridge_oauth_skip.py`

- [ ] **Step 1: Inspect both files**

```bash
sed -n '1,60p' app/core/config_bridge.py
grep -n "TradingAgentsGraph\|llm_provider" app/services/analysis_service.py | head -20
```

- [ ] **Step 2: Write failing tests for the config_bridge skip behavior**

Create `tests/unit/test_config_bridge_oauth_skip.py`:

```python
"""Test that config_bridge does not bridge OAuth providers' (nonexistent) api_key."""
import os
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _stubs(stub_optional_llm_deps):
    pass


@pytest.mark.asyncio
async def test_oauth_providers_dont_set_api_key_env(monkeypatch):
    """When an LLM config has provider=claude_code/codex, bridge_config_to_env
    must not set CLAUDE_CODE_API_KEY or CODEX_API_KEY env vars (these don't
    exist; OAuth path uses access_token instead, injected per-request).
    """
    monkeypatch.delenv("CLAUDE_CODE_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)

    fake_config = type("SystemConfig", (), {
        "llm_configs": [
            type("LLMConfig", (), {
                "provider": "claude_code", "model_name": "claude-opus-4-7",
                "api_key": "", "api_base": "", "enabled": True,
            })(),
            type("LLMConfig", (), {
                "provider": "codex", "model_name": "gpt-5",
                "api_key": "", "api_base": "", "enabled": True,
            })(),
        ],
        "data_source_configs": [],
    })()

    fake_service = AsyncMock()
    fake_service.get_system_config.return_value = fake_config
    with patch("app.services.config_service.config_service", fake_service):
        from app.core.config_bridge import bridge_config_to_env
        await bridge_config_to_env()

    # Must not have set spurious env vars
    assert "CLAUDE_CODE_API_KEY" not in os.environ
    assert "CODEX_API_KEY" not in os.environ
```

- [ ] **Step 3: Run — verify (may currently pass or fail depending on existing logic)**

```bash
.venv/bin/python -m pytest tests/unit/test_config_bridge_oauth_skip.py -v
```

If passing already: no code change needed for skip. If failing: there's a generic loop that env-vars every provider. In that case add to `bridge_config_to_env`:

```python
        if llm.provider in ("claude_code", "codex"):
            # OAuth providers don't use api_key bridging — analysis_service
            # injects access_token per-request from oauth_service.resolve.
            logger.info("Skipping api_key bridge for OAuth provider %s", llm.provider)
            continue
```

- [ ] **Step 4: Modify analysis_service.py to inject token**

Open `app/services/analysis_service.py`. Find the place where `TradingAgentsGraph` is constructed (search for `TradingAgentsGraph(`). Above that call, add:

```python
        # OAuth subscription providers: inject token before constructing the graph.
        if config.get("llm_provider") in ("claude_code", "codex"):
            from app.services import oauth_service
            from app.routers.oauth import get_credentials_collection
            try:
                token = await oauth_service.resolve(
                    get_credentials_collection(),
                    user_id=current_user_id,  # adapt to the actual variable name
                    provider=config["llm_provider"],
                )
                config["quick_api_key"] = token
                config["deep_api_key"] = token
            except Exception as exc:
                logger.error("OAuth token resolution failed for user %s: %s",
                             current_user_id, exc)
                raise
```

If `current_user_id` isn't in scope at that point, plumb it from the request handler. **Read the existing function signature first** — the user_id is likely already passed in.

- [ ] **Step 5: Run the test**

```bash
.venv/bin/python -m pytest tests/unit/test_config_bridge_oauth_skip.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add app/core/config_bridge.py app/services/analysis_service.py tests/unit/test_config_bridge_oauth_skip.py
git commit -m "feat(oauth): skip OAuth providers in config_bridge; analysis_service injects token"
```

---

### Task 22: Database init — create user_oauth_credentials indexes

**Files:**
- Modify: `app/core/database.py` (add index creation on startup)
- Test: skip (this is a one-shot infra change; smoke verifies it works)

- [ ] **Step 1: Read app/core/database.py**

```bash
cat app/core/database.py | head -80
```

Find the `init_db()` function.

- [ ] **Step 2: Add index creation**

In `app/core/database.py::init_db()`, after other collections' indexes are created, add:

```python
    # OAuth subscription credentials (PR-2)
    await db.user_oauth_credentials.create_index(
        [("user_id", 1), ("provider", 1)],
        unique=True,
        name="uniq_user_provider",
    )
    await db.user_oauth_credentials.create_index(
        [("access_token_expires_at", 1)],
        name="expiry_scan",
    )
    logger.info("✅ user_oauth_credentials indexes ensured")
```

- [ ] **Step 3: Smoke**

```bash
.venv/bin/python -c "from app.core.database import init_db; print('OK')"
```

Expected: imports OK.

- [ ] **Step 4: Commit**

```bash
git add app/core/database.py
git commit -m "feat(oauth): create user_oauth_credentials indexes on init_db"
```

---

### Task 23: Manual smoke test (real Anthropic PKCE flow)

This validates the entire end-to-end OAuth flow against the real Anthropic infrastructure. Like PR-1 Task 11, this is manual and not in CI.

**Files:**
- Create: `scripts/smoke_test_oauth_pkce.py`

- [ ] **Step 1: Write the smoke instructions + helper**

Create `scripts/smoke_test_oauth_pkce.py`:

```python
"""Manual smoke test: end-to-end Anthropic PKCE flow against the real service.

Prerequisites:
  1. MongoDB and Redis running (e.g. via docker-compose).
  2. .venv installed and `OAUTH_ENCRYPTION_KEY` set in the environment.
  3. A test user account exists. Set TEST_USER_ID env var to that user's _id.

Run:
  PYTHONPATH=. .venv/bin/python scripts/smoke_test_oauth_pkce.py

The script:
  1. Calls oauth_service.start_pkce_flow with a localhost redirect_uri.
  2. Prints the authorize_url; user opens it in a browser, authorizes, and
     is redirected to localhost (which will 404 — that's fine, copy the
     ?code=... and ?state=... from the URL bar).
  3. User pastes code and state into the script's stdin prompts.
  4. Script calls complete_pkce_flow, which stores the encrypted token in
     MongoDB.
  5. Script then calls resolve() and prints the decrypted access_token's
     first 20 chars to confirm round-trip works.

Exit codes:
  0 = PASS (token round-trip successful)
  2 = FAIL: missing prerequisites
  3 = FAIL: PKCE flow error
"""
from __future__ import annotations

import asyncio
import os
import sys


async def main() -> int:
    user_id = os.environ.get("TEST_USER_ID")
    if not user_id:
        print("FAIL: set TEST_USER_ID env var", file=sys.stderr)
        return 2
    if not os.environ.get("OAUTH_ENCRYPTION_KEY"):
        print("FAIL: set OAUTH_ENCRYPTION_KEY env var", file=sys.stderr)
        return 2

    from app.core.database import init_db, get_database
    from app.core.redis_client import get_redis
    from app.services import oauth_service
    import httpx

    await init_db()
    db = get_database()
    collection = db["user_oauth_credentials"]
    redis_client = get_redis()
    http_client = httpx.AsyncClient(timeout=10.0)

    redirect_uri = "http://localhost:8000/api/oauth/callback/claude_code"
    print(f"Starting PKCE flow for user {user_id} with redirect_uri={redirect_uri}")
    start_result = await oauth_service.start_pkce_flow(
        redis_client=redis_client,
        user_id=user_id,
        redirect_uri=redirect_uri,
    )
    print()
    print("Open this URL in your browser:")
    print(start_result["authorize_url"])
    print()
    print("After authorizing, the page will try to redirect to localhost and fail.")
    print("Copy the `code` and `state` query parameters from the URL bar.")
    print()
    code = input("Paste code: ").strip()
    state = input("Paste state: ").strip()

    try:
        bound_user_id = await oauth_service.complete_pkce_flow(
            redis_client=redis_client,
            collection=collection,
            state=state,
            code=code,
            redirect_uri=redirect_uri,
            http_client=http_client,
        )
        print(f"PASS: bound user_id={bound_user_id}")
    except Exception as exc:
        print(f"FAIL: complete_pkce_flow raised: {exc}", file=sys.stderr)
        return 3

    try:
        token = await oauth_service.resolve(collection, user_id, "claude_code")
        print(f"PASS: resolve returned token starting with {token[:20]}...")
    except Exception as exc:
        print(f"FAIL: resolve raised: {exc}", file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Document and commit (do NOT actually run unless you have full prerequisites)**

```bash
git add scripts/smoke_test_oauth_pkce.py
git commit -m "test(oauth): manual smoke test script for Anthropic PKCE round-trip"
```

- [ ] **Step 3: Run if prerequisites available**

If MongoDB + Redis are running and you have a test user account:

```bash
export OAUTH_ENCRYPTION_KEY=$(python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())")
export TEST_USER_ID=<your-test-user-id>
PYTHONPATH=. .venv/bin/python scripts/smoke_test_oauth_pkce.py
```

Expected: PASS at both checkpoints. If FAIL with HTTP errors from Anthropic, the most likely cause is that the `authorize_url` host (`claude.ai/oauth/authorize`) or scope list needs verification against the current hermes-agent reference; update `_ANTHROPIC_AUTHORIZE_URL` / `_ANTHROPIC_SCOPES` in `app/services/oauth_service.py` and retry.

- [ ] **Step 4: Final regression check**

```bash
.venv/bin/python -m pytest tests/unit/ -q --ignore=tests/unit/dataflows --ignore=tests/unit/test_stocks_kline_news_api.py
```

Expected: all unit tests pass.

---

## Self-review checklist (do this before announcing "PR-2 done")

- [ ] All 23 tasks committed individually
- [ ] Full unit-test suite green
- [ ] `scripts/smoke_test_oauth_pkce.py` passes against the real Anthropic API (or documented failure with diagnosis)
- [ ] `OAUTH_ENCRYPTION_KEY` is in `.env.example` with generation instructions
- [ ] `cryptography` is in `pyproject.toml` deps
- [ ] No tokens appear in any log output during a test run (`pytest -v 2>&1 | grep -i "token" | grep -v test_` should return nothing surprising)
- [ ] `tradingagents/llm_adapters/__init__.py` still does NOT re-export the OAuth adapters (PR-1 lazy-load discipline preserved)
- [ ] Spec § 14 "待定问题" revisited; any decisions ratified during implementation documented in the design doc
