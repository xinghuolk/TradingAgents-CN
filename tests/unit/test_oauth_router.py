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
    async def _fake_current_user():
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
        monkeypatch.setattr(oauth_router, "get_credentials_collection", lambda: AsyncMock())

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
