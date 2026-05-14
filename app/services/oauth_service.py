"""OAuth subscription service: storage, refresh, retrieval.

PKCE and device-code flow logic live in this module too (added in later tasks);
this file currently has only the CRUD + resolve layer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets as _secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Literal

from motor.motor_asyncio import AsyncIOMotorCollection

from app.models.oauth import OAuthCredentialError
from app.services import oauth_crypto as oc

logger = logging.getLogger(__name__)

# 60-second skew window: refresh when token expires within this margin.
_REFRESH_SKEW_SECONDS = 60

# --- PKCE / Anthropic Claude Code OAuth constants ---
_ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_ANTHROPIC_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_ANTHROPIC_SCOPES = "org:create_api_key user:profile user:inference"
_PKCE_TTL_SECONDS = 600

# --- Codex / OpenAI device-code OAuth constants ---
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_DEVICE_CODE_URL = "https://auth.openai.com/oauth/device/code"
_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


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
