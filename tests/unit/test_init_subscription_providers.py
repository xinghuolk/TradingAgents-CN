"""init_subscription_providers seeds the two OAuth providers idempotently.
First call: 2 created, 0 updated. Second call: 0 created, 2 updated.
Editable fields (display_name, description, is_active, supported_features)
are preserved on subsequent calls; only structural fields (auth_kind,
default_base_url, updated_at) get refreshed."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.config_service import ConfigService


class _FakeCollection:
    """Simulates the subset of Motor collection upsert semantics the seed uses:
    update_one(filter, {$set, $setOnInsert}, upsert=True).
    - existing doc → apply only $set (preserve everything else)
    - missing doc + upsert → insert from filter + $setOnInsert + $set,
      and return a result whose upserted_id is non-None (mirrors pymongo).
    """

    def __init__(self):
        self.docs = {}  # keyed by name

    async def update_one(self, query, update, upsert=False):
        name = query["name"]
        existing = self.docs.get(name)
        if existing is not None:
            existing.update(update.get("$set", {}))
            return MagicMock(matched_count=1, upserted_id=None)
        if not upsert:
            return MagicMock(matched_count=0, upserted_id=None)
        doc = {**query}
        doc.update(update.get("$setOnInsert", {}))
        doc.update(update.get("$set", {}))
        self.docs[name] = doc
        return MagicMock(matched_count=0, upserted_id=f"fake_id_{name}")


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
