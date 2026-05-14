"""Unit tests for oauth_service PKCE flow (Anthropic Claude Code)."""
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
    r = AsyncMock()
    return r


class TestStartPkceFlow:
    async def test_returns_authorize_url_and_state(self, fake_redis):
        result = await svc.start_pkce_flow(
            redis_client=fake_redis,
            user_id="u1",
            redirect_uri="https://my.host/api/oauth/callback/claude_code",
        )
        assert "authorize_url" in result
        assert "state" in result
        # state is a base64url string at least 32 chars
        assert len(result["state"]) >= 32
        # authorize_url is the Anthropic OAuth endpoint with the right params
        assert "claude.ai/oauth/authorize" in result["authorize_url"]
        assert f"state={result['state']}" in result["authorize_url"]
        assert "code_challenge=" in result["authorize_url"]
        assert "code_challenge_method=S256" in result["authorize_url"]
        assert "client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e" in result["authorize_url"]
        # redirect_uri appears URL-encoded
        import urllib.parse
        assert urllib.parse.quote("https://my.host/api/oauth/callback/claude_code",
                                   safe="") in result["authorize_url"]

    async def test_stores_state_in_redis_with_ttl(self, fake_redis):
        await svc.start_pkce_flow(
            redis_client=fake_redis,
            user_id="u1",
            redirect_uri="https://my.host/api/oauth/callback/claude_code",
        )
        fake_redis.setex.assert_called_once()
        # Args: (key, ttl, value)
        key, ttl, value = fake_redis.setex.call_args.args
        assert key.startswith("oauth:state:claude_code:")
        assert ttl == 600
        # value should be JSON containing user_id and code_verifier
        import json
        parsed = json.loads(value)
        assert parsed["user_id"] == "u1"
        assert len(parsed["code_verifier"]) >= 43

    async def test_each_call_generates_distinct_state(self, fake_redis):
        r1 = await svc.start_pkce_flow(fake_redis, user_id="u", redirect_uri="https://h")
        r2 = await svc.start_pkce_flow(fake_redis, user_id="u", redirect_uri="https://h")
        assert r1["state"] != r2["state"]
