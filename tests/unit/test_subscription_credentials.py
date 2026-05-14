"""Unit tests for tradingagents.llm_adapters.subscription_credentials."""
import dataclasses
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tradingagents.llm_adapters import subscription_credentials as sc


def _make_cred(expires_at_ms: int) -> sc.SubscriptionCredential:
    return sc.SubscriptionCredential(
        access_token="at-test",
        refresh_token="rt-test",
        expires_at_ms=expires_at_ms,
        provider="claude_code",
        source="test",
    )


class TestSubscriptionCredential:
    def test_is_frozen(self):
        cred = _make_cred(0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cred.access_token = "mutated"  # type: ignore[misc]

    def test_zero_expires_at_means_never_expires(self):
        """Some managed keys have no expiry; expires_at_ms=0 must be treated as valid."""
        cred = _make_cred(0)
        assert sc.is_expiring(cred) is False

    def test_already_expired_is_expiring(self):
        cred = _make_cred(int(time.time() * 1000) - 1000)  # 1s ago
        assert sc.is_expiring(cred) is True

    def test_far_future_not_expiring(self):
        cred = _make_cred(int(time.time() * 1000) + 3600_000)  # 1h from now
        assert sc.is_expiring(cred) is False

    def test_within_skew_window_is_expiring(self):
        """Token that expires in 30s should be flagged with default 60s skew."""
        cred = _make_cred(int(time.time() * 1000) + 30_000)
        assert sc.is_expiring(cred, skew_seconds=60) is True

    def test_custom_skew(self):
        cred = _make_cred(int(time.time() * 1000) + 30_000)
        assert sc.is_expiring(cred, skew_seconds=10) is False

    def test_expired_with_zero_skew(self):
        """Pin the comparison direction: expired token is always expiring, regardless of skew."""
        cred = _make_cred(int(time.time() * 1000) - 1000)  # 1s ago
        assert sc.is_expiring(cred, skew_seconds=0) is True

    def test_repr_redacts_tokens(self):
        """Tokens must not appear in repr — accidental logging would leak them."""
        cred = _make_cred(0)
        r = repr(cred)
        assert "at-test" not in r
        assert "rt-test" not in r
        # But the non-secret diagnostic fields should still be present
        assert "claude_code" in r
        assert "test" in r  # the source value


class TestReadClaudeCodeFromFile:
    def test_file_missing_returns_none(self, tmp_path):
        with patch.object(sc, "_claude_code_credentials_path", return_value=tmp_path / "nope.json"):
            assert sc.read_claude_code_from_file() is None

    def test_valid_file_returns_credential(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text(json.dumps({
            "claudeAiOauth": {
                "accessToken": "at-real",
                "refreshToken": "rt-real",
                "expiresAt": 1_700_000_000_000,
            }
        }))
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            cred = sc.read_claude_code_from_file()
        assert cred is not None
        assert cred.access_token == "at-real"
        assert cred.refresh_token == "rt-real"
        assert cred.expires_at_ms == 1_700_000_000_000
        assert cred.provider == "claude_code"
        assert cred.source == "claude_code_file"

    def test_missing_access_token_returns_none(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text(json.dumps({"claudeAiOauth": {"refreshToken": "rt"}}))
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            assert sc.read_claude_code_from_file() is None

    def test_malformed_json_returns_none(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text("not json {")
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            assert sc.read_claude_code_from_file() is None

    def test_missing_claudeAiOauth_key_returns_none(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text(json.dumps({"someOtherKey": {}}))
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            assert sc.read_claude_code_from_file() is None

    def test_no_expires_at_defaults_to_zero(self, tmp_path):
        creds_path = tmp_path / ".credentials.json"
        creds_path.write_text(json.dumps({
            "claudeAiOauth": {"accessToken": "at-noexp"}
        }))
        with patch.object(sc, "_claude_code_credentials_path", return_value=creds_path):
            cred = sc.read_claude_code_from_file()
        assert cred is not None
        assert cred.expires_at_ms == 0
        assert cred.refresh_token is None
