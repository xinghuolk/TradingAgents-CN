"""Unit tests for app.models.oauth."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import oauth as m


class TestOAuthCredentialDoc:
    def test_construct_minimal(self):
        doc = m.OAuthCredentialDoc(
            user_id="u1",
            provider="claude_code",
            ciphertext=b"\x01\x02",
            nonce=b"\x00" * 12,
            tag=b"\x00" * 16,
            access_token_expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            refresh_token_present=True,
            created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            last_refresh_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            last_used_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        )
        assert doc.provider == "claude_code"
        assert doc.refresh_token_present is True

    def test_provider_must_be_known(self):
        with pytest.raises(ValidationError):
            m.OAuthCredentialDoc(
                user_id="u1",
                provider="anthropic",  # wrong name (should be claude_code)
                ciphertext=b"x", nonce=b"\x00" * 12, tag=b"\x00" * 16,
                access_token_expires_at=datetime.now(timezone.utc),
                refresh_token_present=False,
                created_at=datetime.now(timezone.utc),
                last_refresh_at=datetime.now(timezone.utc),
                last_used_at=datetime.now(timezone.utc),
            )


class TestResponseModels:
    def test_authorize_anthropic_response(self):
        resp = m.AuthorizeClaudeCodeResponse(
            authorize_url="https://claude.ai/oauth/authorize?state=x",
            state="x",
        )
        assert resp.expires_in == 600

    def test_authorize_codex_response(self):
        resp = m.AuthorizeCodexResponse(
            user_code="ABCD-EFGH",
            verification_uri="https://chatgpt.com/device",
            expires_in=300,
            interval=5,
        )
        assert resp.user_code == "ABCD-EFGH"

    def test_poll_codex_response_status_enum(self):
        for s in ("pending", "bound", "expired", "denied"):
            r = m.PollCodexResponse(status=s)
            assert r.status == s
        with pytest.raises(ValidationError):
            m.PollCodexResponse(status="nonsense")

    def test_status_response_unbound(self):
        r = m.OAuthStatusResponse(bound=False, provider="claude_code")
        assert r.expires_at is None
        assert r.last_refresh_at is None

    def test_status_response_bound(self):
        r = m.OAuthStatusResponse(
            bound=True,
            provider="codex",
            expires_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            last_refresh_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
        )
        assert r.bound is True
        assert r.provider == "codex"
