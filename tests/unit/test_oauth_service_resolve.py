"""Unit tests for oauth_service.resolve / store / delete (no flow yet)."""
import base64
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import oauth_crypto as oc
from app.services import oauth_service as svc
from app.models.oauth import OAuthCredentialError


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    """Provide a valid encryption key for the whole module."""
    raw = secrets.token_bytes(32)
    monkeypatch.setenv("OAUTH_ENCRYPTION_KEY", base64.b64encode(raw).decode())


@pytest.fixture
def fake_collection():
    """Async mock for the Motor collection."""
    coll = AsyncMock()
    return coll


class TestStoreCredentials:
    async def test_store_upserts_with_encryption(self, fake_collection):
        await svc.store_credentials(
            collection=fake_collection,
            user_id="u1",
            provider="claude_code",
            access_token="at-1",
            refresh_token="rt-1",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        fake_collection.update_one.assert_called_once()
        call_kwargs = fake_collection.update_one.call_args
        # Filter targets (user_id, provider)
        filter_doc = call_kwargs.args[0]
        assert filter_doc == {"user_id": "u1", "provider": "claude_code"}
        # Update doc has ciphertext, nonce, tag
        update_doc = call_kwargs.args[1]
        set_doc = update_doc["$set"]
        assert "ciphertext" in set_doc
        assert len(set_doc["nonce"]) == 12
        assert len(set_doc["tag"]) == 16
        assert set_doc["refresh_token_present"] is True
        # upsert=True kwarg
        assert call_kwargs.kwargs.get("upsert") is True


class TestResolve:
    async def test_resolve_no_doc_raises(self, fake_collection):
        fake_collection.find_one.return_value = None
        with pytest.raises(OAuthCredentialError) as exc:
            await svc.resolve(fake_collection, "u1", "claude_code")
        assert "not bound" in str(exc.value).lower() or "未绑定" in str(exc.value)

    async def test_resolve_fresh_token_returned_directly(self, fake_collection, monkeypatch):
        # Construct a doc encrypted with the same key
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at-good", "refresh_token": "rt-1"})
        fake_collection.find_one.return_value = {
            "user_id": "u1",
            "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "refresh_token_present": True,
            "last_used_at": datetime.now(timezone.utc),
            "last_refresh_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
        token = await svc.resolve(fake_collection, "u1", "claude_code")
        assert token == "at-good"
        # Should not call refresh_claude_code
        # Should update last_used_at
        fake_collection.update_one.assert_called_once()
        update_doc = fake_collection.update_one.call_args.args[1]
        assert "last_used_at" in update_doc.get("$set", {})

    async def test_resolve_expiring_token_refreshes(self, fake_collection, monkeypatch):
        # Token that expires in 30s — within 60s skew
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at-old", "refresh_token": "rt-1"})
        fake_collection.find_one.return_value = {
            "user_id": "u1",
            "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime.now(timezone.utc) + timedelta(seconds=30),
            "refresh_token_present": True,
            "last_used_at": datetime.now(timezone.utc),
            "last_refresh_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }

        def fake_refresh(rt, **_):
            return ("at-new", "rt-2", int(datetime.now(timezone.utc).timestamp() * 1000) + 3600_000)
        monkeypatch.setattr(
            "tradingagents.llm_adapters.subscription_credentials.refresh_claude_code",
            fake_refresh,
        )
        token = await svc.resolve(fake_collection, "u1", "claude_code")
        assert token == "at-new"

    async def test_resolve_expiring_no_refresh_token_raises(self, fake_collection):
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at-old", "refresh_token": ""})
        fake_collection.find_one.return_value = {
            "user_id": "u1",
            "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime.now(timezone.utc) - timedelta(minutes=5),
            "refresh_token_present": False,
            "last_used_at": datetime.now(timezone.utc),
            "last_refresh_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
        with pytest.raises(OAuthCredentialError) as exc:
            await svc.resolve(fake_collection, "u1", "claude_code")
        assert "expired" in str(exc.value).lower() or "expired" in str(exc.value)

    async def test_resolve_force_refresh(self, fake_collection, monkeypatch):
        ct, nonce, tag = oc.encrypt_token_payload({"access_token": "at-old", "refresh_token": "rt-1"})
        fake_collection.find_one.return_value = {
            "user_id": "u1",
            "provider": "claude_code",
            "ciphertext": ct, "nonce": nonce, "tag": tag,
            "access_token_expires_at": datetime.now(timezone.utc) + timedelta(hours=1),  # not expiring
            "refresh_token_present": True,
            "last_used_at": datetime.now(timezone.utc),
            "last_refresh_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
        monkeypatch.setattr(
            "tradingagents.llm_adapters.subscription_credentials.refresh_claude_code",
            lambda rt, **_: ("at-forced", "rt-2", int(datetime.now(timezone.utc).timestamp() * 1000) + 3600_000),
        )
        token = await svc.resolve(fake_collection, "u1", "claude_code", force_refresh=True)
        assert token == "at-forced"


class TestDelete:
    async def test_delete_calls_delete_one(self, fake_collection):
        await svc.delete_credentials(fake_collection, "u1", "claude_code")
        fake_collection.delete_one.assert_called_once_with(
            {"user_id": "u1", "provider": "claude_code"}
        )
