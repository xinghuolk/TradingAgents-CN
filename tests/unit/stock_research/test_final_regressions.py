from dataclasses import replace
from datetime import date

import pytest

from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import EntryPatch, NewEntry, ThesisPatch
from app.services.stock_research.service import StockResearchService
from app.services.stock_research.storage import StockResearchRepository
from app.services.stock_research.references import AnalysisReportAdapter
from app.services.stock_research.models import ResearchSecurityId
from tests.unit.stock_research.fakes import FakeDatabase
from tests.unit.stock_research.test_generation import setup_generation, submit
from tests.unit.stock_research.test_references import FakeRealPortfolioService


@pytest.fixture
def service():
    return StockResearchService(StockResearchRepository(FakeDatabase()))


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["confirm", "archive", "delete"])
async def test_delayed_autosave_cannot_undo_lifecycle(service, monkeypatch, transition):
    await service.get_or_create_workspace("u1", "A", "600519", "Moutai")
    entry = await service.create_entry(
        "u1", NewEntry.decision("A:600519", "buy", date(2026, 9, 8), body="original")
    )
    original_get = service._get_entry
    after = None

    async def delayed_get(*args, **kwargs):
        nonlocal after
        stale = await original_get(*args, **kwargs)
        monkeypatch.setattr(service, "_get_entry", original_get)
        await getattr(service, f"{transition}_entry")("u1", entry.id)
        after = await original_get("u1", entry.id, include_deleted=True)
        return stale

    monkeypatch.setattr(service, "_get_entry", delayed_get)
    with pytest.raises(ResearchError) as caught:
        await service.update_entry_draft("u1", entry.id, EntryPatch(body="late save"))
    assert caught.value.code == "RESEARCH_CONFLICT"
    assert await original_get("u1", entry.id, include_deleted=True) == after


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["convert", "confirm", "archive", "restore_revision"])
async def test_delayed_operation_cannot_resurrect_deleted_entry(service, monkeypatch, operation):
    request = NewEntry.routine_review(scope="portfolio", body="original") if operation == "confirm" else NewEntry.note("A:600519", "title", "original")
    entry = await service.create_entry("u1", request)
    revision = await service.repository.append_revision("u1", "entry", entry.id, entry.to_document(), "manual")
    original_get = service._get_entry

    async def delayed_get(*args, **kwargs):
        stale = await original_get(*args, **kwargs)
        monkeypatch.setattr(service, "_get_entry", original_get)
        await service.delete_entry("u1", entry.id)
        return stale

    monkeypatch.setattr(service, "_get_entry", delayed_get)
    with pytest.raises(ResearchError) as caught:
        if operation == "convert":
            await service.convert_entry("u1", entry.id, "research")
        elif operation == "restore_revision":
            await service.restore_revision("u1", revision.id)
        else:
            await getattr(service, f"{operation}_entry")("u1", entry.id)
    assert caught.value.code == "RESEARCH_CONFLICT"
    current = await original_get("u1", entry.id, include_deleted=True)
    assert current.deleted_at is not None
    assert current.status == "draft"
    assert len(await service.list_revisions("u1", "entry", entry.id)) == 1


@pytest.mark.asyncio
async def test_delayed_thesis_save_does_not_reset_manual_revision(service, monkeypatch):
    await service.get_or_create_workspace("u1", "A", "600519", "Moutai")
    original_get = service.get_workspace

    async def delayed_get(*args):
        stale = await original_get(*args)
        monkeypatch.setattr(service, "get_workspace", original_get)
        await service.save_workspace_version("u1", "A:600519", "version")
        return stale

    monkeypatch.setattr(service, "get_workspace", delayed_get)
    await service.save_thesis_draft("u1", "A:600519", ThesisPatch(body="latest"))
    current = await original_get("u1", "A:600519")
    assert (current.body, current.current_revision) == ("latest", 1)


@pytest.mark.asyncio
async def test_production_reports_resolve_ownership_through_tasks():
    db = FakeDatabase()
    reports = AnalysisReportAdapter(db)
    await db["analysis_tasks"].insert_one({"task_id": "owned", "user_id": "u1"})
    await db["analysis_tasks"].insert_one({"task_id": "other", "user_id": "u2"})
    for task_id in ("owned", "other", "missing"):
        await db["analysis_reports"].insert_one({
            "analysis_id": task_id, "task_id": task_id, "stock_symbol": "600519",
            "analysis_date": "2026-09-08", "summary": "production report",
        })
    found = await reports.list_reports("u1", ResearchSecurityId.parse("A", "600519"), None, None)
    assert [item["analysis_id"] for item in found] == ["owned"]
    assert (await reports.get_report("u1", "owned"))["summary"] == "production report"
    assert await reports.get_report("u2", "owned") is None
    assert await reports.get_report("u1", "missing") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["note", "research"])
async def test_entry_manual_version_and_restore_are_owned_and_preserve_lifecycle(service, kind):
    entry = await service.create_entry("u1", NewEntry.note("A:600519", "title", "v1"))
    if kind == "research":
        entry = await service.convert_entry("u1", entry.id, "research")
    revision = await service.save_entry_version("u1", entry.id, "checkpoint")
    assert revision.reason == "manual"
    assert revision.snapshot["label"] == "checkpoint"
    current = await service.get_entry("u1", entry.id)
    assert (current.body, current.status, current.current_revision) == ("v1", "draft", 1)
    with pytest.raises(ResearchError, match="not found"):
        await service.save_entry_version("u2", entry.id)
    await service.update_entry_draft("u1", entry.id, EntryPatch(body="v2"))
    restored = await service.restore_revision("u1", revision.id)
    assert restored.revision == 2
    assert (await service.get_entry("u1", entry.id)).body == "v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["stock", "portfolio"])
async def test_review_confirmation_freezes_theses_and_versions_existing_workspaces(service, scope):
    await service.get_or_create_workspace("u1", "A", "600519", "Moutai")
    await service.save_thesis_draft("u1", "A:600519", ThesisPatch(body="contemporary", risks=("risk",)))
    request = NewEntry.routine_review(scope=scope, security_id="A:600519" if scope == "stock" else None, body="review")
    if scope == "portfolio":
        request = replace(request, security_ids=("A:600519", "US:AAPL"))
    review = await service.create_entry("u1", request)
    confirmed = await service.confirm_entry("u1", review.id)
    snapshots = confirmed.thesis_snapshot
    assert snapshots is not None
    if scope == "stock":
        assert snapshots["body"] == "contemporary"
    else:
        assert snapshots["workspaces"]["A:600519"]["body"] == "contemporary"
        assert snapshots["workspaces"]["US:AAPL"] == {"security_id": "US:AAPL", "available": False, "reason": "workspace_unavailable"}
    revisions = await service.list_revisions("u1", "workspace", "A:600519")
    assert len(revisions) == 1
    assert revisions[0].reason == "review_confirmed"
    assert await service.list_revisions("u1", "workspace", "US:AAPL") == []
    await service.save_thesis_draft("u1", "A:600519", ThesisPatch(body="later"))
    assert (await service.get_entry("u1", review.id)).thesis_snapshot == snapshots


@pytest.mark.asyncio
async def test_holding_universe_is_complete_without_research_workspaces():
    generation, _, repo, _, _ = await setup_generation()
    repo.db["stock_research_workspaces"].documents.clear()
    generation.references.real_portfolio = FakeRealPortfolioService()
    for number in range(125):
        await repo.db["paper_positions"].insert_one({"user_id": "u1", "market": "US", "code": f"S{number}", "quantity": 2})
    await repo.db["paper_positions"].insert_one({"user_id": "u2", "market": "US", "code": "PRIVATE", "quantity": 3})
    universe = await generation.references.list_holdings("u1")
    assert len(universe) == 126
    assert {item.account_type for item in universe} == {"real", "paper"}
    assert "US:S124" in {item.security_id for item in universe}
    assert "US:PRIVATE" not in {item.security_id for item in universe}
    assert repo.db["stock_research_workspaces"].documents == []


@pytest.mark.asyncio
async def test_review_generation_uses_scope_dates_defaults_and_frozen_decision_detail():
    generation, _, repo, service, review = await setup_generation()
    generation.references.real_portfolio = FakeRealPortfolioService()
    await service.create_entry("u1", NewEntry.note("A:600519", "recent", "period research"))
    decision = await service.create_entry("u1", NewEntry.decision("A:600519", "buy", date(2026, 9, 8), body="decision rationale"))
    await service.confirm_entry("u1", decision.id)
    await service.update_entry_draft("u1", review.id, EntryPatch(
        review_kind="decision", decision_id=decision.id,
        scope_metadata={"include_market": True, "include_real_holdings": True, "date_from": "2026-09-01", "date_through": "2026-09-30"},
    ))
    task = await generation.submit(user_id="u1", target_entry_id=review.id, draft_kind="review", provider="openai", model_name="gpt-5", reasoning_effort=None, references=None)
    context = task.context_snapshot
    assert context["target"]["scope_metadata"]["date_from"] == "2026-09-01"
    assert any(item["body"] == "period research" for item in context["recent_entries"])
    decision_ref = next(item for item in context["references"] if item["kind"] == "decision")
    assert decision_ref["snapshot"]["body"] == "decision rationale"
    assert decision_ref["snapshot"]["thesis_snapshot"]["body"] == "frozen thesis"
    assert any(item["kind"] == "holding_date" and item["account_type"] == "real" for item in context["references"])
    assert any(item["kind"] == "market" and item["available"] is False for item in context["sources"])
    await service.save_thesis_draft("u1", "A:600519", ThesisPatch(body="later thesis"))
    assert (await generation.get("u1", task.id)).context_snapshot == context
    adjusted = await submit(generation, review, context_options={"include_thesis": False, "include_recent_entries": False, "include_real_holdings": False}, references=[])
    assert adjusted.context_snapshot["references"] == []
    assert adjusted.context_snapshot["theses"] == []
    assert adjusted.context_snapshot["recent_entries"] == []


@pytest.mark.asyncio
async def test_all_research_index_specifications_are_preserved():
    db = FakeDatabase()
    await StockResearchRepository(db).ensure_indexes()
    expected = {
        "workspaces": {"workspace_identity": {"keys": [("user_id", 1), ("security_id", 1)], "unique": True}},
        "entries": {
            "entry_identity": {"keys": [("user_id", 1), ("id", 1)], "unique": True},
            "entry_listing": {"keys": [("user_id", 1), ("security_ids", 1), ("entry_type", 1), ("deleted_at", 1), ("updated_at", -1)], "unique": False},
            "decision_trade_identity": {"keys": [("user_id", 1), ("trade_link_keys", 1)], "unique": True, "partialFilterExpression": {"trade_link_keys.0": {"$exists": True}}},
        },
        "revisions": {"revision_identity": {"keys": [("user_id", 1), ("target_type", 1), ("target_id", 1), ("revision", 1)], "unique": True}},
        "generation_tasks": {
            "generation_task_identity": {"keys": [("user_id", 1), ("id", 1)], "unique": True},
            "generation_task_status": {"keys": [("user_id", 1), ("status", 1), ("created_at", 1)], "unique": False},
        },
    }
    for collection, indexes in expected.items():
        assert db[f"stock_research_{collection}"].indexes == indexes


@pytest.mark.asyncio
async def test_decision_default_context_includes_before_and_after_holdings():
    generation, _, _, service, review = await setup_generation()
    generation.references.real_portfolio = FakeRealPortfolioService()
    decision = await service.create_entry("u1", NewEntry.decision("A:600519", "buy", date(2026, 9, 8)))
    await service.confirm_entry("u1", decision.id)
    await service.update_entry_draft("u1", review.id, EntryPatch(review_kind="decision", decision_id=decision.id))
    context = await generation.preview_context("u1", review.id)
    dates = {item["source_date"] for item in context["references"] if item["kind"] == "holding_date"}
    assert "2026-09-01" in dates
    assert len(dates) == 2


@pytest.mark.asyncio
async def test_explicit_reference_selection_retains_unavailable_source_results():
    generation, _, _, _, entry = await setup_generation()
    task = await submit(generation, entry, references=[])
    assert any(item["kind"] == "real_holding" and not item["available"] for item in task.context_snapshot["sources"])


@pytest.mark.asyncio
async def test_missing_paper_source_is_explicit_without_discarding_other_context(monkeypatch):
    generation, _, _, _, entry = await setup_generation()
    async def unavailable(*args):
        raise RuntimeError("paper storage unavailable")
    monkeypatch.setattr(generation.references, "_paper_holdings", unavailable)
    context = await generation.preview_context("u1", entry.id, {"include_paper_holdings": True})
    assert context["theses"][0]["body"] == "frozen thesis"
    assert any(item["kind"] == "paper_holding" and not item["available"] for item in context["sources"])
