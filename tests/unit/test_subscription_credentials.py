"""Unit tests for tradingagents.llm_adapters.subscription_credentials."""
import dataclasses
import time

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
