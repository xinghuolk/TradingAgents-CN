from datetime import UTC, date, datetime

import pytest

import app.services.stock_research as stock_research
from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    Entry,
    EntryPatch,
    GenerationTask,
    NewEntry,
    Reference,
    ResearchPage,
    ResearchSecurityId,
    Revision,
    ThesisPatch,
    Workspace,
)
from app.services.stock_research.service import StockResearchService


NOW = datetime(2026, 9, 8, 9, 30, tzinfo=UTC)


def test_security_id_canonicalizes_cn_and_supports_us():
    assert str(ResearchSecurityId.parse("CN", "600519")) == "A:600519"
    assert ResearchSecurityId.parse("US", "aapl").code == "AAPL"


@pytest.mark.parametrize("action", ["buy", "add", "reduce", "sell", "observe"])
def test_decision_minimum_is_action_and_date(action):
    entry = Entry.new_decision(
        user_id="u1",
        security=ResearchSecurityId.parse("CN", "600519"),
        action=action,
        decision_date=date(2026, 9, 8),
        body="",
    )
    assert entry.decision_action == action


def test_note_requires_title_and_body():
    with pytest.raises(ResearchError, match="title and body"):
        Entry.new_note(
            user_id="u1",
            security=ResearchSecurityId.parse("CN", "600519"),
            title="",
            body="",
        )


def test_decision_review_may_omit_decision_reference():
    entry = Entry(
        id="review-1",
        user_id="u1",
        entry_type="review",
        scope="stock",
        security_id="A:600519",
        security_ids=("A:600519",),
        body="复盘正文",
        review_kind="decision",
        decision_id=None,
        created_at=NOW,
        updated_at=NOW,
    )

    assert entry.validate() == ()


def test_missing_sources_are_warnings_not_validation_failures():
    entry = Entry.new_note(
        user_id="u1",
        security=ResearchSecurityId.parse("CN", "600519"),
        title="标题",
        body="正文",
        warnings=("analysis report is unavailable",),
    )

    assert entry.validate() == ("analysis report is unavailable",)


def test_domain_documents_round_trip_all_persisted_fields():
    reference = Reference(
        kind="real_trade",
        source_id="trade-1",
        account_type="real",
        source_date=date(2026, 9, 7),
        label="成交",
    )
    workspace = Workspace(
        user_id="u1",
        security_id="A:600519",
        market="A",
        code="600519",
        name="贵州茅台",
        body="当前论点",
        assumptions=("假设",),
        risks=("风险",),
        invalidation_conditions=("失效",),
        open_questions=("问题",),
        tags=("消费",),
        external_links=("https://example.test",),
        current_revision=2,
        created_at=NOW,
        updated_at=NOW,
    )
    entry = Entry(
        id="decision-1",
        user_id="u1",
        entry_type="decision",
        scope="stock",
        security_id="A:600519",
        security_ids=("A:600519",),
        title="决策",
        body="正文",
        status="confirmed",
        tags=("长期",),
        external_links=("https://example.test/decision",),
        references=(reference,),
        decision_action="buy",
        decision_date=date(2026, 9, 8),
        planned_price="1450",
        target_allocation="10%",
        horizon="3y",
        thesis_snapshot={"body": "当前论点"},
        source_metadata={"origin": "manual"},
        current_revision=1,
        confirmed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    revision = Revision(
        id="revision-1",
        user_id="u1",
        target_type="entry",
        target_id="decision-1",
        revision=1,
        snapshot={"body": "正文"},
        reason="decision_confirmed",
        created_at=NOW,
    )
    task = GenerationTask(
        id="task-1",
        user_id="u1",
        target_entry_id="decision-1",
        draft_kind="decision",
        provider="openai",
        model_name="gpt-5",
        reasoning_effort="medium",
        references=(reference,),
        source_ids=("trade-1",),
        context_snapshot={"body": "正文"},
        prompt_version="research-draft-v1",
        status="completed",
        content="草稿",
        generated_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )

    assert Reference.from_document(reference.to_document()) == reference
    assert Workspace.from_document(workspace.to_document()) == workspace
    assert Entry.from_document(entry.to_document()) == entry
    assert Revision.from_document(revision.to_document()) == revision
    assert GenerationTask.from_document(task.to_document()) == task
    assert ResearchPage((entry,), 1, 20, 1).items == (entry,)


def test_analysis_report_reference_keeps_its_display_label():
    reference = Reference.analysis_report("report-1", "旧报告")

    assert (reference.kind, reference.source_id, reference.label) == (
        "analysis_report",
        "report-1",
        "旧报告",
    )


def test_new_entry_factories_capture_low_friction_inputs():
    note = NewEntry.note("CN:600519", "标题", "正文")
    research = NewEntry.research(
        "A:600519", "竞争格局", "正文", topic="竞争优势"
    )
    decision = NewEntry.decision("A:600519", "buy", date(2026, 9, 8))
    review = NewEntry.routine_review(
        scope="stock", security_id="A:600519", body="复盘"
    )
    decision_review = NewEntry.decision_review(
        scope="stock", security_id="A:600519", body="决策复盘"
    )

    assert (note.entry_type, note.security_id, note.title, note.body) == (
        "note",
        "A:600519",
        "标题",
        "正文",
    )
    assert (decision.decision_action, decision.decision_date, decision.body) == (
        "buy",
        date(2026, 9, 8),
        "",
    )
    assert (research.entry_type, research.topic, research.body) == (
        "research",
        "竞争优势",
        "正文",
    )
    assert (review.entry_type, review.review_kind, review.scope) == (
        "review",
        "routine",
        "stock",
    )
    assert (decision_review.review_kind, decision_review.decision_id) == (
        "decision",
        None,
    )


def test_patch_values_keep_only_explicit_workflow_fields():
    thesis = ThesisPatch(body="论点", risks=("风险",))
    entry = EntryPatch(body="正文", tags=("消费",))

    assert thesis.changes() == {"body": "论点", "risks": ("风险",)}
    assert entry.changes() == {"body": "正文", "tags": ("消费",)}


def test_workflow_types_are_available_from_the_package_api():
    assert stock_research.NewEntry is NewEntry
    assert stock_research.ThesisPatch is ThesisPatch
    assert stock_research.EntryPatch is EntryPatch
    assert stock_research.StockResearchService is StockResearchService
