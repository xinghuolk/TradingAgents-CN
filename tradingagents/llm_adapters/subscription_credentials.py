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
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Tuple

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


_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"


def _platform_system() -> str:
    """Wrapper around `platform.system()` so tests can override."""
    return platform.system()


_CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_CLAUDE_CODE_TOKEN_ENDPOINTS = (
    "https://platform.claude.com/v1/oauth/token",
    "https://console.anthropic.com/v1/oauth/token",
)
_CLAUDE_CODE_USER_AGENT = "TradingAgents-CN/1.0 (claude-code-oauth)"


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
        if not isinstance(payload, dict):
            raise SubscriptionCredentialError(
                f"Claude Code refresh response is not a JSON object: {payload!r}"
            )
        new_access = payload.get("access_token") or ""
        if not new_access:
            raise SubscriptionCredentialError(
                f"Claude Code refresh response missing access_token: {payload!r}"
            )
        new_refresh = payload.get("refresh_token") or refresh_token
        try:
            expires_in = int(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        expires_at_ms = int(time.time() * 1000) + expires_in * 1000
        return new_access, new_refresh, expires_at_ms
    raise SubscriptionCredentialError(
        f"Claude Code token refresh failed against all endpoints: {last_exc}"
    )


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
    if not isinstance(payload, dict):
        raise SubscriptionCredentialError(
            f"Codex refresh response is not a JSON object: {payload!r}"
        )
    new_access = payload.get("access_token") or ""
    if not new_access:
        raise SubscriptionCredentialError(
            f"Codex refresh response missing access_token: {payload!r}"
        )
    new_refresh = payload.get("refresh_token") or refresh_token
    try:
        expires_in = int(payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    expires_at_ms = int(time.time() * 1000) + expires_in * 1000
    return new_access, new_refresh, expires_at_ms


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
