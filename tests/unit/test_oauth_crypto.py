"""Unit tests for app.services.oauth_crypto."""
import base64
import os
import secrets

import pytest

from app.services import oauth_crypto as oc


@pytest.fixture
def valid_key(monkeypatch):
    """A valid base64-encoded 32-byte key, exported via env var."""
    raw = secrets.token_bytes(32)
    b64 = base64.b64encode(raw).decode()
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", b64)
    return raw


class TestEncryptDecryptRoundTrip:
    def test_round_trip_preserves_dict(self, valid_key):
        payload = {"access_token": "at-x", "refresh_token": "rt-y"}
        ciphertext, nonce, tag = oc.encrypt_token_payload(payload)
        assert oc.decrypt_token_payload(ciphertext, nonce, tag) == payload

    def test_nonce_is_unique_per_call(self, valid_key):
        payload = {"access_token": "at"}
        _, n1, _ = oc.encrypt_token_payload(payload)
        _, n2, _ = oc.encrypt_token_payload(payload)
        assert n1 != n2

    def test_nonce_length_is_12_bytes(self, valid_key):
        _, nonce, _ = oc.encrypt_token_payload({"access_token": "at"})
        assert len(nonce) == 12

    def test_tag_length_is_16_bytes(self, valid_key):
        _, _, tag = oc.encrypt_token_payload({"access_token": "at"})
        assert len(tag) == 16

    def test_tampered_ciphertext_raises(self, valid_key):
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at"})
        tampered = bytes([ct[0] ^ 1]) + ct[1:]
        with pytest.raises(oc.OAuthCryptoError):
            oc.decrypt_token_payload(tampered, nonce, tag)

    def test_tampered_tag_raises(self, valid_key):
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at"})
        tampered_tag = bytes([tag[0] ^ 1]) + tag[1:]
        with pytest.raises(oc.OAuthCryptoError):
            oc.decrypt_token_payload(ct, nonce, tampered_tag)


class TestStartupValidation:
    def test_missing_key_when_collection_empty_warns_only(self, monkeypatch, caplog):
        monkeypatch.delenv("OAUTH_ENCRYPTION_KEY", raising=False)
        # Caller passes has_existing_credentials=False
        oc.validate_encryption_key_at_startup(has_existing_credentials=False)
        assert any("OAUTH_ENCRYPTION_KEY" in r.message for r in caplog.records)

    def test_missing_key_when_collection_has_records_raises(self, monkeypatch):
        monkeypatch.delenv("OAUTH_ENCRYPTION_KEY", raising=False)
        with pytest.raises(oc.OAuthCryptoError):
            oc.validate_encryption_key_at_startup(has_existing_credentials=True)

    def test_key_wrong_length_raises(self, monkeypatch):
        # 16-byte key (too short)
        monkeypatch.setenv(
            "OAUTH_ENCRYPTION_KEY",
            base64.b64encode(secrets.token_bytes(16)).decode(),
        )
        with pytest.raises(oc.OAuthCryptoError):
            oc.validate_encryption_key_at_startup(has_existing_credentials=False)

    def test_key_not_base64_raises(self, monkeypatch):
        monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", "not-base64!@#$")
        with pytest.raises(oc.OAuthCryptoError):
            oc.validate_encryption_key_at_startup(has_existing_credentials=False)

    def test_valid_key_passes(self, valid_key):
        # Should not raise
        oc.validate_encryption_key_at_startup(has_existing_credentials=True)
