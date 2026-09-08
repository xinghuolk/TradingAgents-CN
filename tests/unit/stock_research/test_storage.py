from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    Entry,
    EntryQuery,
    ResearchSecurityId,
    Workspace,
    WorkspaceQuery,
)
from app.services.stock_research.storage import StockResearchRepository
from tests.unit.stock_research.fakes import FakeDatabase


NOW = datetime(2026, 9, 8, 9, 30, tzinfo=UTC)


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


def make_note(*, user_id: str, entry_id: str, updated_at: datetime = NOW) -> Entry:
    security = ResearchSecurityId.parse("CN", "600519")
    return Entry(
        id=entry_id,
        user_id=user_id,
        entry_type="note",
        scope="stock",
        security_id=str(security),
        security_ids=(str(security),),
        title="标题",
        body="正文",
        created_at=updated_at,
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_indexes_and_revision_numbers_are_user_scoped(fake_db):
    repo = StockResearchRepository(fake_db)
    await repo.ensure_indexes()
    first = await repo.append_revision(
        "u1", "workspace", "A:600519", {"body": "v1"}, "manual"
    )
    second = await repo.append_revision(
        "u1", "workspace", "A:600519", {"body": "v2"}, "manual"
    )
    other_user = await repo.append_revision(
        "u2", "workspace", "A:600519", {"body": "other"}, "manual"
    )

    assert (first.revision, second.revision, other_user.revision) == (1, 2, 1)
    assert (
        "user_id",
        "security_id",
    ) in fake_db["stock_research_workspaces"].unique_keys
    assert (
        "user_id",
        "target_type",
        "target_id",
        "revision",
    ) in fake_db["stock_research_revisions"].unique_keys
    assert (
        "user_id",
        "trade_link_keys",
    ) in fake_db["stock_research_entries"].unique_keys


@pytest.mark.asyncio
async def test_entry_reads_never_cross_users_and_delete_is_soft(fake_db):
    repo = StockResearchRepository(fake_db)
    await repo.insert_entry(make_note(user_id="u1", entry_id="e1"))

    assert await repo.get_entry("u2", "e1") is None
    await repo.soft_delete_entry("u1", "e1", now=NOW)

    assert await repo.get_entry("u1", "e1") is None
    assert (await repo.list_trash("u1", page=1, page_size=20)).items[0].id == "e1"
    assert (await repo.list_trash("u2", page=1, page_size=20)).items == ()


@pytest.mark.asyncio
async def test_workspace_upsert_and_lists_are_owned_filtered_and_paginated(fake_db):
    repo = StockResearchRepository(fake_db)
    first = Workspace(
        user_id="u1",
        security_id="A:600519",
        market="A",
        code="600519",
        name="贵州茅台",
        body="白酒龙头",
        created_at=NOW,
        updated_at=NOW,
    )
    second = Workspace(
        user_id="u1",
        security_id="US:AAPL",
        market="US",
        code="AAPL",
        name="Apple",
        created_at=NOW,
        updated_at=NOW,
    )
    await repo.upsert_workspace(first)
    await repo.upsert_workspace(second)
    await repo.upsert_workspace(replace(first, body="更新后的论点"))

    page = await repo.list_workspaces(
        "u1", WorkspaceQuery(market="A", query="茅台", page=1, page_size=1)
    )

    assert await repo.get_workspace("u2", first.security_id) is None
    assert (page.total, page.items[0].body) == (1, "更新后的论点")


@pytest.mark.asyncio
async def test_entry_list_filters_security_type_and_archived_state(fake_db):
    repo = StockResearchRepository(fake_db)
    note = make_note(user_id="u1", entry_id="note-1")
    archived = replace(
        note,
        id="note-2",
        status="archived",
        archived_at=NOW,
        title="归档标题",
    )
    await repo.insert_entry(note)
    await repo.insert_entry(archived)
    await repo.insert_entry(make_note(user_id="u2", entry_id="note-3"))

    active = await repo.list_entries(
        "u1",
        EntryQuery(
            security_id="A:600519",
            entry_type="note",
            page=1,
            page_size=20,
        ),
    )
    archived_page = await repo.list_entries(
        "u1", EntryQuery(status="archived", query="归档", page=1, page_size=20)
    )

    assert [item.id for item in active.items] == ["note-1"]
    assert [item.id for item in archived_page.items] == ["note-2"]


@pytest.mark.asyncio
async def test_restore_then_permanent_delete_obeys_trash_boundary(fake_db):
    repo = StockResearchRepository(fake_db)
    await repo.insert_entry(make_note(user_id="u1", entry_id="e1"))
    await repo.soft_delete_entry("u1", "e1", now=NOW)

    restored = await repo.restore_entry("u1", "e1")
    assert restored.deleted_at is None

    await repo.permanently_delete_entry("u1", "e1")
    assert await repo.get_entry("u1", "e1") == restored

    await repo.soft_delete_entry("u1", "e1", now=NOW)
    await repo.permanently_delete_entry("u1", "e1")
    assert await repo.get_entry("u1", "e1", include_deleted=True) is None


@pytest.mark.asyncio
async def test_replace_entry_and_revision_listing_remain_user_scoped(fake_db):
    repo = StockResearchRepository(fake_db)
    entry = make_note(user_id="u1", entry_id="e1")
    await repo.insert_entry(entry)
    updated = replace(entry, body="更新")

    assert await repo.replace_entry(updated) == updated
    await repo.append_revision("u1", "entry", "e1", {"body": "v1"}, "manual")
    await repo.append_revision("u1", "entry", "e1", {"body": "v2"}, "manual")

    revisions = await repo.list_revisions("u1", "entry", "e1")
    assert [item.snapshot["body"] for item in revisions] == ["v1", "v2"]
    assert await repo.list_revisions("u2", "entry", "e1") == []


@pytest.mark.asyncio
async def test_revision_lookup_is_user_scoped(fake_db):
    repo = StockResearchRepository(fake_db)
    revision = await repo.append_revision(
        "u1", "workspace", "A:600519", {"body": "v1"}, "manual"
    )

    assert await repo.get_revision("u1", revision.id) == revision
    assert await repo.get_revision("u2", revision.id) is None


@pytest.mark.parametrize(
    ("raw_security_id", "canonical_security_id"),
    [("CN:600519", "A:600519"), ("us:aapl", "US:AAPL")],
)
@pytest.mark.asyncio
async def test_entry_persistence_canonicalizes_security_identifiers(
    fake_db, raw_security_id, canonical_security_id
):
    repo = StockResearchRepository(fake_db)
    entry = Entry(
        id=f"entry-{canonical_security_id}",
        user_id="u1",
        entry_type="note",
        scope="stock",
        security_id=raw_security_id,
        security_ids=(raw_security_id,),
        title="标题",
        body="正文",
        created_at=NOW,
        updated_at=NOW,
    )

    stored = await repo.insert_entry(entry)
    document = fake_db["stock_research_entries"].documents[0]

    assert stored.security_id == canonical_security_id
    assert stored.security_ids == (canonical_security_id,)
    assert document["security_id"] == canonical_security_id
    assert document["security_ids"] == [canonical_security_id]


@pytest.mark.parametrize(
    ("security_id", "market", "code", "canonical"),
    [
        ("CN:600519", "CN", "600519", ("A:600519", "A", "600519")),
        ("us:aapl", "us", "aapl", ("US:AAPL", "US", "AAPL")),
    ],
)
@pytest.mark.asyncio
async def test_workspace_persistence_canonicalizes_one_security_identity(
    fake_db, security_id, market, code, canonical
):
    repo = StockResearchRepository(fake_db)
    workspace = Workspace(
        user_id="u1",
        security_id=security_id,
        market=market,
        code=code,
        name="测试证券",
        created_at=NOW,
        updated_at=NOW,
    )

    stored = await repo.upsert_workspace(workspace)
    repeated = await repo.upsert_workspace(workspace)
    document = fake_db["stock_research_workspaces"].documents[0]

    assert (stored.security_id, stored.market, stored.code) == canonical
    assert (repeated.security_id, repeated.market, repeated.code) == canonical
    assert (document["security_id"], document["market"], document["code"]) == canonical
    assert len(fake_db["stock_research_workspaces"].documents) == 1


@pytest.mark.asyncio
async def test_workspace_rejects_inconsistent_security_fields_before_persistence(
    fake_db,
):
    repo = StockResearchRepository(fake_db)

    with pytest.raises(ResearchError, match="inconsistent"):
        await repo.upsert_workspace(
            Workspace(
                user_id="u1",
                security_id="A:600519",
                market="US",
                code="AAPL",
                name="冲突记录",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    assert fake_db["stock_research_workspaces"].documents == []


@pytest.mark.parametrize(
    ("security_id", "market", "code", "lookup", "canonical"),
    [
        ("CN:600519", "CN", "600519", "CN:600519", "A:600519"),
        ("us:aapl", "us", "aapl", "us:aapl", "US:AAPL"),
    ],
)
@pytest.mark.asyncio
async def test_get_workspace_accepts_security_aliases(
    fake_db, security_id, market, code, lookup, canonical
):
    repo = StockResearchRepository(fake_db)
    await repo.upsert_workspace(
        Workspace(
            user_id="u1",
            security_id=security_id,
            market=market,
            code=code,
            name="测试证券",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    found = await repo.get_workspace("u1", lookup)

    assert found is not None
    assert found.security_id == canonical


@pytest.mark.parametrize(
    ("stored_security_id", "query_security_id", "canonical"),
    [
        ("CN:600519", "CN:600519", "A:600519"),
        ("us:aapl", "us:aapl", "US:AAPL"),
    ],
)
@pytest.mark.asyncio
async def test_list_entries_accepts_security_aliases(
    fake_db, stored_security_id, query_security_id, canonical
):
    repo = StockResearchRepository(fake_db)
    entry = Entry(
        id=f"entry-{canonical}",
        user_id="u1",
        entry_type="note",
        scope="stock",
        security_id=stored_security_id,
        security_ids=(stored_security_id,),
        title="标题",
        body="正文",
        created_at=NOW,
        updated_at=NOW,
    )
    await repo.insert_entry(entry)

    page = await repo.list_entries(
        "u1", EntryQuery(security_id=query_security_id, page=1, page_size=20)
    )

    assert page.total == 1
    assert page.items[0].security_id == canonical


@pytest.mark.parametrize(
    ("market_alias", "canonical_security_id"),
    [
        ("CN", "A:600519"),
        ("cn", "A:600519"),
        ("A", "A:600519"),
        ("a", "A:600519"),
        ("US", "US:AAPL"),
        ("us", "US:AAPL"),
    ],
)
@pytest.mark.asyncio
async def test_list_workspaces_accepts_market_aliases_without_crossing_users(
    fake_db, market_alias, canonical_security_id
):
    repo = StockResearchRepository(fake_db)
    await repo.upsert_workspace(
        Workspace(
            user_id="u1",
            security_id="A:600519",
            market="A",
            code="600519",
            name="贵州茅台",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await repo.upsert_workspace(
        Workspace(
            user_id="u1",
            security_id="US:AAPL",
            market="US",
            code="AAPL",
            name="Apple",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await repo.upsert_workspace(
        Workspace(
            user_id="u2",
            security_id=canonical_security_id,
            market=canonical_security_id.split(":", maxsplit=1)[0],
            code=canonical_security_id.split(":", maxsplit=1)[1],
            name="其他用户",
            created_at=NOW,
            updated_at=NOW,
        )
    )

    page = await repo.list_workspaces(
        "u1", WorkspaceQuery(market=market_alias, page=1, page_size=20)
    )

    assert page.total == 1
    assert [workspace.security_id for workspace in page.items] == [
        canonical_security_id
    ]
