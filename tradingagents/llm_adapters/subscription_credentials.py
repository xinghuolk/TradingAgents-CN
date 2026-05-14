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
