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


from app.models.oauth import AuthorizeClaudeCodeResponse  # noqa: E402


def _derive_redirect_uri(request: Request, provider: str) -> str:
    """Construct https://<host>/api/oauth/callback/<provider> from request headers."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    return f"{scheme}://{host}/api/oauth/callback/{provider}"


@router.get("/authorize/claude_code", response_model=AuthorizeClaudeCodeResponse)
async def authorize_claude_code(
    request: Request,
    user: dict = Depends(get_current_user),
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


from app.models.oauth import AuthorizeCodexResponse, PollCodexResponse  # noqa: E402


@router.post("/authorize/codex", response_model=AuthorizeCodexResponse)
async def authorize_codex(user: dict = Depends(get_current_user)):
    """Start the Codex device-code flow."""
    from app.services import oauth_service
    result = await oauth_service.start_device_code_flow(
        redis_client=get_redis_client(),
        user_id=user["_id"],
        http_client=get_http_client(),
    )
    return AuthorizeCodexResponse(**result)


@router.post("/poll/codex", response_model=PollCodexResponse)
async def poll_codex(user: dict = Depends(get_current_user)):
    """Poll Codex device-code flow for completion."""
    from app.services import oauth_service
    result = await oauth_service.poll_device_code_flow(
        redis_client=get_redis_client(),
        collection=get_credentials_collection(),
        user_id=user["_id"],
        http_client=get_http_client(),
    )
    return PollCodexResponse(**result)


@router.post("/refresh/{provider}")
async def refresh_endpoint(
    provider: OAuthProvider,
    user: dict = Depends(get_current_user),
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
