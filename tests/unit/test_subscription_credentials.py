"""Unit tests for tradingagents.llm_adapters.subscription_credentials."""
import dataclasses
import json
import time
import urllib.error
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

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


class TestReadClaudeCodeFromKeychain:
    def test_non_darwin_returns_none(self):
        with patch.object(sc, "_platform_system", return_value="Linux"):
            assert sc.read_claude_code_from_keychain() is None

    def test_security_command_missing_returns_none(self):
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", side_effect=FileNotFoundError):
            assert sc.read_claude_code_from_keychain() is None

    def test_security_command_nonzero_exit_returns_none(self):
        completed = MagicMock(returncode=44, stdout="", stderr="")
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", return_value=completed):
            assert sc.read_claude_code_from_keychain() is None

    def test_security_command_timeout_returns_none(self):
        import subprocess
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="security", timeout=5)):
            assert sc.read_claude_code_from_keychain() is None

    def test_security_command_os_error_returns_none(self):
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", side_effect=OSError("spawn failed")):
            assert sc.read_claude_code_from_keychain() is None

    def test_valid_keychain_payload_returns_credential(self):
        payload = json.dumps({
            "claudeAiOauth": {
                "accessToken": "at-kc",
                "refreshToken": "rt-kc",
                "expiresAt": 1_800_000_000_000,
            }
        })
        completed = MagicMock(returncode=0, stdout=payload + "\n", stderr="")
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", return_value=completed):
            cred = sc.read_claude_code_from_keychain()
        assert cred is not None
        assert cred.access_token == "at-kc"
        assert cred.refresh_token == "rt-kc"
        assert cred.expires_at_ms == 1_800_000_000_000
        assert cred.provider == "claude_code"
        assert cred.source == "macos_keychain"

    def test_keychain_returns_garbage_returns_none(self):
        completed = MagicMock(returncode=0, stdout="not json", stderr="")
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", return_value=completed):
            assert sc.read_claude_code_from_keychain() is None

    def test_keychain_missing_claudeAiOauth_key_returns_none(self):
        completed = MagicMock(returncode=0, stdout=json.dumps({"otherKey": {}}), stderr="")
        with patch.object(sc, "_platform_system", return_value="Darwin"), \
             patch("subprocess.run", return_value=completed):
            assert sc.read_claude_code_from_keychain() is None


class TestReadCodexFromFile:
    def test_file_missing_returns_none(self, tmp_path):
        with patch.object(sc, "_codex_credentials_path", return_value=tmp_path / "nope.json"):
            assert sc.read_codex_from_file() is None

    def test_valid_file_returns_credential(self, tmp_path):
        # Codex CLI's auth.json shape (best-effort — confirmed against codex CLI 0.x).
        # Structure may evolve; the loader must tolerate unknown fields.
        from datetime import datetime, timezone
        expires_iso = "2026-12-31T00:00:00Z"
        expected_ms = int(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp() * 1000)

        creds_path = tmp_path / "auth.json"
        creds_path.write_text(json.dumps({
            "OPENAI_API_KEY": None,
            "tokens": {
                "access_token": "at-cx",
                "refresh_token": "rt-cx",
                "expires_at": expires_iso,
            },
            "last_refresh": "2026-05-13T10:00:00Z",
        }))
        with patch.object(sc, "_codex_credentials_path", return_value=creds_path):
            cred = sc.read_codex_from_file()
        assert cred is not None
        assert cred.access_token == "at-cx"
        assert cred.refresh_token == "rt-cx"
        assert cred.provider == "codex"
        assert cred.source == "codex_file"
        assert cred.expires_at_ms == expected_ms

    def test_missing_tokens_section_returns_none(self, tmp_path):
        creds_path = tmp_path / "auth.json"
        creds_path.write_text(json.dumps({"OPENAI_API_KEY": "sk-..."}))
        with patch.object(sc, "_codex_credentials_path", return_value=creds_path):
            assert sc.read_codex_from_file() is None

    def test_invalid_expires_at_falls_back_to_zero(self, tmp_path):
        creds_path = tmp_path / "auth.json"
        creds_path.write_text(json.dumps({
            "tokens": {"access_token": "at", "refresh_token": "rt", "expires_at": "not-a-date"}
        }))
        with patch.object(sc, "_codex_credentials_path", return_value=creds_path):
            cred = sc.read_codex_from_file()
        assert cred is not None
        assert cred.expires_at_ms == 0  # unparseable → treated as "no expiry tracked"


class TestRefreshClaudeCode:
    def test_empty_refresh_token_raises(self):
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_claude_code("")

    def test_success_returns_new_tokens(self, monkeypatch):
        captured = {}
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["data"] = req.data
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expires_in": 3600,
            }).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)

        before_ms = int(time.time() * 1000)
        new_at, new_rt, new_exp = sc.refresh_claude_code("old-rt")
        after_ms = int(time.time() * 1000)

        assert new_at == "new-at"
        assert new_rt == "new-rt"
        # expires_at_ms should be roughly now + 3600s
        assert before_ms + 3_600_000 <= new_exp <= after_ms + 3_600_000 + 100
        # POST hit the primary endpoint with x-www-form-urlencoded body
        assert captured["url"] == "https://platform.claude.com/v1/oauth/token"
        assert b"grant_type=refresh_token" in captured["data"]
        assert b"refresh_token=old-rt" in captured["data"]
        # client_id must be the public Claude Code client id
        assert b"client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e" in captured["data"]

    def test_primary_endpoint_fails_falls_back_to_console(self, monkeypatch):
        call_count = {"n": 0}
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise urllib.error.URLError("primary down")
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "access_token": "fallback-at",
                "refresh_token": "old-rt",
                "expires_in": 1800,
            }).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)

        new_at, _, _ = sc.refresh_claude_code("old-rt")
        assert new_at == "fallback-at"
        assert call_count["n"] == 2

    def test_all_endpoints_fail_raises(self, monkeypatch):
        @contextmanager
        def always_fail(req, timeout=None):  # noqa: ARG001
            raise urllib.error.URLError("network out")
            yield  # noqa: unreachable
        monkeypatch.setattr(sc.urllib.request, "urlopen", always_fail)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_claude_code("any")

    def test_response_missing_access_token_raises(self, monkeypatch):
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            resp = MagicMock()
            resp.read.return_value = json.dumps({"refresh_token": "x"}).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_claude_code("any")

    def test_non_dict_response_raises_typed_error(self, monkeypatch):
        """If server returns valid JSON that is not an object, raise SubscriptionCredentialError (not AttributeError)."""
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            resp = MagicMock()
            resp.read.return_value = json.dumps(["not", "a", "dict"]).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_claude_code("any")

    def test_non_numeric_expires_in_falls_back_to_default(self, monkeypatch):
        """If server returns garbage for expires_in, default to 3600s instead of crashing."""
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": "forever",  # non-numeric
            }).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
        before_ms = int(time.time() * 1000)
        _, _, exp_ms = sc.refresh_claude_code("any")
        # Should fall back to 3600s default
        assert before_ms + 3_600_000 <= exp_ms <= before_ms + 3_600_000 + 1000


class TestRefreshCodex:
    def test_empty_refresh_token_raises(self):
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_codex("")

    def test_success_returns_new_tokens(self, monkeypatch):
        captured = {}
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            captured["data"] = req.data
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "access_token": "cx-new",
                "refresh_token": "cx-new-rt",
                "expires_in": 1800,
            }).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)

        new_at, new_rt, new_exp = sc.refresh_codex("cx-old-rt")
        assert new_at == "cx-new"
        assert new_rt == "cx-new-rt"
        assert captured["url"] == "https://auth.openai.com/oauth/token"
        assert b"grant_type=refresh_token" in captured["data"]
        assert b"client_id=app_EMoamEEZ73f0CkXaXp7hrann" in captured["data"]

    def test_failure_raises(self, monkeypatch):
        @contextmanager
        def fail(req, timeout=None):  # noqa: ARG001
            raise urllib.error.URLError("offline")
            yield
        monkeypatch.setattr(sc.urllib.request, "urlopen", fail)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_codex("any")

    def test_non_dict_response_raises_typed_error(self, monkeypatch):
        """If server returns valid JSON that is not an object, raise SubscriptionCredentialError (not AttributeError)."""
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            resp = MagicMock()
            resp.read.return_value = json.dumps(["not", "a", "dict"]).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_codex("any")

    def test_non_numeric_expires_in_falls_back_to_default(self, monkeypatch):
        """If server returns garbage for expires_in, default to 3600s instead of crashing."""
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            resp = MagicMock()
            resp.read.return_value = json.dumps({
                "access_token": "cx-at",
                "refresh_token": "cx-rt",
                "expires_in": "forever",  # non-numeric
            }).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
        before_ms = int(time.time() * 1000)
        _, _, exp_ms = sc.refresh_codex("any")
        # Should fall back to 3600s default
        assert before_ms + 3_600_000 <= exp_ms <= before_ms + 3_600_000 + 1000

    def test_response_missing_access_token_raises(self, monkeypatch):
        """If the server returns a dict but omits access_token, raise SubscriptionCredentialError."""
        @contextmanager
        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            resp = MagicMock()
            resp.read.return_value = json.dumps({"refresh_token": "x"}).encode()
            yield resp
        monkeypatch.setattr(sc.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(sc.SubscriptionCredentialError):
            sc.refresh_codex("any")


class TestResolve:
    def test_no_credentials_raises(self, monkeypatch):
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: None)
        with pytest.raises(sc.SubscriptionCredentialError) as exc:
            sc.resolve("claude_code")
        assert "claude login" in str(exc.value).lower()

    def test_keychain_takes_precedence(self, monkeypatch):
        base = _make_cred(int(time.time() * 1000) + 3600_000)
        kc_cred = dataclasses.replace(base, source="macos_keychain")
        file_cred = dataclasses.replace(base, source="claude_code_file")
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: kc_cred)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: file_cred)
        result = sc.resolve("claude_code")
        assert result.source == "macos_keychain"

    def test_falls_back_to_file_when_keychain_empty(self, monkeypatch):
        file_cred = _make_cred(int(time.time() * 1000) + 3600_000)
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: file_cred)
        result = sc.resolve("claude_code")
        assert result is file_cred

    def test_refreshes_when_expiring(self, monkeypatch, tmp_path):
        expiring = sc.SubscriptionCredential(
            access_token="stale-at",
            refresh_token="rt-1",
            expires_at_ms=int(time.time() * 1000) + 10_000,  # expires in 10s, within 60s skew
            provider="claude_code",
            source="claude_code_file",
        )
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: expiring)

        called = {}
        def fake_refresh(rt, **_):
            called["rt"] = rt
            return ("fresh-at", "rt-2", int(time.time() * 1000) + 3600_000)
        monkeypatch.setattr(sc, "refresh_claude_code", fake_refresh)
        # Persist write target to a tmp file
        monkeypatch.setattr(sc, "_claude_code_credentials_path", lambda: tmp_path / "creds.json")

        result = sc.resolve("claude_code")
        assert result.access_token == "fresh-at"
        assert result.refresh_token == "rt-2"
        assert called["rt"] == "rt-1"
        # Verify write-back happened with the rotated tokens
        written = json.loads((tmp_path / "creds.json").read_text())
        assert written["claudeAiOauth"]["accessToken"] == "fresh-at"
        assert written["claudeAiOauth"]["refreshToken"] == "rt-2"

    def test_no_refresh_token_and_expiring_raises(self, monkeypatch):
        expiring_no_rt = sc.SubscriptionCredential(
            access_token="x", refresh_token=None,
            expires_at_ms=1, provider="claude_code", source="test",
        )
        monkeypatch.setattr(sc, "read_claude_code_from_keychain", lambda: None)
        monkeypatch.setattr(sc, "read_claude_code_from_file", lambda: expiring_no_rt)
        with pytest.raises(sc.SubscriptionCredentialError) as exc:
            sc.resolve("claude_code")
        assert "expired" in str(exc.value).lower() or "refresh" in str(exc.value).lower()

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError):
            sc.resolve("nonsense")  # type: ignore[arg-type]

    def test_codex_path(self, monkeypatch):
        fresh = sc.SubscriptionCredential(
            access_token="cx-at", refresh_token="cx-rt",
            expires_at_ms=int(time.time() * 1000) + 3600_000,
            provider="codex", source="codex_file",
        )
        monkeypatch.setattr(sc, "read_codex_from_file", lambda: fresh)
        result = sc.resolve("codex")
        assert result is fresh
