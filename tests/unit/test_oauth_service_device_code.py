"""Unit tests for oauth_service Codex device-code flow."""
import base64
import secrets
from unittest.mock import AsyncMock

import pytest

from app.services import oauth_service as svc


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    raw = secrets.token_bytes(32)
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", base64.b64encode(raw).decode())


@pytest.fixture
def fake_redis():
    return AsyncMock()


@pytest.fixture
def fake_http_with_device_response():
    """httpx mock that returns a valid device-code start response."""
    resp = AsyncMock()
    resp.status_code = 200
    resp.json = lambda: {
        "device_code": "dev-code-xyz",
        "user_code": "ABCD-EFGH",
        "verification_uri_complete": "https://chatgpt.com/device?code=ABCD-EFGH",
        "expires_in": 600,
        "interval": 5,
    }
    http = AsyncMock()
    http.post.return_value = resp
    return http


class TestStartDeviceCodeFlow:
    async def test_returns_user_code_and_uri(self, fake_redis, fake_http_with_device_response):
        result = await svc.start_device_code_flow(
            redis_client=fake_redis,
            user_id="u1",
            http_client=fake_http_with_device_response,
        )
        assert result["user_code"] == "ABCD-EFGH"
        assert "chatgpt.com" in result["verification_uri"]
        assert result["interval"] == 5
        assert result["expires_in"] == 600

    async def test_stores_device_code_in_redis(self, fake_redis, fake_http_with_device_response):
        await svc.start_device_code_flow(
            redis_client=fake_redis,
            user_id="u1",
            http_client=fake_http_with_device_response,
        )
        fake_redis.setex.assert_called_once()
        key, ttl, value = fake_redis.setex.call_args.args
        assert key == "oauth:device:u1:codex"
        assert ttl == 600
        import json
        parsed = json.loads(value)
        assert parsed["device_code"] == "dev-code-xyz"
        assert parsed["interval"] == 5

    async def test_posts_to_correct_endpoint(self, fake_redis, fake_http_with_device_response):
        await svc.start_device_code_flow(
            redis_client=fake_redis,
            user_id="u1",
            http_client=fake_http_with_device_response,
        )
        url = fake_http_with_device_response.post.call_args.args[0]
        assert url == "https://auth.openai.com/oauth/device/code"
        body = fake_http_with_device_response.post.call_args.kwargs.get("data", {})
        assert body.get("client_id") == "app_EMoamEEZ73f0CkXaXp7hrann"


class TestPollDeviceCodeFlow:
    async def test_no_device_code_in_redis_returns_expired(self, fake_redis):
        fake_redis.get.return_value = None
        fake_http = AsyncMock()
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=fake_http,
        )
        assert result["status"] == "expired"

    async def test_pending_returns_pending(self, fake_redis):
        import json
        fake_redis.get.return_value = json.dumps({
            "device_code": "dev-x",
            "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        # Mock upstream "authorization_pending"
        resp = AsyncMock()
        resp.status_code = 400
        resp.json = lambda: {"error": "authorization_pending"}
        http = AsyncMock()
        http.post.return_value = resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "pending"
        assert result["increment_interval"] is False

    async def test_slow_down_returns_increment(self, fake_redis):
        import json
        fake_redis.get.return_value = json.dumps({
            "device_code": "dev-x", "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        resp = AsyncMock()
        resp.status_code = 400
        resp.json = lambda: {"error": "slow_down"}
        http = AsyncMock()
        http.post.return_value = resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "pending"
        assert result["increment_interval"] is True

    async def test_expired_token_clears_redis(self, fake_redis):
        import json
        fake_redis.get.return_value = json.dumps({
            "device_code": "dev-x", "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        resp = AsyncMock()
        resp.status_code = 400
        resp.json = lambda: {"error": "expired_token"}
        http = AsyncMock()
        http.post.return_value = resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "expired"
        fake_redis.delete.assert_called_once()

    async def test_success_stores_credentials_and_returns_bound(self, fake_redis):
        import json
        fake_redis.get.return_value = json.dumps({
            "device_code": "dev-x", "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })
        resp = AsyncMock()
        resp.status_code = 200
        resp.json = lambda: {
            "access_token": "cx-at-final",
            "refresh_token": "cx-rt-final",
            "expires_in": 1800,
        }
        http = AsyncMock()
        http.post.return_value = resp
        fake_collection = AsyncMock()
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=fake_collection,
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "bound"
        fake_collection.update_one.assert_called_once()
        fake_redis.delete.assert_called_once()
