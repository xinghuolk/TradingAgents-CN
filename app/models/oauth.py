"""Pydantic models for OAuth subscription auth (PR-2)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, HttpUrl

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
