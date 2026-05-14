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
