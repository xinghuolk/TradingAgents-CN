from datetime import UTC, date, datetime

import pytest

from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    EntryPatch,
    EntryQuery,
    NewEntry,
    ThesisPatch,
    WorkspaceDirectoryFacts,
    WorkspaceQuery,
)
from app.services.stock_research.service import StockResearchService
from app.services.stock_research.storage import StockResearchRepository
from tests.unit.stock_research.fakes import FakeDatabase


NOW = datetime(2026, 9, 8, 9, 30, tzinfo=UTC)


@pytest.fixture
def repo() -> StockResearchRepository:
    return StockResearchRepository(FakeDatabase())


@pytest.fixture
def service(repo) -> StockResearchService:
    identifiers = iter(f"entry-{number}" for number in range(1, 20))
    return StockResearchService(repo, clock=lambda: NOW, id_factory=lambda: next(identifiers))


class DirectorySourceStub:
    def __init__(self, facts: dict[str, WorkspaceDirectoryFacts]) -> None:
        self.facts = facts
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def get_workspace_facts(
        self, user_id: str, security_ids: tuple[str, ...]
    ) -> dict[str, WorkspaceDirectoryFacts]:
        self.calls.append((user_id, security_ids))
        return self.facts


@pytest.fixture
def source_spy():
    class SourceSpy:
        calls: list[object] = []

    return SourceSpy()


@pytest.mark.asyncio
async def test_autosave_does_not_create_revision_but_manual_save_does(service, repo):
    workspace = await service.get_or_create_workspace(
        "u1", "CN", "600519", "贵州茅台"
    )
    saved = await service.save_thesis_draft(
        "u1",
        workspace.security_id,
        ThesisPatch(body="draft", risks=("需求下滑",)),
    )

    assert saved.body == "draft"
    assert saved.risks == ("需求下滑",)
    assert await repo.list_revisions("u1", "workspace", workspace.security_id) == []

    revision = await service.save_workspace_version(
        "u1", workspace.security_id, "首次论点"
    )

    assert revision.revision == 1
    assert revision.reason == "manual"
    assert revision.snapshot["label"] == "首次论点"
    assert (await service.get_workspace("u1", workspace.security_id)).current_revision == 1


@pytest.mark.asyncio
async def test_note_research_conversion_keeps_identity_and_body(service):
    note = await service.create_entry(
        "u1", NewEntry.note("A:600519", "护城河", "正文")
    )

    research = await service.convert_entry(
        "u1", note.id, "research", topic="竞争优势"
    )

    assert (research.id, research.body, research.entry_type, research.topic) == (
        note.id,
        "正文",
        "research",
        "竞争优势",
    )

    note_again = await service.convert_entry("u1", note.id, "note")
    assert (note_again.id, note_again.body, note_again.entry_type) == (
        note.id,
        "正文",
        "note",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_type,target", [("note", "research"), ("research", "note")])
async def test_archived_document_cannot_be_converted(service, repo, entry_type, target):
    entry = await service.create_entry(
        "u1", NewEntry.note("A:600519", "护城河", "保留正文")
    )
    if entry_type == "research":
        entry = await service.convert_entry("u1", entry.id, "research")
    archived = await service.archive_entry("u1", entry.id)

    with pytest.raises(ResearchError, match="archived entry"):
        await service.convert_entry("u1", archived.id, target)

    stored = await repo.get_entry("u1", archived.id)
    assert (stored.id, stored.entry_type, stored.body, stored.status) == (
        archived.id, entry_type, "保留正文", "archived"
    )


@pytest.mark.asyncio
async def test_confirm_decision_freezes_thesis_and_cannot_be_autosaved(service, repo):
    await service.get_or_create_workspace("u1", "CN", "600519", "贵州茅台")
    await service.save_thesis_draft(
        "u1", "A:600519", ThesisPatch(body="长期品牌溢价", assumptions=("需求稳定",))
    )
    decision = await service.create_entry(
        "u1", NewEntry.decision("A:600519", "buy", date(2026, 9, 8))
    )

    confirmed = await service.confirm_entry("u1", decision.id)

    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_at == NOW
    assert confirmed.thesis_snapshot is not None
    assert confirmed.thesis_snapshot["security_id"] == "A:600519"
    assert confirmed.thesis_snapshot["body"] == "长期品牌溢价"
    revisions = await repo.list_revisions("u1", "entry", decision.id)
    assert [revision.reason for revision in revisions] == ["decision_confirmed"]
    with pytest.raises(ResearchError, match="formal entry"):
        await service.update_entry_draft(
            "u1", decision.id, EntryPatch(body="overwrite")
        )


@pytest.mark.asyncio
async def test_confirmed_review_edit_creates_a_new_formal_revision(service, repo):
    review = await service.create_entry(
        "u1",
        NewEntry.routine_review(
            scope="portfolio", body="## 市场与持仓表现\n\n平稳"
        ),
    )
    confirmed = await service.confirm_entry("u1", review.id)

    edited = await service.update_entry_draft(
        "u1", confirmed.id, EntryPatch(body="修订后的复盘")
    )

    assert edited.body == "修订后的复盘"
    assert edited.status == "confirmed"
    revisions = await repo.list_revisions("u1", "entry", review.id)
    assert [revision.reason for revision in revisions] == [
        "review_confirmed",
        "review_confirmed",
    ]
    assert revisions[-1].snapshot["body"] == "修订后的复盘"


@pytest.mark.asyncio
async def test_archived_confirmed_decision_cannot_be_autosaved(service, repo):
    await service.get_or_create_workspace("u1", "CN", "600519", "贵州茅台")
    decision = await service.create_entry(
        "u1",
        NewEntry.decision(
            "A:600519", "buy", date(2026, 9, 8), body="确认时正文"
        ),
    )
    confirmed = await service.confirm_entry("u1", decision.id)
    archived = await service.archive_entry("u1", confirmed.id)

    with pytest.raises(ResearchError, match="archived entry"):
        await service.update_entry_draft(
            "u1", archived.id, EntryPatch(body="越过正式决策保护")
        )

    stored = await repo.get_entry("u1", archived.id)
    assert stored is not None
    assert (stored.body, stored.status, stored.confirmed_at) == (
        "确认时正文",
        "archived",
        NOW,
    )
    assert len(await repo.list_revisions("u1", "entry", archived.id)) == 1


@pytest.mark.asyncio
async def test_archived_confirmed_review_cannot_be_autosaved(service, repo):
    review = await service.create_entry(
        "u1", NewEntry.routine_review(scope="portfolio", body="正式复盘")
    )
    confirmed = await service.confirm_entry("u1", review.id)
    archived = await service.archive_entry("u1", confirmed.id)

    with pytest.raises(ResearchError, match="archived entry"):
        await service.update_entry_draft(
            "u1", archived.id, EntryPatch(body="无版本覆盖")
        )

    stored = await repo.get_entry("u1", archived.id)
    assert stored is not None
    assert (stored.body, stored.status, stored.confirmed_at) == (
        "正式复盘",
        "archived",
        NOW,
    )
    revisions = await repo.list_revisions("u1", "entry", archived.id)
    assert [revision.snapshot["body"] for revision in revisions] == ["正式复盘"]


@pytest.mark.asyncio
async def test_restore_old_workspace_revision_writes_a_new_latest_revision(service, repo):
    workspace = await service.get_or_create_workspace("u1", "CN", "600519", "贵州茅台")
    await service.save_thesis_draft("u1", workspace.security_id, ThesisPatch(body="v1"))
    first = await service.save_workspace_version("u1", workspace.security_id, "v1")
    await service.save_thesis_draft("u1", workspace.security_id, ThesisPatch(body="v2"))
    await service.save_workspace_version("u1", workspace.security_id, "v2")

    restored = await service.restore_revision("u1", first.id)

    current = await service.get_workspace("u1", workspace.security_id)
    assert (restored.revision, restored.reason, current.body, current.current_revision) == (
        3,
        "revision_restored",
        "v1",
        3,
    )
    assert len(await repo.list_revisions("u1", "workspace", workspace.security_id)) == 3


@pytest.mark.asyncio
async def test_restore_old_entry_revision_writes_a_new_latest_revision(service, repo):
    review = await service.create_entry(
        "u1", NewEntry.routine_review(scope="portfolio", body="v1")
    )
    await service.confirm_entry("u1", review.id)
    await service.update_entry_draft("u1", review.id, EntryPatch(body="v2"))
    first = (await repo.list_revisions("u1", "entry", review.id))[0]

    restored = await service.restore_revision("u1", first.id)

    current = await repo.get_entry("u1", review.id)
    assert current is not None
    assert (restored.revision, restored.reason, current.body, current.current_revision) == (
        3,
        "revision_restored",
        "v1",
        3,
    )


@pytest.mark.asyncio
async def test_review_does_not_change_thesis_until_explicit_apply(service):
    workspace = await service.get_or_create_workspace("u1", "CN", "600519", "贵州茅台")
    await service.save_thesis_draft("u1", workspace.security_id, ThesisPatch(body="旧论点"))
    review = await service.create_entry(
        "u1",
        NewEntry.routine_review(
            scope="stock", security_id="A:600519", body="失效条件改变"
        ),
    )
    await service.confirm_entry("u1", review.id)

    assert (await service.get_workspace("u1", "A:600519")).body == "旧论点"

    revision = await service.apply_review_to_thesis(
        "u1", review.id, ThesisPatch(body="新论点")
    )

    assert revision.reason == "review_applied_to_thesis"
    assert (await service.get_workspace("u1", "A:600519")).body == "新论点"


@pytest.mark.asyncio
async def test_mutations_require_owner_and_delete_does_not_call_sources(
    service, repo, source_spy
):
    entry = await service.create_entry(
        "u1", NewEntry.note("A:600519", "标题", "正文")
    )
    with pytest.raises(ResearchError, match="not found"):
        await service.archive_entry("u2", entry.id)

    await service.delete_entry("u1", entry.id)

    assert await repo.get_entry("u1", entry.id) is None
    assert source_spy.calls == []


@pytest.mark.asyncio
async def test_archive_restore_and_permanent_delete_only_change_the_entry(service, repo):
    entry = await service.create_entry(
        "u1", NewEntry.note("A:600519", "标题", "正文")
    )

    archived = await service.archive_entry("u1", entry.id)
    assert (archived.status, archived.archived_at) == ("archived", NOW)

    await service.delete_entry("u1", entry.id)
    restored = await service.restore_entry("u1", entry.id)
    assert restored.deleted_at is None
    assert restored.id == entry.id

    await service.permanently_delete_entry("u1", entry.id)
    assert await repo.get_entry("u1", entry.id, include_deleted=True) == restored

    await service.delete_entry("u1", entry.id)
    await service.permanently_delete_entry("u1", entry.id)
    assert await repo.get_entry("u1", entry.id, include_deleted=True) is None


@pytest.mark.asyncio
async def test_missing_owned_records_raise_not_found(service):
    with pytest.raises(ResearchError, match="workspace not found"):
        await service.get_workspace("u1", "A:600519")
    with pytest.raises(ResearchError, match="entry not found"):
        await service.update_entry_draft("u1", "missing", EntryPatch(body="x"))
    with pytest.raises(ResearchError, match="revision not found"):
        await service.restore_revision("u1", "missing")


@pytest.mark.asyncio
async def test_read_workflows_delegate_to_user_scoped_repository_queries(service, repo):
    workspace = await service.get_or_create_workspace(
        "u1", "CN", "600519", "贵州茅台"
    )
    entry = await service.create_entry(
        "u1", NewEntry.note("A:600519", "标题", "正文")
    )
    revision = await service.save_workspace_version(
        "u1", workspace.security_id, "初始版本"
    )
    await service.delete_entry("u1", entry.id)

    workspaces = await service.list_workspaces("u1", WorkspaceQuery(page_size=10))
    entries = await service.list_entries(
        "u1", EntryQuery(security_id="A:600519", page_size=10)
    )
    revisions = await service.list_revisions(
        "u1", "workspace", workspace.security_id
    )
    trash = await service.list_trash("u1", page=1, page_size=10)

    assert [item.security_id for item in workspaces.items] == [workspace.security_id]
    assert workspaces.items[0].thesis_summary == ""
    assert entries.items == ()
    deleted = await service.get_entry("u1", entry.id, include_deleted=True)
    assert deleted.id == entry.id
    assert deleted.deleted_at == NOW
    assert revisions == [revision]
    assert await service.get_revision("u1", revision.id) == revision
    assert [item.id for item in trash.items] == [entry.id]


@pytest.mark.asyncio
async def test_workspace_directory_enriches_filters_and_paginates_derived_facts(repo):
    service = StockResearchService(
        repo,
        directory_source=DirectorySourceStub(
            {
                "A:600519": WorkspaceDirectoryFacts(
                    latest_entry_type="decision",
                    latest_entry_at=NOW,
                    has_real_holding=True,
                    watchlisted=True,
                ),
                "HK:00700": WorkspaceDirectoryFacts(has_paper_holding=True),
            }
        ),
    )
    await service.get_or_create_workspace("u1", "CN", "600519", "贵州茅台")
    await service.save_thesis_draft(
        "u1", "A:600519", ThesisPatch(body="  核心论点\n第二行  ")
    )
    await service.get_or_create_workspace("u1", "HK", "00700", "腾讯控股")

    page = await service.list_workspaces(
        "u1",
        WorkspaceQuery(real_holding=True, watchlisted=True, page=1, page_size=1),
    )

    assert page.total == 1
    assert page.items[0].security_id == "A:600519"
    assert page.items[0].thesis_summary == "核心论点 第二行"
    assert page.items[0].latest_entry_type == "decision"
    assert page.items[0].has_real_holding is True
    assert page.items[0].has_paper_holding is False
    assert page.items[0].watchlisted is True


@pytest.mark.asyncio
async def test_read_workflows_hide_missing_or_other_user_records(service, repo):
    entry = await service.create_entry(
        "u1", NewEntry.note("A:600519", "标题", "正文")
    )
    revision = await repo.append_revision(
        "u1", "entry", entry.id, entry.to_document(), "manual"
    )

    with pytest.raises(ResearchError, match="entry not found"):
        await service.get_entry("u2", entry.id)
    with pytest.raises(ResearchError, match="revision not found"):
        await service.get_revision("u2", revision.id)
