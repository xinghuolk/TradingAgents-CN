"""Unit tests for oauth_service Codex device-code flow (OpenAI deviceauth API)."""
import base64
import json
import secrets
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import oauth_service as svc


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    raw = secrets.token_bytes(32)
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", base64.b64encode(raw).decode())


@pytest.fixture
def fake_redis():
    return AsyncMock()


def _make_resp(status_code: int, body: dict):
    """Build a mock httpx response that returns a fixed JSON body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = lambda: body
    resp.text = json.dumps(body)
    return resp


@pytest.fixture
def fake_http_with_device_response():
    """httpx mock that returns a valid deviceauth/usercode response."""
    resp = _make_resp(200, {
        "device_auth_id": "dev-auth-xyz",
        "user_code": "ABCD-EFGH",
        "interval": 5,
        "expires_in": 600,
    })
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
        assert result["verification_uri"] == "https://auth.openai.com/deviceauth?user_code=ABCD-EFGH"
        assert result["interval"] == 5
        assert result["expires_in"] == 600

    async def test_stores_device_auth_id_in_redis(self, fake_redis, fake_http_with_device_response):
        await svc.start_device_code_flow(
            redis_client=fake_redis,
            user_id="u1",
            http_client=fake_http_with_device_response,
        )
        fake_redis.setex.assert_called_once()
        key, ttl, value = fake_redis.setex.call_args.args
        assert key == "oauth:device:u1:codex"
        assert ttl == 600
        parsed = json.loads(value)
        assert parsed["device_auth_id"] == "dev-auth-xyz"
        assert parsed["user_code"] == "ABCD-EFGH"
        assert parsed["interval"] == 5

    async def test_posts_to_correct_endpoint(self, fake_redis, fake_http_with_device_response):
        await svc.start_device_code_flow(
            redis_client=fake_redis,
            user_id="u1",
            http_client=fake_http_with_device_response,
        )
        url = fake_http_with_device_response.post.call_args.args[0]
        assert url == "https://auth.openai.com/api/accounts/deviceauth/usercode"
        body = fake_http_with_device_response.post.call_args.kwargs.get("json", {})
        assert body.get("client_id") == "app_EMoamEEZ73f0CkXaXp7hrann"


class TestPollDeviceCodeFlow:
    def _redis_state(self, device_auth_id="dev-auth-xyz", user_code="ABCD-EFGH"):
        return json.dumps({
            "device_auth_id": device_auth_id,
            "user_code": user_code,
            "interval": 5,
            "expires_at": "2099-01-01T00:00:00+00:00",
        })

    async def test_no_state_in_redis_returns_expired(self, fake_redis):
        fake_redis.get.return_value = None
        fake_http = AsyncMock()
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=fake_http,
        )
        assert result["status"] == "expired"

    async def test_403_returns_pending(self, fake_redis):
        fake_redis.get.return_value = self._redis_state()
        step3_resp = _make_resp(403, {})
        http = AsyncMock()
        http.post.return_value = step3_resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "pending"
        assert result["increment_interval"] is False

    async def test_404_returns_pending(self, fake_redis):
        fake_redis.get.return_value = self._redis_state()
        step3_resp = _make_resp(404, {})
        http = AsyncMock()
        http.post.return_value = step3_resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "pending"
        assert result["increment_interval"] is False

    async def test_unknown_error_returns_denied(self, fake_redis):
        fake_redis.get.return_value = self._redis_state()
        step3_resp = _make_resp(400, {"error": "some_unexpected_error"})
        http = AsyncMock()
        http.post.return_value = step3_resp
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=AsyncMock(),
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "denied"
        fake_redis.delete.assert_called_once()

    async def test_success_stores_credentials_and_returns_bound(self, fake_redis):
        fake_redis.get.return_value = self._redis_state()

        # Step 3: deviceauth/token returns authorization_code + code_verifier
        step3_resp = _make_resp(200, {
            "authorization_code": "auth-code-abc",
            "code_verifier": "verifier-xyz",
        })
        # Step 4: /oauth/token returns final tokens
        step4_resp = _make_resp(200, {
            "access_token": "cx-at-final",
            "refresh_token": "cx-rt-final",
            "expires_in": 1800,
        })
        http = AsyncMock()
        http.post.side_effect = [step3_resp, step4_resp]

        fake_collection = AsyncMock()
        result = await svc.poll_device_code_flow(
            redis_client=fake_redis,
            collection=fake_collection,
            user_id="u1",
            http_client=http,
        )
        assert result["status"] == "bound"
        assert result["increment_interval"] is False
        fake_collection.update_one.assert_called_once()
        fake_redis.delete.assert_called_once()

        # Verify both calls happened with correct URLs
        assert http.post.call_count == 2
        call_urls = [c.args[0] for c in http.post.call_args_list]
        assert call_urls[0] == "https://auth.openai.com/api/accounts/deviceauth/token"
        assert call_urls[1] == "https://auth.openai.com/oauth/token"

        # Verify step 3 sent JSON body
        step3_json = http.post.call_args_list[0].kwargs.get("json", {})
        assert step3_json == {"device_auth_id": "dev-auth-xyz", "user_code": "ABCD-EFGH"}

        # Verify step 4 sent the authorization_code and code_verifier
        step4_data = http.post.call_args_list[1].kwargs.get("data", {})
        assert step4_data["grant_type"] == "authorization_code"
        assert step4_data["code"] == "auth-code-abc"
        assert step4_data["code_verifier"] == "verifier-xyz"
        assert step4_data["client_id"] == "app_EMoamEEZ73f0CkXaXp7hrann"

    async def test_step4_token_exchange_failure_raises(self, fake_redis):
        """Step 3 succeeds but step 4 token exchange returns 400 → OAuthCredentialError."""
        fake_redis.get.return_value = self._redis_state()

        step3_resp = _make_resp(200, {
            "authorization_code": "auth-code-abc",
            "code_verifier": "verifier-xyz",
        })
        step4_resp = _make_resp(400, {"error": "invalid_grant"})
        http = AsyncMock()
        http.post.side_effect = [step3_resp, step4_resp]

        from app.models.oauth import OAuthCredentialError
        with pytest.raises(OAuthCredentialError, match="token exchange failed"):
            await svc.poll_device_code_flow(
                redis_client=fake_redis,
                collection=AsyncMock(),
                user_id="u1",
                http_client=http,
            )
        fake_redis.delete.assert_called_once()

    async def test_step3_missing_fields_raises(self, fake_redis):
        """Step 3 returns 200 but missing authorization_code → OAuthCredentialError."""
        fake_redis.get.return_value = self._redis_state()

        step3_resp = _make_resp(200, {"some_unexpected_field": "value"})
        http = AsyncMock()
        http.post.return_value = step3_resp

        from app.models.oauth import OAuthCredentialError
        with pytest.raises(OAuthCredentialError, match="missing authorization_code"):
            await svc.poll_device_code_flow(
                redis_client=fake_redis,
                collection=AsyncMock(),
                user_id="u1",
                http_client=http,
            )
        fake_redis.delete.assert_called_once()
