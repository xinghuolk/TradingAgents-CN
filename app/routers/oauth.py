"""REST endpoints for OAuth subscription auth (PR-2)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
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


# Re-export the existing auth dep so tests can override it via dependency_overrides.
from app.routers.auth_db import get_current_user  # noqa: E402


# --- routes ---

@router.get("/status/{provider}", response_model=OAuthStatusResponse)
async def status_endpoint(
    provider: OAuthProvider,
    user: dict = Depends(get_current_user),
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
    user: dict = Depends(get_current_user),
):
    """Delete the binding for (user, provider)."""
    from app.services import oauth_service
    collection = get_credentials_collection()
    await oauth_service.delete_credentials(collection, user["_id"], provider)
