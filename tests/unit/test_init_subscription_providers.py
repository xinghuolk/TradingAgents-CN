"""init_subscription_providers seeds the two OAuth providers idempotently.
First call: 2 created, 0 updated. Second call: 0 created, 2 updated.
Editable fields (display_name, description, is_active, supported_features)
are preserved on subsequent calls; only structural fields (auth_kind,
default_base_url, updated_at) get refreshed."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.config_service import ConfigService


class _FakeCollection:
    def __init__(self):
        self.docs = {}  # keyed by name

    async def find_one(self, query):
        return self.docs.get(query["name"])

    async def insert_one(self, doc):
        self.docs[doc["name"]] = dict(doc)

    async def update_one(self, query, update):
        existing = self.docs.get(query["name"])
        if existing is None:
            return MagicMock(matched_count=0)
        existing.update(update["$set"])
        return MagicMock(matched_count=1)


@pytest.mark.asyncio
async def test_first_call_creates_both_providers(monkeypatch):
    fake = _FakeCollection()
    fake_db = MagicMock()
    fake_db.llm_providers = fake
    service = ConfigService.__new__(ConfigService)
    monkeypatch.setattr(service, "_get_db", AsyncMock(return_value=fake_db))

    result = await service.init_subscription_providers()

    assert sorted(result["created"]) == ["claude_code", "codex"]
    assert result["updated"] == []
    assert fake.docs["codex"]["auth_kind"] == "oauth"
    assert fake.docs["claude_code"]["auth_kind"] == "oauth"
    assert fake.docs["codex"]["default_base_url"] == "https://chatgpt.com/backend-api/codex"
    assert fake.docs["claude_code"]["default_base_url"] == "https://api.anthropic.com"
    assert fake.docs["codex"]["supported_features"] == ["chat"]


@pytest.mark.asyncio
async def test_second_call_only_updates_structural_fields(monkeypatch):
    fake = _FakeCollection()
    fake_db = MagicMock()
    fake_db.llm_providers = fake
    service = ConfigService.__new__(ConfigService)
    monkeypatch.setattr(service, "_get_db", AsyncMock(return_value=fake_db))

    await service.init_subscription_providers()
    # Simulate user edits between calls
    fake.docs["codex"]["display_name"] = "My Custom Codex Name"
    fake.docs["codex"]["description"] = "edited by user"
    fake.docs["codex"]["is_active"] = False
    fake.docs["codex"]["supported_features"] = ["chat", "vision"]

    result = await service.init_subscription_providers()

    assert result["created"] == []
    assert sorted(result["updated"]) == ["claude_code", "codex"]
    # Editable fields preserved
    assert fake.docs["codex"]["display_name"] == "My Custom Codex Name"
    assert fake.docs["codex"]["description"] == "edited by user"
    assert fake.docs["codex"]["is_active"] is False
    assert fake.docs["codex"]["supported_features"] == ["chat", "vision"]
    # Structural fields still refreshed
    assert fake.docs["codex"]["auth_kind"] == "oauth"
    assert fake.docs["codex"]["default_base_url"] == "https://chatgpt.com/backend-api/codex"
