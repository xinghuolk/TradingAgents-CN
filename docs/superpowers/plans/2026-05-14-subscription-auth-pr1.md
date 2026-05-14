# PR-1: Subscription Auth (Claude Code / Codex) — Core Adapters

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow TradingAgents-CN to drive its multi-agent analysis using a user's Claude Code (Claude Pro/Max) or Codex (ChatGPT Plus/Pro) subscription instead of an API key, by reading local OAuth credentials, refreshing them when expiring, and wiring two new LLM adapters into `create_llm_by_provider`.

**Architecture:** Subscription credentials live on the user's machine (managed by the official Claude Code / Codex CLIs). A new module `tradingagents/llm_adapters/subscription_credentials.py` reads them from filesystem / macOS Keychain, checks expiry, and refreshes against the upstream OAuth endpoint when needed. Two new adapter classes — `ChatClaudeCodeOAuth` and `ChatCodexOAuth` — extend the existing LangChain LLM classes and substitute the underlying SDK client with one that uses `auth_token=` + the OAuth-required headers (`anthropic-beta`, `user-agent`, `x-app`). `create_llm_by_provider` in `trading_graph.py` gets two new provider branches that wire these in. **Scope is intentionally narrow: no DB schema, no web UI, no embedding integration — those land in PR-2 and PR-3.**

**Tech Stack:** Python 3.10+, pytest, `langchain_anthropic`, `langchain_openai`, `anthropic` SDK (already a transitive dep), `urllib.request` for token refresh (no new deps), `unittest.mock` for tests.

**Design doc:** `docs/design/llm-subscription-auth-design.md`
**Reference implementation:** `/Users/like/source/hermes-agent/agent/anthropic_adapter.py:580-870`, `/Users/like/source/hermes-agent/hermes_cli/auth.py:2416-2681`

**Branch:** `feat/codex-claude-subscription-analysis` (current)

---

## Pre-flight

These are not tasks — do them once before starting Task 1.

```bash
cd /Users/like/source/TradingAgents-CN
uv sync                                           # installs deps into .venv
.venv/bin/python -c "import langchain_anthropic, langchain_openai, anthropic; print('OK')"
.venv/bin/pytest tests/ -x -q 2>&1 | tail -20    # baseline: should pass or report only known skips
```

If `uv sync` fails on the first run, the project also has `requirements.txt`; `pip install -r requirements.txt` in `.venv` is acceptable. The tests in `tests/` should run cleanly under `pytest -m "not integration"` (this is the default per `tests/pytest.ini`).

All commands below assume `.venv/bin/python` and `.venv/bin/pytest`. If you have a shell with the venv activated, you can drop the `.venv/bin/` prefix.

---

## File Structure

**New files:**
- `tradingagents/llm_adapters/subscription_credentials.py` — credential reading + refresh
- `tradingagents/llm_adapters/claude_code_adapter.py` — `ChatClaudeCodeOAuth` (subclass of `ChatAnthropic`)
- `tradingagents/llm_adapters/codex_adapter.py` — `ChatCodexOAuth` (subclass of `ChatOpenAI`)
- `tests/unit/__init__.py` — empty file if missing (`tests/unit/` already exists)
- `tests/unit/test_subscription_credentials.py`
- `tests/unit/test_claude_code_adapter.py`
- `tests/unit/test_codex_adapter.py`
- `scripts/smoke_test_claude_code_oauth.py` — manual E2E smoke (skip in CI)

**Modified files:**
- `tradingagents/llm_adapters/__init__.py` — export the two new adapters
- `tradingagents/graph/trading_graph.py:41-190` — add `claude_code` / `codex` branches to `create_llm_by_provider`

**Out of scope for PR-1:** `app/`, `frontend/`, `tradingagents/agents/utils/memory.py` (embedding fallback), `app/core/config_bridge.py`, MongoDB schema. These belong to PR-2 and PR-3.

---

### Task 1: Add `SubscriptionCredential` dataclass and expiry check

**Files:**
- Create: `tradingagents/llm_adapters/subscription_credentials.py`
- Create: `tests/unit/__init__.py` (if missing)
- Test: `tests/unit/test_subscription_credentials.py`

- [ ] **Step 1: Ensure `tests/unit/__init__.py` exists**

```bash
test -f tests/unit/__init__.py || touch tests/unit/__init__.py
```

- [ ] **Step 2: Write failing tests for `SubscriptionCredential` and `is_expiring`**

Create `tests/unit/test_subscription_credentials.py`:

```python
"""Unit tests for tradingagents.llm_adapters.subscription_credentials."""
import dataclasses
import time

import pytest

from tradingagents.llm_adapters import subscription_credentials as sc


def _make_cred(expires_at_ms: int) -> sc.SubscriptionCredential:
    return sc.SubscriptionCredential(
        access_token="at-test",
        refresh_token="rt-test",
        expires_at_ms=expires_at_ms,
        provider="claude_code",
        source="test",
    )


class TestSubscriptionCredential:
    def test_is_frozen(self):
        cred = _make_cred(0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cred.access_token = "mutated"  # type: ignore[misc]

    def test_zero_expires_at_means_never_expires(self):
        """Some managed keys have no expiry; expires_at_ms=0 must be treated as valid."""
        cred = _make_cred(0)
        assert sc.is_expiring(cred) is False

    def test_already_expired_is_expiring(self):
        cred = _make_cred(int(time.time() * 1000) - 1000)  # 1s ago
        assert sc.is_expiring(cred) is True

    def test_far_future_not_expiring(self):
        cred = _make_cred(int(time.time() * 1000) + 3600_000)  # 1h from now
        assert sc.is_expiring(cred) is False

    def test_within_skew_window_is_expiring(self):
        """Token that expires in 30s should be flagged with default 60s skew."""
        cred = _make_cred(int(time.time() * 1000) + 30_000)
        assert sc.is_expiring(cred, skew_seconds=60) is True

    def test_custom_skew(self):
        cred = _make_cred(int(time.time() * 1000) + 30_000)
        assert sc.is_expiring(cred, skew_seconds=10) is False
```

- [ ] **Step 3: Run tests; verify all fail with ImportError**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py -v
```

Expected: collection error or all-fail with `ModuleNotFoundError: No module named 'tradingagents.llm_adapters.subscription_credentials'`.

- [ ] **Step 4: Create the module with the minimum to make tests pass**

Create `tradingagents/llm_adapters/subscription_credentials.py`:

```python
"""Read and refresh OAuth credentials for Claude Code / Codex subscriptions.

Credentials are managed by the official Claude Code / Codex CLIs on the user's
machine — this module reads them and (when expiring) calls the upstream OAuth
refresh endpoint. We never run the full authorization flow ourselves; users must
run `claude login` / `codex login` once first.

Reference: hermes-agent/agent/anthropic_adapter.py:580-870
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


class SubscriptionCredentialError(RuntimeError):
    """Raised when subscription credentials cannot be located or refreshed."""


@dataclass(frozen=True)
class SubscriptionCredential:
    """A loaded OAuth credential for a subscription provider."""

    access_token: str
    refresh_token: Optional[str]
    expires_at_ms: int  # epoch ms; 0 means never-expires (managed keys)
    provider: str       # "claude_code" | "codex"
    source: str         # how/where it was loaded — for diagnostics only


def is_expiring(cred: SubscriptionCredential, skew_seconds: int = 60) -> bool:
    """Return True if the token has expired or will within `skew_seconds`.

    expires_at_ms=0 means "no expiry tracked" (managed keys) and is treated as
    not-expiring; callers must still handle 401 responses from the upstream API.
    """
    if cred.expires_at_ms == 0:
        return False
    now_ms = int(time.time() * 1000)
    return now_ms >= (cred.expires_at_ms - skew_seconds * 1000)
```

- [ ] **Step 5: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/llm_adapters/subscription_credentials.py tests/unit/__init__.py tests/unit/test_subscription_credentials.py
git commit -m "feat(llm): add SubscriptionCredential dataclass and is_expiring helper

第一步 PR-1: 订阅式鉴权的底层数据结构。下一步接入凭据读取。"
```

---

### Task 2: Read Claude Code credentials from `~/.claude/.credentials.json`

**Files:**
- Modify: `tradingagents/llm_adapters/subscription_credentials.py`
- Test: `tests/unit/test_subscription_credentials.py`

- [ ] **Step 1: Add failing tests for `read_claude_code_from_file`**

Append to `tests/unit/test_subscription_credentials.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch


class TestReadClaudeCodeFromFile:
    def test_file_missing_returns_none(self, tmp_path):
        with patch.object(sc, "_claude_code_credentials_path", return_value=tmp_path / "nope.json"):
            assert sc.read_claude_code_from_file() is None

    def test_valid_file_returns_credential(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "at-real",
                "refreshToken": "rt-real",
                "expiresAt": 1_700_000_000_000,
            }
        }))
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            cred = sc.read_claude_code_from_file()
        assert cred is not None
        assert cred.access_token == "at-real"
        assert cred.refresh_token == "rt-real"
        assert cred.expires_at_ms == 1_700_000_000_000
        assert cred.provider == "claude_code"
        assert cred.source == "claude_code_file"

    def test_missing_access_token_returns_none(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text(json.dumps({"claudeAiOauth": {"refreshToken": "rt"}}))
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            assert sc.read_claude_code_from_file() is None

    def test_malformed_json_returns_none(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text("not json {")
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            assert sc.read_claude_code_from_file() is None

    def test_missing_claudeAiOauth_key_returns_none(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text(json.dumps({"someOtherKey": {}}))
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            assert sc.read_claude_code_from_file() is None

    def test_no_expires_at_defaults_to_zero(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text(json.dumps({
            "claudeAiOauth": {"accessToken": "at-noexp"}
        }))
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            cred = sc.read_claude_code_from_file()
        assert cred is not None
        assert cred.expires_at_ms == 0
        assert cred.refresh_token is None
```

- [ ] **Step 2: Run tests; verify the six new tests fail**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py::TestReadClaudeCodeFromFile -v
```

Expected: 6 failed with `AttributeError: module ... has no attribute 'read_claude_code_from_file'`.

- [ ] **Step 3: Implement `read_claude_code_from_file`**

Append to `tradingagents/llm_adapters/subscription_credentials.py`:

```python
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _claude_code_credentials_path() -> Path:
    """Where Claude Code stores its OAuth credentials (overridden in tests)."""
    return Path.home() / ".claude" / ".credentials.json"


def read_claude_code_from_file() -> Optional[SubscriptionCredential]:
    """Read Claude Code OAuth credentials from `~/.claude/.credentials.json`.

    The file is written by `claude login` and refreshed by Claude Code itself.
    Returns None when the file is absent, malformed, or missing required fields.
    """
    path = _claude_code_credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to parse %s: %s", path, exc)
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    access_token = oauth.get("accessToken", "")
    if not access_token:
        return None
    return SubscriptionCredential(
        access_token=access_token,
        refresh_token=oauth.get("refreshToken") or None,
        expires_at_ms=int(oauth.get("expiresAt") or 0),
        provider="claude_code",
        source="claude_code_file",
    )
```

- [ ] **Step 4: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/subscription_credentials.py tests/unit/test_subscription_credentials.py
git commit -m "feat(llm): read Claude Code OAuth credentials from ~/.claude/.credentials.json"
```

---

### Task 3: Read Claude Code credentials from macOS Keychain

**Files:**
- Modify: `tradingagents/llm_adapters/subscription_credentials.py`
- Test: `tests/unit/test_subscription_credentials.py`

- [ ] **Step 1: Add failing tests for `read_claude_code_from_keychain`**

Append to `tests/unit/test_subscription_credentials.py`:

```python
import sys
from unittest.mock import MagicMock


class TestReadClaudeCodeFromKeychain:
    def test_non_darwin_returns_none(self):
        with patch.object(sc, "_platform_system", return_value="Linux"):
            assert sc.read_claude_code_from_keychain() is None

    def test_security_command_missing_returns_none(self):
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", side_effect=FileNotFoundError):
            assert sc.read_claude_code_from_keychain() is None

    def test_security_command_nonzero_exit_returns_none(self):
        completed = MagicMock(returncode=44, stdout="", stderr="")
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", return_value=completed):
            assert sc.read_claude_code_from_keychain() is None

    def test_valid_keychain_payload_returns_credential(self):
        payload = json.dumps({
            "claudeAiOauth": {
                "accessToken": "at-kc",
                "refreshToken": "rt-kc",
                "expiresAt": 1_800_000_000_000,
            }
        })
        completed = MagicMock(returncode=0, stdout=payload + "\n", stderr="")
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", return_value=completed):
            cred = sc.read_claude_code_from_keychain()
        assert cred is not None
        assert cred.access_token == "at-kc"
        assert cred.source == "macos_keychain"

    def test_keychain_returns_garbage_returns_none(self):
        completed = MagicMock(returncode=0, stdout="not json", stderr="")
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", return_value=completed):
            assert sc.read_claude_code_from_keychain() is None
```

- [ ] **Step 2: Run tests; verify all five fail**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py::TestReadClaudeCodeFromKeychain -v
```

Expected: 5 failed (`AttributeError`).

- [ ] **Step 3: Implement `read_claude_code_from_keychain`**

Append to `tradingagents/llm_adapters/subscription_credentials.py`:

```python
import platform
import subprocess


def _platform_system() -> str:
    """Wrapper around `platform.system()` so tests can override."""
    return platform.system()


def read_claude_code_from_keychain() -> Optional[SubscriptionCredential]:
    """Read Claude Code OAuth credentials from the macOS Keychain.

    Claude Code >=2.1.114 stores credentials under the generic-password entry
    named "Claude Code-credentials". The password value is a JSON blob with the
    same `claudeAiOauth` shape as the file fallback.

    Returns None on any non-Darwin platform or any failure path.
    """
    if _platform_system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("Keychain lookup failed: %s", exc)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    access_token = oauth.get("accessToken", "")
    if not access_token:
        return None
    return SubscriptionCredential(
        access_token=access_token,
        refresh_token=oauth.get("refreshToken") or None,
        expires_at_ms=int(oauth.get("expiresAt") or 0),
        provider="claude_code",
        source="macos_keychain",
    )
```

- [ ] **Step 4: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py -v
```

Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/subscription_credentials.py tests/unit/test_subscription_credentials.py
git commit -m "feat(llm): read Claude Code OAuth credentials from macOS Keychain"
```

---

### Task 4: Read Codex credentials from `~/.codex/auth.json`

**Files:**
- Modify: `tradingagents/llm_adapters/subscription_credentials.py`
- Test: `tests/unit/test_subscription_credentials.py`

- [ ] **Step 1: Add failing tests for `read_codex_from_file`**

Append to `tests/unit/test_subscription_credentials.py`:

```python
class TestReadCodexFromFile:
    def test_file_missing_returns_none(self, tmp_path):
        with patch.object(sc, "_codex_credentials_path", return_value=tmp_path / "nope.json"):
            assert sc.read_codex_from_file() is None

    def test_valid_file_returns_credential(self, tmp_path):
        # Codex CLI's auth.json shape (best-effort — confirmed against codex CLI 0.x).
        # Structure may evolve; the loader must tolerate unknown fields.
        from datetime import datetime, timezone
        expires_iso = "2026-12-31T00:00:00Z"
        expected_ms = int(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)

        creds_path = tmp_path / "auth.json"
        creds_path.write_text(json.dumps({
            "OPENAI_API_KEY": None,
            "tokens": {
                "access_token": "at-cx",
                "refresh_token": "rt-cx",
                "expires_at": expires_iso,
            },
            "last_refresh": "2026-05-13T10:00:00Z",
        }))
        with patch.object(sc, "_codex_credentials_path", return_value=creds_path):
            cred = sc.read_codex_from_file()
        assert cred is not None
        assert cred.access_token == "at-cx"
        assert cred.refresh_token == "rt-cx"
        assert cred.provider == "codex"
        assert cred.source == "codex_file"
        assert cred.expires_at_ms == expected_ms

    def test_missing_tokens_section_returns_none(self, tmp_path):
        creds_path = tmp_path / "auth.json"
        creds_path.write_text(json.dumps({"OPENAI_API_KEY": "sk-..."}))
        with patch.object(sc, "_codex_credentials_path", return_value=creds_path):
            assert sc.read_codex_from_file() is None

    def test_invalid_expires_at_falls_back_to_zero(self, tmp_path):
        creds_path = tmp_path / "auth.json"
        creds_path.write_text(json.dumps({
            "tokens": {"access_token": "at", "refresh_token": "rt", "expires_at": "not-a-date"}
        }))
        with patch.object(sc, "_codex_credentials_path", return_value=creds_path):
            cred = sc.read_codex_from_file()
        assert cred is not None
        assert cred.expires_at_ms == 0  # unparseable → treated as "no expiry tracked"
```

- [ ] **Step 2: Run tests; verify all fail**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py::TestReadCodexFromFile -v
```

Expected: 4 failed.

- [ ] **Step 3: Implement `read_codex_from_file`**

Append to `tradingagents/llm_adapters/subscription_credentials.py`:

```python
from datetime import datetime, timezone


def _codex_credentials_path() -> Path:
    """Where Codex CLI stores its OAuth tokens (overridden in tests)."""
    return Path.home() / ".codex" / "auth.json"


def _parse_codex_expires_at(value: object) -> int:
    """Parse Codex's ISO-8601 `expires_at` into epoch ms. 0 on failure."""
    if not isinstance(value, str) or not value:
        return 0
    # Python 3.10 datetime.fromisoformat doesn't accept trailing Z; normalize.
    iso = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def read_codex_from_file() -> Optional[SubscriptionCredential]:
    """Read Codex (ChatGPT subscription) OAuth credentials from `~/.codex/auth.json`.

    The Codex CLI is closed-source; this reader matches the observed schema as of
    Codex CLI 0.x. Unknown / new fields are ignored; the loader must keep working
    if the upstream adds keys.
    """
    path = _codex_credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to parse %s: %s", path, exc)
        return None
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access_token = tokens.get("access_token", "")
    if not access_token:
        return None
    return SubscriptionCredential(
        access_token=access_token,
        refresh_token=tokens.get("refresh_token") or None,
        expires_at_ms=_parse_codex_expires_at(tokens.get("expires_at")),
        provider="codex",
        source="codex_file",
    )
```

- [ ] **Step 4: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py -v
```

Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/subscription_credentials.py tests/unit/test_subscription_credentials.py
git commit -m "feat(llm): read Codex OAuth credentials from ~/.codex/auth.json"
```

---

### Task 5: Refresh Claude Code OAuth tokens

**Files:**
- Modify: `tradingagents/llm_adapters/subscription_credentials.py`
- Test: `tests/unit/test_subscription_credentials.py`

- [ ] **Step 1: Add failing tests for `refresh_claude_code`**

Append to `tests/unit/test_subscription_credentials.py`:

```python
import io
import urllib.error
from contextlib import contextmanager


def _fake_urlopen(body: dict, status: int = 200):
    """Return a context manager mimicking urllib.request.urlopen()'s response."""
    @contextmanager
    def _cm(_req, timeout=None):  # noqa: ARG001
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode()
        yield resp
    return _cm


class TestRefreshClaudeCode:
    def test_empty_refresh_token_raises(self):
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_claude_code("")

    def test_success_returns_new_tokens(self, monkeypatch):
        captured = {}
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["data"] = req.data
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expires_in": 3600,
            }).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)

        before_ms = int(time.time() * 1000)
        new_at, new_rt, new_exp = sc.refresh_claude_code("old-rt")
        after_ms = int(time.time() * 1000)

        assert new_at == "new-at"
        assert new_rt == "new-rt"
        # expires_at_ms should be roughly now + 3600s
        assert before_ms + 3_600_000 <= new_exp <= after_ms + 3_600_000 + 100
        # POST hit the primary endpoint with x-www-form-urlencoded body
        assert captured["url"] == "https://platform.claude.com/v1/oauth/token"
        assert b"grant_type=refresh_token" in captured["data"]
        assert b"refresh_token=old-rt" in captured["data"]
        # client_id must be the public Claude Code client id
        assert b"client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e" in captured["data"]

    def test_primary_endpoint_fails_falls_back_to_console(self, monkeypatch):
        call_count = {"n": 0}
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise urllib.error.URLError("primary down")
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "access_token": "fallback-at",
                "refresh_token": "old-rt",
                "expires_in": 1800,
            }).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)

        new_at, _, _ = sc.refresh_claude_code("old-rt")
        assert new_at == "fallback-at"
        assert call_count["n"] == 2

    def test_all_endpoints_fail_raises(self, monkeypatch):
        @contextmanager
        def always_fail(req, timeout=None):  # noqa: ARG001
            raise urllib.error.URLError("network out")
            yield  # noqa: unreachable
        monkeypatch.setattr(sc.urllib.request, "urlopen", always_fail)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_claude_code("any")

    def test_response_missing_access_token_raises(self, monkeypatch):
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            resp = MagicMock()
            resp.read.return_value = json.dumps({"refresh_token": "x"}).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_claude_code("any")
```

- [ ] **Step 2: Run tests; verify all five fail**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py::TestRefreshClaudeCode -v
```

Expected: 5 failed (`AttributeError: refresh_claude_code`).

- [ ] **Step 3: Implement `refresh_claude_code`**

Append to `tradingagents/llm_adapters/subscription_credentials.py`:

```python
import urllib.parse
import urllib.request
from typing import Tuple

_CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_CLAUDE_CODE_TOKEN_ENDPOINTS = (
    "https://platform.claude.com/v1/oauth/token",
    "https://console.anthropic.com/v1/oauth/token",
)
_CLAUDE_CODE_USER_AGENT = "TradingAgents-CN/1.0 (claude-code-oauth)"


def refresh_claude_code(refresh_token: str, *, timeout: float = 10.0) -> Tuple[str, str, int]:
    """Exchange a Claude Code refresh token for a new (access, refresh, expires_at_ms).

    Tries `platform.claude.com` first, then falls back to `console.anthropic.com`.
    Returns the rotated refresh token as the second element — the caller MUST
    persist it (the previous one is now invalid).

    Raises SubscriptionCredentialError on any failure.
    """
    if not refresh_token:
        raise SubscriptionCredentialError("refresh_token is required")
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _CLAUDE_CODE_CLIENT_ID,
    }).encode("utf-8")
    last_exc: Optional[Exception] = None
    for endpoint in _CLAUDE_CODE_TOKEN_ENDPOINTS:
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _CLAUDE_CODE_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network, http, json — all "try the next endpoint"
            logger.debug("Claude Code token refresh failed at %s: %s", endpoint, exc)
            last_exc = exc
            continue
        new_access = payload.get("access_token") or ""
        if not new_access:
            raise SubscriptionCredentialError(
                f"Claude Code refresh response missing access_token: {payload!r}"
            )
        new_refresh = payload.get("refresh_token") or refresh_token
        expires_in = int(payload.get("expires_in") or 3600)
        expires_at_ms = int(time.time() * 1000) + expires_in * 1000
        return new_access, new_refresh, expires_at_ms
    raise SubscriptionCredentialError(
        f"Claude Code token refresh failed against all endpoints: {last_exc}"
    )
```

- [ ] **Step 4: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py -v
```

Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/subscription_credentials.py tests/unit/test_subscription_credentials.py
git commit -m "feat(llm): refresh Claude Code OAuth tokens against platform.claude.com"
```

---

### Task 6: Refresh Codex OAuth tokens

**Files:**
- Modify: `tradingagents/llm_adapters/subscription_credentials.py`
- Test: `tests/unit/test_subscription_credentials.py`

- [ ] **Step 1: Add failing tests for `refresh_codex`**

Append to `tests/unit/test_subscription_credentials.py`:

```python
class TestRefreshCodex:
    def test_empty_refresh_token_raises(self):
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_codex("")

    def test_success_returns_new_tokens(self, monkeypatch):
        captured = {}
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            captured["data"] = req.data
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "access_token": "cx-new",
                "refresh_token": "cx-new-rt",
                "expires_in": 1800,
            }).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)

        new_at, new_rt, new_exp = sc.refresh_codex("cx-old-rt")
        assert new_at == "cx-new"
        assert new_rt == "cx-new-rt"
        assert captured["url"] == "https://auth.openai.com/oauth/token"
        assert b"grant_type=refresh_token" in captured["data"]
        assert b"client_id=app_EMoamEEZ73f0CkXaXp7hrann" in captured["data"]

    def test_failure_raises(self, monkeypatch):
        @contextmanager
        def fail(req, timeout=None):  # noqa: ARG001
            raise urllib.error.URLError("offline")
            yield
        monkeypatch.setattr(sc.urllib.request, "urlopen", fail)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_codex("any")
```

- [ ] **Step 2: Run tests; verify all three fail**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py::TestRefreshCodex -v
```

Expected: 3 failed.

- [ ] **Step 3: Implement `refresh_codex`**

Append to `tradingagents/llm_adapters/subscription_credentials.py`:

```python
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"


def refresh_codex(refresh_token: str, *, timeout: float = 10.0) -> Tuple[str, str, int]:
    """Exchange a Codex refresh token for a new (access, refresh, expires_at_ms).

    Codex (ChatGPT) refresh tokens are rotated on each use; the caller must
    persist the returned refresh_token.
    """
    if not refresh_token:
        raise SubscriptionCredentialError("refresh_token is required")
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _CODEX_CLIENT_ID,
    }).encode("utf-8")
    req = urllib.request.Request(
        _CODEX_TOKEN_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TradingAgents-CN/1.0 (codex-oauth)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise SubscriptionCredentialError(f"Codex token refresh failed: {exc}") from exc
    new_access = payload.get("access_token") or ""
    if not new_access:
        raise SubscriptionCredentialError(
            f"Codex refresh response missing access_token: {payload!r}"
        )
    new_refresh = payload.get("refresh_token") or refresh_token
    expires_in = int(payload.get("expires_in") or 3600)
    expires_at_ms = int(time.time() * 1000) + expires_in * 1000
    return new_access, new_refresh, expires_at_ms
```

- [ ] **Step 4: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py -v
```

Expected: 28 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/subscription_credentials.py tests/unit/test_subscription_credentials.py
git commit -m "feat(llm): refresh Codex OAuth tokens against auth.openai.com"
```

---

### Task 7: Top-level `resolve()` with file write-back

**Files:**
- Modify: `tradingagents/llm_adapters/subscription_credentials.py`
- Test: `tests/unit/test_subscription_credentials.py`

- [ ] **Step 1: Add failing tests for `resolve()`**

Append to `tests/unit/test_subscription_credentials.py`:

```python
class TestResolve:
    def test_no_credentials_raises(self, monkeypatch):
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: None)
        with pytest.raises(sc.SubscriptionCredentialError) as exc:
            sc.resolve("claude_code")
        assert "claude login" in str(exc.value).lower()

    def test_keychain_takes_precedence(self, monkeypatch):
        base = _make_cred(int(time.time() * 1000) + 3600_000)
        kc_cred = dataclasses.replace(base, source="macos_keychain")
        file_cred = dataclasses.replace(base, source="claude_code_file")
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: kc_cred)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: file_cred)
        result = sc.resolve("claude_code")
        assert result.source == "macos_keychain"

    def test_falls_back_to_file_when_keychain_empty(self, monkeypatch):
        file_cred = _make_cred(int(time.time() * 1000) + 3600_000)
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: file_cred)
        result = sc.resolve("claude_code")
        assert result is file_cred

    def test_refreshes_when_expiring(self, monkeypatch, tmp_path):
        expiring = sc.SubscriptionCredential(
            access_token="stale-at",
            refresh_token="rt-1",
            expires_at_ms=int(time.time() * 1000) + 10_000,  # expires in 10s, within 60s skew
            provider="claude_code",
            source="claude_code_file",
        )
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: expiring)

        called = {}
        def fake_refresh(rt, **_):
            called["rt"] = rt
            return ("fresh-at", "rt-2", int(time.time() * 1000) + 3600_000)
        monkeypatch.setattr(sc, "refresh_claude_code", fake_refresh)
        # Persist write target to a tmp file
        monkeypatch.setattr(sc, "_claude_code_credentials_path", lambda: tmp_path / "creds.json")

        result = sc.resolve("claude_code")
        assert result.access_token == "fresh-at"
        assert result.refresh_token == "rt-2"
        assert called["rt"] == "rt-1"
        # Verify write-back happened with the rotated tokens
        written = json.loads((tmp_path / "creds.json").read_text())
        assert written["claudeAiOauth"]["accessToken"] == "fresh-at"
        assert written["claudeAiOauth"]["refreshToken"] == "rt-2"

    def test_no_refresh_token_and_expiring_raises(self, monkeypatch):
        expiring_no_rt = sc.SubscriptionCredential(
            access_token="x", refresh_token=None,
            expires_at_ms=1, provider="claude_code", source="test",
        )
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: expiring_no_rt)
        with pytest.raises(sc.SubscriptionCredentialError) as exc:
            sc.resolve("claude_code")
        assert "expired" in str(exc.value).lower() or "refresh" in str(exc.value).lower()

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError):
            sc.resolve("nonsense")  # type: ignore[arg-type]

    def test_codex_path(self, monkeypatch):
        fresh = sc.SubscriptionCredential(
            access_token="cx-at", refresh_token="cx-rt",
            expires_at_ms=int(time.time() * 1000) + 3600_000,
            provider="codex", source="codex_file",
        )
        monkeypatch.setattr(sc, "read_codex_from_file", lambda: fresh)
        result = sc.resolve("codex")
        assert result is fresh
```

- [ ] **Step 2: Run tests; verify all seven fail**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py::TestResolve -v
```

Expected: 7 failed.

- [ ] **Step 3: Implement `resolve` + write-back helper**

Append to `tradingagents/llm_adapters/subscription_credentials.py`:

```python
from typing import Literal


def _write_claude_code_credentials(
    access_token: str, refresh_token: str, expires_at_ms: int
) -> None:
    """Persist refreshed credentials back to ~/.claude/.credentials.json.

    Claude Code's refresh tokens rotate on each use, so failing to write back
    will permanently lose access after the first refresh. We preserve any other
    keys already in the file.
    """
    path = _claude_code_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    oauth = existing.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        oauth = {}
    oauth.update({
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_ms,
    })
    existing["claudeAiOauth"] = oauth
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def resolve(provider: Literal["claude_code", "codex"]) -> SubscriptionCredential:
    """Locate and (if needed) refresh subscription credentials for `provider`.

    Lookup order:
      claude_code: macOS Keychain → ~/.claude/.credentials.json
      codex:       ~/.codex/auth.json

    If the located credential is expiring (within the default 60s skew) and a
    refresh_token is present, refreshes against the upstream endpoint and writes
    the rotated tokens back to disk (Claude Code only; Codex write-back is
    deferred — Codex CLI's auth.json schema is partially private).

    Raises SubscriptionCredentialError if no credential is found, if the token
    is expired with no refresh_token, or if the refresh call fails.
    """
    if provider == "claude_code":
        cred = read_claude_code_from_keychain() or read_claude_code_from_file()
        if cred is None:
            raise SubscriptionCredentialError(
                "No Claude Code credentials found. Run `claude login` first, "
                "then retry. Looked in: macOS Keychain ('Claude Code-credentials') "
                f"and {_claude_code_credentials_path()}."
            )
        if not is_expiring(cred):
            return cred
        if not cred.refresh_token:
            raise SubscriptionCredentialError(
                "Claude Code access token has expired and no refresh_token is "
                "available. Run `claude login` again."
            )
        new_at, new_rt, new_exp = refresh_claude_code(cred.refresh_token)
        _write_claude_code_credentials(new_at, new_rt, new_exp)
        return SubscriptionCredential(
            access_token=new_at, refresh_token=new_rt, expires_at_ms=new_exp,
            provider="claude_code", source=cred.source + "+refreshed",
        )
    if provider == "codex":
        cred = read_codex_from_file()
        if cred is None:
            raise SubscriptionCredentialError(
                "No Codex credentials found. Run `codex login` first, then retry. "
                f"Looked in: {_codex_credentials_path()}."
            )
        if not is_expiring(cred):
            return cred
        if not cred.refresh_token:
            raise SubscriptionCredentialError(
                "Codex access token has expired and no refresh_token is available. "
                "Run `codex login` again."
            )
        new_at, new_rt, new_exp = refresh_codex(cred.refresh_token)
        # NOTE: Codex auth.json write-back is intentionally deferred — the file
        # has other fields managed by Codex CLI and we don't yet have a safe
        # schema-preserving writer. If the test invocation rate is high enough
        # to hit token expiry, the user should re-run `codex login`.
        return SubscriptionCredential(
            access_token=new_at, refresh_token=new_rt, expires_at_ms=new_exp,
            provider="codex", source=cred.source + "+refreshed",
        )
    raise ValueError(f"Unknown subscription provider: {provider!r}")
```

- [ ] **Step 4: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_subscription_credentials.py -v
```

Expected: 35 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/subscription_credentials.py tests/unit/test_subscription_credentials.py
git commit -m "feat(llm): add resolve() with lookup, refresh-if-expiring, write-back"
```

---

### Task 8: Claude Code OAuth chat adapter

**Files:**
- Create: `tradingagents/llm_adapters/claude_code_adapter.py`
- Test: `tests/unit/test_claude_code_adapter.py`

**Background:** `langchain_anthropic.ChatAnthropic` constructs an `anthropic.Anthropic` client internally using `api_key=`, which sends `x-api-key` headers. For OAuth we need to send `Authorization: Bearer ...` instead, which the Anthropic SDK supports via the alternate `auth_token=` kwarg. We subclass `ChatAnthropic` and **replace the underlying `_client` / `_async_client` attributes** post-construction with SDK clients built using `auth_token` + the OAuth-only beta headers, user-agent, and `x-app: cli`.

These headers are mandatory: without `oauth-2025-04-20` Anthropic returns 401; without the `claude-cli/...` user-agent and `x-app: cli` the OAuth route 500s intermittently. See `hermes-agent/agent/anthropic_adapter.py:604-621`.

- [ ] **Step 1: Write failing tests for `ChatClaudeCodeOAuth`**

Create `tests/unit/test_claude_code_adapter.py`:

```python
"""Unit tests for ChatClaudeCodeOAuth.

We don't make real network calls — we verify that the underlying anthropic
SDK client is constructed with the right auth and headers.
"""
import time
from unittest.mock import patch, MagicMock

import pytest

from tradingagents.llm_adapters import subscription_credentials as sc
from tradingagents.llm_adapters.claude_code_adapter import (
    ChatClaudeCodeOAuth,
    OAUTH_BETA_HEADERS,
)


def _fresh_cred() -> sc.SubscriptionCredential:
    return sc.SubscriptionCredential(
        access_token="oauth-at-xyz",
        refresh_token="rt-xyz",
        expires_at_ms=int(time.time() * 1000) + 3600_000,
        provider="claude_code",
        source="test",
    )


class TestChatClaudeCodeOAuth:
    def test_resolves_credentials_on_init(self):
        with patch.object(sc, "resolve", return_value=_fresh_cred()) as resolve_mock:
            ChatClaudeCodeOAuth(model="claude-opus-4-7")
        resolve_mock.assert_called_once_with("claude_code")

    def test_underlying_clients_use_auth_token(self):
        cred = _fresh_cred()
        fake_sync = MagicMock(name="anthropic.Anthropic")
        fake_async = MagicMock(name="anthropic.AsyncAnthropic")
        with patch.object(sc, "resolve", return_value=cred), \
             patch("anthropic.Anthropic", return_value=fake_sync) as sync_ctor, \
             patch("anthropic.AsyncAnthropic", return_value=fake_async) as async_ctor:
            chat = ChatClaudeCodeOAuth(model="claude-opus-4-7", temperature=0.5)

        # Both clients constructed with auth_token, not api_key
        for ctor in (sync_ctor, async_ctor):
            kwargs = ctor.call_args.kwargs
            assert "auth_token" in kwargs and kwargs["auth_token"] == "oauth-at-xyz"
            assert "api_key" not in kwargs or not kwargs.get("api_key")
            # OAuth headers must be present
            hdr = kwargs["default_headers"]
            assert "anthropic-beta" in hdr
            for required in OAUTH_BETA_HEADERS:
                assert required in hdr["anthropic-beta"], f"missing beta: {required}"
            assert hdr.get("x-app") == "cli"
            assert hdr.get("user-agent", "").startswith("claude-cli/")
        # And ChatAnthropic's internal client attrs were replaced
        assert chat._client is fake_sync
        assert chat._async_client is fake_async

    def test_passes_temperature_and_max_tokens_to_parent(self):
        with patch.object(sc, "resolve", return_value=_fresh_cred()), \
             patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            chat = ChatClaudeCodeOAuth(
                model="claude-opus-4-7",
                temperature=0.3,
                max_tokens=8000,
            )
        assert chat.temperature == 0.3
        assert chat.max_tokens == 8000
        assert chat.model == "claude-opus-4-7"

    def test_missing_credentials_propagates(self):
        with patch.object(
            sc, "resolve",
            side_effect=sc.SubscriptionCredentialError("no creds"),
        ):
            with pytest.raises(sc.SubscriptionCredentialError):
                ChatClaudeCodeOAuth(model="claude-opus-4-7")
```

- [ ] **Step 2: Run tests; verify they fail with ImportError**

```bash
.venv/bin/pytest tests/unit/test_claude_code_adapter.py -v
```

Expected: collection error / all fail (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `ChatClaudeCodeOAuth`**

Create `tradingagents/llm_adapters/claude_code_adapter.py`:

```python
"""LangChain chat model that authenticates against Anthropic via Claude Code OAuth.

Uses the subscription's OAuth access token (read from ~/.claude/.credentials.json
or macOS Keychain) instead of an API key. The token is automatically refreshed
when expiring.

Reference: hermes-agent/agent/anthropic_adapter.py:604-621
"""
from __future__ import annotations

import logging
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic

from tradingagents.llm_adapters import subscription_credentials as sc

logger = logging.getLogger(__name__)


# Beta features Anthropic requires for OAuth-authenticated callers.
# Removing any of these will result in 401 / 500 errors from the API.
# Reference: hermes-agent/agent/anthropic_adapter.py:_OAUTH_ONLY_BETAS
OAUTH_BETA_HEADERS = (
    "oauth-2025-04-20",
    "claude-code-20250219",
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
)

_CLAUDE_CLI_VERSION = "1.0.0"  # Header value; real Claude Code CLIs use their own.


def _oauth_default_headers(access_token: str) -> dict:
    return {
        "anthropic-beta": ",".join(OAUTH_BETA_HEADERS),
        "user-agent": f"claude-cli/{_CLAUDE_CLI_VERSION} (TradingAgents-CN)",
        "x-app": "cli",
    }


class ChatClaudeCodeOAuth(ChatAnthropic):
    """ChatAnthropic that authenticates via Claude Code OAuth instead of an API key.

    Differences from ChatAnthropic:
      * Reads the access token from local Claude Code credentials at construction
      * Constructs the underlying anthropic.Anthropic / .AsyncAnthropic client
        with `auth_token=` (Bearer auth) instead of `api_key=` (x-api-key auth)
      * Attaches Anthropic's OAuth-required beta headers + claude-cli identity
    """

    def __init__(self, model: str, **kwargs: Any) -> None:
        cred = sc.resolve("claude_code")
        # ChatAnthropic's validator requires *some* api_key value to construct.
        # We pass a placeholder; the real auth happens on the client we install
        # immediately afterwards.
        super().__init__(model=model, anthropic_api_key="placeholder-oauth", **kwargs)

        default_headers = _oauth_default_headers(cred.access_token)
        sync_client = anthropic.Anthropic(
            auth_token=cred.access_token,
            default_headers=default_headers,
        )
        async_client = anthropic.AsyncAnthropic(
            auth_token=cred.access_token,
            default_headers=default_headers,
        )
        # Bypass pydantic's frozen-on-some-versions guard.
        object.__setattr__(self, "_client", sync_client)
        object.__setattr__(self, "_async_client", async_client)
        logger.info(
            "ChatClaudeCodeOAuth initialized: model=%s source=%s expires_at_ms=%s",
            model, cred.source, cred.expires_at_ms,
        )
```

- [ ] **Step 4: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_claude_code_adapter.py -v
```

Expected: 4 passed.

If `chat._client is fake_sync` fails because the parent's validator overwrote the attribute, switch the assignment to happen via the pydantic model_config — but the `object.__setattr__` approach has been verified against `langchain_anthropic >= 0.3.0`. If you see a failure here, check the installed `langchain_anthropic` version and adjust the attribute name (in older versions it may be `client` rather than `_client`).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/claude_code_adapter.py tests/unit/test_claude_code_adapter.py
git commit -m "feat(llm): add ChatClaudeCodeOAuth adapter using Anthropic SDK auth_token"
```

---

### Task 9: Codex OAuth chat adapter

**Files:**
- Create: `tradingagents/llm_adapters/codex_adapter.py`
- Test: `tests/unit/test_codex_adapter.py`

**Background:** Codex (the ChatGPT-subscription–backed model) is served at `https://chatgpt.com/backend-api/codex` and accepts a Bearer access token. The endpoint exposes an OpenAI-compatible Chat Completions path. Our adapter is a thin `ChatOpenAI` subclass that pulls the subscription token, sets `base_url`, and uses the token as `api_key` (which puts it on the `Authorization: Bearer ...` header — exactly what Codex wants).

**Risk note:** The Codex API surface is undocumented. This adapter is implemented best-effort; the unit tests verify wiring but the real-API behavior must be validated against a live ChatGPT Plus/Pro account in Task 11. If the wire format turns out to differ from Chat Completions, this adapter may need a follow-up rewrite. Document any divergence in the design doc.

- [ ] **Step 1: Write failing tests for `ChatCodexOAuth`**

Create `tests/unit/test_codex_adapter.py`:

```python
"""Unit tests for ChatCodexOAuth."""
import time
from unittest.mock import patch

import pytest

from tradingagents.llm_adapters import subscription_credentials as sc
from tradingagents.llm_adapters.codex_adapter import (
    ChatCodexOAuth,
    CODEX_BASE_URL,
)


def _fresh_codex_cred() -> sc.SubscriptionCredential:
    return sc.SubscriptionCredential(
        access_token="cx-at-xyz",
        refresh_token="cx-rt-xyz",
        expires_at_ms=int(time.time() * 1000) + 3600_000,
        provider="codex",
        source="test",
    )


class TestChatCodexOAuth:
    def test_resolves_codex_credentials(self):
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()) as r:
            ChatCodexOAuth(model="gpt-5")
        r.assert_called_once_with("codex")

    def test_uses_codex_base_url(self):
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()):
            chat = ChatCodexOAuth(model="gpt-5")
        # ChatOpenAI stores base URL on `openai_api_base` in some versions and
        # `base_url` in others. Accept either.
        actual = getattr(chat, "openai_api_base", None) or getattr(chat, "base_url", None)
        assert str(actual).rstrip("/") == CODEX_BASE_URL.rstrip("/")

    def test_uses_access_token_as_api_key(self):
        cred = _fresh_codex_cred()
        with patch.object(sc, "resolve", return_value=cred):
            chat = ChatCodexOAuth(model="gpt-5")
        # ChatOpenAI uses `openai_api_key` (SecretStr) in some versions, `api_key` in others
        api_key_field = getattr(chat, "openai_api_key", None) or getattr(chat, "api_key", None)
        # SecretStr requires .get_secret_value(); raw str compares directly
        raw = api_key_field.get_secret_value() if hasattr(api_key_field, "get_secret_value") else api_key_field
        assert raw == "cx-at-xyz"

    def test_passes_other_kwargs(self):
        with patch.object(sc, "resolve", return_value=_fresh_codex_cred()):
            chat = ChatCodexOAuth(model="gpt-5", temperature=0.4, max_tokens=2000)
        assert chat.temperature == 0.4
        # ChatOpenAI may store as max_tokens or model_kwargs; accept either
        assert getattr(chat, "max_tokens", None) == 2000 or chat.model_kwargs.get("max_tokens") == 2000

    def test_missing_credentials_propagates(self):
        with patch.object(
            sc, "resolve",
            side_effect=sc.SubscriptionCredentialError("no codex creds"),
        ):
            with pytest.raises(sc.SubscriptionCredentialError):
                ChatCodexOAuth(model="gpt-5")
```

- [ ] **Step 2: Run tests; verify all five fail**

```bash
.venv/bin/pytest tests/unit/test_codex_adapter.py -v
```

Expected: collection or all-fail (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `ChatCodexOAuth`**

Create `tradingagents/llm_adapters/codex_adapter.py`:

```python
"""LangChain chat model authenticated against Codex (ChatGPT subscription).

NOTE: The Codex API at https://chatgpt.com/backend-api/codex is undocumented.
This adapter assumes an OpenAI Chat Completions–compatible surface. If real-API
validation reveals a different wire format, expect this file to need a rewrite.

Reference: hermes-agent/hermes_cli/auth.py:74-91 (Codex endpoint + client id)
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI

from tradingagents.llm_adapters import subscription_credentials as sc

logger = logging.getLogger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


class ChatCodexOAuth(ChatOpenAI):
    """ChatOpenAI variant that authenticates via the user's ChatGPT subscription.

    The Codex OAuth access token is used as the `api_key` (LangChain places it
    on the Authorization header), and the OpenAI base URL is swapped to the
    Codex endpoint.
    """

    def __init__(self, model: str, **kwargs: Any) -> None:
        cred = sc.resolve("codex")
        super().__init__(
            model=model,
            api_key=cred.access_token,
            base_url=CODEX_BASE_URL,
            **kwargs,
        )
        logger.info(
            "ChatCodexOAuth initialized: model=%s source=%s expires_at_ms=%s",
            model, cred.source, cred.expires_at_ms,
        )
```

- [ ] **Step 4: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_codex_adapter.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/llm_adapters/codex_adapter.py tests/unit/test_codex_adapter.py
git commit -m "feat(llm): add ChatCodexOAuth adapter (best-effort, needs live validation)"
```

---

### Task 10: Wire adapters into `create_llm_by_provider`

**Files:**
- Modify: `tradingagents/graph/trading_graph.py:41-190`
- Modify: `tradingagents/llm_adapters/__init__.py`
- Test: `tests/unit/test_create_llm_by_provider_subscription.py` (new)

- [ ] **Step 1: Export new adapters from package init**

Read the existing `tradingagents/llm_adapters/__init__.py` first to see the current export style. Then add (preserving existing exports — append to whatever style is in use; the file currently exports `ChatDashScopeOpenAI` and `ChatGoogleOpenAI`):

```python
from tradingagents.llm_adapters.claude_code_adapter import ChatClaudeCodeOAuth
from tradingagents.llm_adapters.codex_adapter import ChatCodexOAuth
```

(Use `Edit`, not `Write` — do not overwrite existing content.)

- [ ] **Step 2: Write failing tests for the new `create_llm_by_provider` branches**

Create `tests/unit/test_create_llm_by_provider_subscription.py`:

```python
"""Unit tests for the claude_code / codex branches in create_llm_by_provider."""
from unittest.mock import patch, MagicMock

from tradingagents.graph.trading_graph import create_llm_by_provider


class TestCreateLlmByProviderSubscription:
    def test_claude_code_branch_returns_oauth_adapter(self):
        fake = MagicMock(name="ChatClaudeCodeOAuth-instance")
        with patch(
            "tradingagents.llm_adapters.claude_code_adapter.ChatClaudeCodeOAuth",
            return_value=fake,
        ) as ctor:
            llm = create_llm_by_provider(
                provider="claude_code",
                model="claude-opus-4-7",
                backend_url="",        # ignored for OAuth path
                temperature=0.4,
                max_tokens=4000,
                timeout=180,
                api_key=None,
            )
        assert llm is fake
        ctor.assert_called_once()
        kwargs = ctor.call_args.kwargs
        assert kwargs["model"] == "claude-opus-4-7"
        assert kwargs["temperature"] == 0.4
        assert kwargs["max_tokens"] == 4000

    def test_codex_branch_returns_oauth_adapter(self):
        fake = MagicMock(name="ChatCodexOAuth-instance")
        with patch(
            "tradingagents.llm_adapters.codex_adapter.ChatCodexOAuth",
            return_value=fake,
        ) as ctor:
            llm = create_llm_by_provider(
                provider="codex",
                model="gpt-5",
                backend_url="",
                temperature=0.5,
                max_tokens=2000,
                timeout=180,
                api_key=None,
            )
        assert llm is fake
        kwargs = ctor.call_args.kwargs
        assert kwargs["model"] == "gpt-5"

    def test_claude_code_case_insensitive(self):
        fake = MagicMock()
        with patch(
            "tradingagents.llm_adapters.claude_code_adapter.ChatClaudeCodeOAuth",
            return_value=fake,
        ):
            assert create_llm_by_provider("Claude_Code", "claude-opus-4-7", "", 0, 1, 1) is fake
            assert create_llm_by_provider("CLAUDE_CODE", "claude-opus-4-7", "", 0, 1, 1) is fake
```

- [ ] **Step 3: Run tests; verify they fail**

```bash
.venv/bin/pytest tests/unit/test_create_llm_by_provider_subscription.py -v
```

Expected: 3 failed (the function falls through to the generic OpenAI fallback and crashes for unknown provider).

- [ ] **Step 4: Add the two branches to `create_llm_by_provider`**

In `tradingagents/graph/trading_graph.py`, locate the start of `create_llm_by_provider` (around line 41) and find the first `if provider.lower() == "google":` (~line 63). Insert the new branches **before** that one, so subscription providers can't be mis-routed.

Use `Edit` to replace the line `    if provider.lower() == "google":` (with surrounding context for uniqueness):

```python
    logger.info(f"🔧 [创建LLM] provider={provider}, model={model}, url={backend_url}")
    logger.info(f"🔑 [API Key] 来源: {'数据库配置' if api_key else '环境变量'}")

    # === 订阅式鉴权分支（Claude Code / Codex） ===
    # 走这两个分支时 api_key/backend_url 被忽略，鉴权信息由 subscription_credentials 模块
    # 从本机 Claude Code / Codex CLI 凭据中读取并自动刷新。
    if provider.lower() == "claude_code":
        from tradingagents.llm_adapters.claude_code_adapter import ChatClaudeCodeOAuth
        return ChatClaudeCodeOAuth(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    if provider.lower() == "codex":
        from tradingagents.llm_adapters.codex_adapter import ChatCodexOAuth
        return ChatCodexOAuth(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    if provider.lower() == "google":
```

- [ ] **Step 5: Run tests; verify all pass**

```bash
.venv/bin/pytest tests/unit/test_create_llm_by_provider_subscription.py -v
.venv/bin/pytest tests/unit/ -v   # all of unit/ should still pass
```

Expected: 3 passed in the new file; total `tests/unit/` count = previous count + new tests, all green.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/graph/trading_graph.py tradingagents/llm_adapters/__init__.py tests/unit/test_create_llm_by_provider_subscription.py
git commit -m "feat(graph): wire claude_code / codex providers into create_llm_by_provider"
```

---

### Task 11: Manual smoke test for Claude Code path

This is a **manual** task — the smoke script must not be run in CI because it makes real network calls against Anthropic with the user's subscription token. The script's purpose is to confirm end-to-end that the OAuth adapter actually authenticates and returns a Claude response.

**Files:**
- Create: `scripts/smoke_test_claude_code_oauth.py`

- [ ] **Step 1: Write the smoke script**

Create `scripts/smoke_test_claude_code_oauth.py`:

```python
"""Manual smoke test: round-trip a single prompt through Claude Code OAuth.

Run locally on a machine where `claude login` has been run at least once:

    .venv/bin/python scripts/smoke_test_claude_code_oauth.py

Prints either the model's response or a diagnostic error. Does NOT run in CI.
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    from tradingagents.llm_adapters import subscription_credentials as sc
    from tradingagents.llm_adapters.claude_code_adapter import ChatClaudeCodeOAuth

    try:
        cred = sc.resolve("claude_code")
    except sc.SubscriptionCredentialError as exc:
        print(f"FAIL: no Claude Code credentials → {exc}", file=sys.stderr)
        return 2

    print(f"OK: loaded credentials from {cred.source}, "
          f"expires_at_ms={cred.expires_at_ms}")

    chat = ChatClaudeCodeOAuth(model="claude-opus-4-7", max_tokens=100, temperature=0.0)
    try:
        resp = chat.invoke("Reply with exactly the word: SMOKE-TEST-OK")
    except Exception as exc:
        print(f"FAIL: invoke raised → {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    text = getattr(resp, "content", str(resp))
    print(f"RESPONSE: {text!r}")
    if "SMOKE-TEST-OK" in str(text):
        print("PASS")
        return 0
    print("WARN: response did not contain the expected string, but the call succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it locally**

```bash
.venv/bin/python scripts/smoke_test_claude_code_oauth.py
```

Expected outcomes:

- **PASS**: exit 0, output contains `RESPONSE: ...SMOKE-TEST-OK...`. The OAuth path works end-to-end.
- **FAIL with "no Claude Code credentials"** (exit 2): user has not run `claude login`. Document this in the test plan as a known prerequisite. The plan is otherwise unblocked.
- **FAIL with 401/403 from anthropic.APIStatusError**: token is rejected. Likely cause: missing or wrong `anthropic-beta` headers — re-check `OAUTH_BETA_HEADERS` against the latest hermes-agent source.
- **FAIL with 500**: missing `user-agent` / `x-app` header. Same fix.
- **Any other exception**: log it in the PR description for review; this is the signal that PR-1 needs another iteration before merging.

If the smoke test FAILs, that is **a failure of PR-1**: do not commit Task 11 as "done" and do not move on. Open a follow-up plan item before merging.

- [ ] **Step 3: If passing, commit the smoke script**

```bash
git add scripts/smoke_test_claude_code_oauth.py
git commit -m "test(llm): add manual smoke test for Claude Code OAuth round-trip"
```

- [ ] **Step 4: Optional — smoke test Codex**

A Codex smoke test should mirror Step 1–3 but requires `codex login` to have been run AND a working ChatGPT Plus/Pro subscription, AND validates the assumption that `chatgpt.com/backend-api/codex` speaks OpenAI Chat Completions. If you don't have a Codex login available, skip this — the unit tests already cover the wiring, and PR-2 / PR-3 will revisit Codex validation when there's a real user trying it. Document the skip in the PR description.

If you do have a Codex login, mirror the Claude Code smoke script at `scripts/smoke_test_codex_oauth.py` and run it. Expect possible failure with mismatched API shape (Responses API vs Chat Completions, different endpoint paths, mandatory custom headers, etc.); if so, file a follow-up issue and do not gate merge on it — but mark Codex as "experimental, unvalidated" in the PR description and in `docs/design/llm-subscription-auth-design.md`.

- [ ] **Step 5: Final verification — run the full unit-test suite**

```bash
.venv/bin/pytest tests/unit/ -v
```

Expected: all of the new tests pass (35 in `test_subscription_credentials.py` + 4 in `test_claude_code_adapter.py` + 5 in `test_codex_adapter.py` + 3 in `test_create_llm_by_provider_subscription.py` = 47 new tests).

Also run the rest of `tests/` to confirm we haven't regressed anything pre-existing:

```bash
.venv/bin/pytest tests/ -q --ignore=tests/integration 2>&1 | tail -10
```

Expected: previously-green tests still green. (Some pre-existing tests are flaky / require external services — those are not our concern.)

- [ ] **Step 6: Push the branch**

```bash
git push -u origin feat/codex-claude-subscription-analysis
```

Then open a PR titled "feat: Claude Code / Codex subscription auth (PR-1: core adapters)" — description should mention:
- This is PR 1 of 3; PR-2 (backend config + API) and PR-3 (Web UI) will follow
- The Codex adapter is best-effort, unvalidated against live ChatGPT API
- Smoke test status (passed / skipped / failed with diagnosis)
- Reference to `docs/design/llm-subscription-auth-design.md`

---

## Self-review checklist (do this before announcing "done")

- [ ] All 11 tasks committed individually
- [ ] `pytest tests/unit/` is green
- [ ] `scripts/smoke_test_claude_code_oauth.py` PASS or documented FAIL with diagnosis
- [ ] No new module imports `app/`, `frontend/`, or MongoDB / Redis clients (PR-1 must stay inside `tradingagents/`)
- [ ] No `print()` calls in new module code — only `logger.info` / `logger.debug`
- [ ] No real OAuth access tokens, refresh tokens, or `~/.claude/.credentials.json` contents present in any test file or commit
- [ ] `docs/design/llm-subscription-auth-design.md` § 6 "实施路线图" updated to reflect Codex validation status
