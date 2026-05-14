"""Read and refresh OAuth credentials for Claude Code / Codex subscriptions.

Credentials are managed by the official Claude Code / Codex CLIs on the user's
machine — this module reads them and (when expiring) calls the upstream OAuth
refresh endpoint. We never run the full authorization flow ourselves; users must
run `claude login` / `codex login` once first.

Reference: hermes-agent/agent/anthropic_adapter.py:580-870
"""
from __future__ import annotations

import json
import logging
import platform
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)


class SubscriptionCredentialError(RuntimeError):
    """Raised when subscription credentials cannot be located or refreshed."""


@dataclass(frozen=True)
class SubscriptionCredential:
    """A loaded OAuth credential for a subscription provider.

    `access_token` and `refresh_token` are intentionally excluded from __repr__
    so that diagnostic logging (e.g. `logger.debug("loaded %s", cred)`) cannot
    leak live OAuth credentials into log files. Use `cred.access_token`
    explicitly when you need the value.
    """

    access_token: str = field(repr=False)
    refresh_token: Optional[str] = field(repr=False)
    expires_at_ms: int  # epoch ms; 0 means never-expires (managed keys)
    provider: Literal["claude_code", "codex"]
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
