from __future__ import annotations

import asyncio
import ast
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import NewEntry, Reference, ThesisPatch, utc_now
from app.services.stock_research.references import (
    AnalysisReportAdapter, PaperTradeAdapter, ReferenceService,
)
from app.services.stock_research.service import StockResearchService
from app.services.stock_research.storage import StockResearchRepository
from tests.unit.stock_research.fakes import FakeDatabase


class FakeGenerator:
    def __init__(self):
        self.error = None
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error
        return "AI original"


async def setup_generation(user_id="u1", *, review=True):
    from app.services.stock_research.generation import ResearchGenerationService

    db = FakeDatabase()
    repo = StockResearchRepository(db)
    service = StockResearchService(repo)
    await service.get_or_create_workspace(user_id, "CN", "600519", "Moutai")
    await service.save_thesis_draft(user_id, "A:600519", ThesisPatch(body="frozen thesis"))
    entry = await service.create_entry(user_id, (
        NewEntry.routine_review(scope="stock", security_id="A:600519", body="human body")
        if review else NewEntry.note("A:600519", "title", "human body")
    ))
    refs = ReferenceService(repo, AnalysisReportAdapter(db), None, PaperTradeAdapter(db))
    generator = FakeGenerator()
    generation = ResearchGenerationService(repo, refs, generator)
    return generation, generator, repo, service, entry


async def submit(generation, entry, **kwargs):
    return await generation.submit(
        user_id=entry.user_id, target_entry_id=entry.id, draft_kind=entry.entry_type,
        provider="openai", model_name="gpt-5", reasoning_effort="high",
        references=kwargs.pop("references", []), **kwargs,
    )


@pytest.mark.asyncio
async def test_task_freezes_context_and_records_model_metadata():
    generation, generator, repo, service, entry = await setup_generation()
    await repo.db["analysis_reports"].insert_one({
        "analysis_id": "r1", "user_id": "u1", "stock_symbol": "600519",
        "summary": "frozen report", "api_key": "private-secret",
    })
    task = await submit(generation, entry, references=[Reference.analysis_report("r1", "Report")])
    assert task.status == "pending"
    assert (await repo.get_generation_task("u1", task.id)).status == "pending"
    await service.save_thesis_draft("u1", "A:600519", ThesisPatch(body="new thesis"))
    await repo.db["analysis_reports"].update_one({"analysis_id": "r1"}, {"$set": {"summary": "new report"}})
    await generation.run(task.id, "u1")
    stored = await generation.get("u1", task.id)
    assert stored.status == "completed"
    assert stored.model_name == "gpt-5"
    assert stored.reasoning_effort == "high"
    assert stored.prompt_version == "research-draft-v1"
    assert stored.context_snapshot["references"][0]["source_id"] == "r1"
    assert "frozen thesis" in generator.calls[0]["user_prompt"]
    assert "frozen report" in generator.calls[0]["user_prompt"]
    assert "new thesis" not in generator.calls[0]["user_prompt"]
    assert "new report" not in generator.calls[0]["user_prompt"]
    assert "private-secret" not in str(stored.to_document())
    assert "api_key" not in str(stored.to_document())
    result = await repo.get_entry("u1", entry.id)
    assert result.body == "human body"
    assert result.decision_id is None
    assert len(result.ai_drafts) == 1
    assert result.ai_drafts[0] == {
        "content": "AI original", "provider": "openai", "model_name": "gpt-5",
        "reasoning_effort": "high", "generated_at": stored.generated_at.isoformat(),
        "prompt_version": "research-draft-v1", "source_ids": ["r1"],
        "references": stored.context_snapshot["references"], "task_id": task.id,
    }
    prompt = generator.calls[0]["system_prompt"]
    for heading in ("## 市场与持仓表现", "## 重要事实、公告和调研变化", "## 当前论点变化", "## 后续观察"):
        assert heading in prompt


@pytest.mark.asyncio
async def test_failure_does_not_create_draft_or_touch_human_body(caplog):
    generation, generator, repo, _, entry = await setup_generation(review=False)
    generator.error = RuntimeError("Authorization: Bearer private-token https://secret.example")
    task = await submit(generation, entry)
    await generation.run(task.id, "u1")
    stored = await generation.get("u1", task.id)
    assert stored.status == "failed"
    assert stored.error_code == "GENERATION_FAILED"
    assert stored.content is None
    assert "private-token" not in str(stored.to_document()) + caplog.text
    result = await repo.get_entry("u1", entry.id)
    assert result.body == "human body"
    assert result.ai_drafts == ()


@pytest.mark.asyncio
async def test_duplicate_run_and_human_edit_preserve_single_original():
    generation, generator, repo, _, entry = await setup_generation()
    task = await submit(generation, entry)
    generator.release.clear()
    running = asyncio.create_task(generation.run(task.id, "u1"))
    await generator.started.wait()
    assert (await generation.get("u1", task.id)).status == "running"
    await generation.run(task.id, "u1")
    entry = await repo.patch_entry(entry, {"body": "edited while generating"})
    generator.release.set()
    await running
    await generation.run(task.id, "u1")
    stored = await repo.get_entry("u1", entry.id)
    assert stored.body == "edited while generating"
    assert len(stored.ai_drafts) == len(generator.calls) == 1
    # A human save based on an older read must not overwrite appended originals.
    await repo.patch_entry(entry, {"body": "later human edit"})
    assert len((await repo.get_entry("u1", entry.id)).ai_drafts) == 1


@pytest.mark.asyncio
async def test_user_scope_and_foreign_reference_do_not_disclose_source():
    generation, generator, repo, _, entry = await setup_generation()
    await repo.db["analysis_reports"].insert_one({
        "analysis_id": "foreign", "user_id": "u2", "stock_symbol": "600519", "summary": "foreign secret",
    })
    task = await submit(generation, entry, references=[Reference.analysis_report("foreign")])
    assert task.context_snapshot["references"][0]["available"] is False
    with pytest.raises(ResearchError, match="not found"):
        await generation.get("u2", task.id)
    await generation.run(task.id, "u2")
    assert generator.calls == []
    with pytest.raises(ResearchError, match="not found"):
        await generation.submit(user_id="u2", target_entry_id=entry.id, draft_kind="review",
                                provider="openai", model_name="gpt-5", reasoning_effort=None, references=[])


@pytest.mark.asyncio
async def test_deleted_target_fails_without_attachment():
    generation, _, repo, _, entry = await setup_generation()
    task = await submit(generation, entry)
    await repo.soft_delete_entry("u1", entry.id, utc_now())
    await generation.run(task.id, "u1")
    assert (await generation.get("u1", task.id)).status == "failed"
    assert (await repo.get_entry("u1", entry.id, include_deleted=True)).ai_drafts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["confirmed", "archived"])
async def test_formal_or_archived_target_cannot_receive_generated_original(status):
    generation, generator, repo, _, entry = await setup_generation()
    task = await submit(generation, entry)
    await repo.patch_entry(entry, {"status": status})
    with pytest.raises(ResearchError) as error:
        await submit(generation, entry)
    assert error.value.code == "RESEARCH_CONFLICT"
    await generation.run(task.id, "u1")
    assert (await generation.get("u1", task.id)).status == "failed"
    assert (await repo.get_entry("u1", entry.id)).ai_drafts == ()


@pytest.mark.asyncio
async def test_cancelled_generation_marks_failed_without_overwriting_body():
    generation, generator, repo, _, entry = await setup_generation()
    task = await submit(generation, entry)
    generator.release.clear()
    running = asyncio.create_task(generation.run(task.id, "u1"))
    await generator.started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert (await generation.get("u1", task.id)).status == "failed"
    assert (await repo.get_entry("u1", entry.id)).body == "human body"
    assert (await repo.get_entry("u1", entry.id)).ai_drafts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["append", "completed"])
async def test_cancellation_after_applied_write_keeps_task_and_original_coherent(monkeypatch, boundary):
    generation, _, repo, _, entry = await setup_generation()
    task = await submit(generation, entry)
    applied = asyncio.Event()
    release = asyncio.Event()
    collection = repo.db[
        "stock_research_entries" if boundary == "append" else "stock_research_generation_tasks"
    ]
    update_one = collection.update_one

    async def pause_after_write(query, update, **kwargs):
        result = await update_one(query, update, **kwargs)
        selected = (
            "ai_drafts" in update.get("$push", {})
            if boundary == "append" else update.get("$set", {}).get("status") == "completed"
        )
        if selected:
            applied.set()
            await release.wait()
        return result

    monkeypatch.setattr(collection, "update_one", pause_after_write)
    running = asyncio.create_task(generation.run(task.id, "u1"))
    await applied.wait()
    running.cancel()
    await asyncio.sleep(0)
    assert not running.done()
    running.cancel()
    await asyncio.sleep(0)
    assert not running.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    stored = await generation.get("u1", task.id)
    result = await repo.get_entry("u1", entry.id)
    assert (stored.status, len(result.ai_drafts)) in {("completed", 1), ("failed", 0)}
    assert result.body == "human body"
    await generation.run(task.id, "u1")
    assert len((await repo.get_entry("u1", entry.id)).ai_drafts) == len(result.ai_drafts)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary,expected", [("append", ("failed", 0)), ("completed", ("completed", 1))])
async def test_write_error_after_apply_reconciles_persisted_result(monkeypatch, boundary, expected):
    generation, _, repo, _, entry = await setup_generation()
    task = await submit(generation, entry)
    collection = repo.db[
        "stock_research_entries" if boundary == "append" else "stock_research_generation_tasks"
    ]
    update_one = collection.update_one

    async def fail_after_apply(query, update, **kwargs):
        result = await update_one(query, update, **kwargs)
        selected = (
            "ai_drafts" in update.get("$push", {})
            if boundary == "append" else update.get("$set", {}).get("status") == "completed"
        )
        if selected:
            raise RuntimeError("private storage exception")
        return result

    monkeypatch.setattr(collection, "update_one", fail_after_apply)
    await generation.run(task.id, "u1")
    stored = await generation.get("u1", task.id)
    result = await repo.get_entry("u1", entry.id)
    assert (stored.status, len(result.ai_drafts)) == expected
    assert result.body == "human body"
    assert "private storage exception" not in str(stored.to_document())


@pytest.mark.asyncio
async def test_cancelled_duplicate_claim_cannot_fail_legitimate_owner(monkeypatch):
    generation, generator, repo, _, entry = await setup_generation()
    task = await submit(generation, entry)
    generator.release.clear()
    owner = asyncio.create_task(generation.run(task.id, "u1"))
    await generator.started.wait()
    claimed = asyncio.Event()
    release = asyncio.Event()
    claim = repo.claim_generation_task

    async def pause_unowned_claim(user_id, task_id):
        result = await claim(user_id, task_id)
        assert result is None
        claimed.set()
        await release.wait()
        return result

    monkeypatch.setattr(repo, "claim_generation_task", pause_unowned_claim)
    duplicate = asyncio.create_task(generation.run(task.id, "u1"))
    await claimed.wait()
    duplicate.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await duplicate
    try:
        assert (await generation.get("u1", task.id)).status == "running"
    finally:
        generator.release.set()
        await owner
    result = await repo.get_entry("u1", entry.id)
    assert (await generation.get("u1", task.id)).status == "completed"
    assert len(result.ai_drafts) == len(generator.calls) == 1
    assert result.body == "human body"


@pytest.mark.asyncio
async def test_cancelled_successful_claim_settles_before_owner_failure(monkeypatch):
    generation, generator, repo, _, entry = await setup_generation()
    task = await submit(generation, entry)
    claimed = asyncio.Event()
    release = asyncio.Event()
    claim = repo.claim_generation_task

    async def pause_owned_claim(user_id, task_id):
        result = await claim(user_id, task_id)
        claimed.set()
        await release.wait()
        return result

    monkeypatch.setattr(repo, "claim_generation_task", pause_owned_claim)
    running = asyncio.create_task(generation.run(task.id, "u1"))
    await claimed.wait()
    running.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert (await generation.get("u1", task.id)).status == "failed"
    assert (await repo.get_entry("u1", entry.id)).ai_drafts == ()
    assert generator.calls == []


@pytest.mark.asyncio
async def test_completion_storage_failure_removes_only_this_original(monkeypatch):
    generation, _, repo, _, entry = await setup_generation()
    first = await submit(generation, entry)
    await generation.run(first.id, "u1")
    second = await submit(generation, entry)
    collection = repo.db["stock_research_generation_tasks"]
    original = collection.update_one

    async def fail_completion(query, update, **kwargs):
        if update.get("$set", {}).get("status") == "completed":
            raise RuntimeError("database secret")
        return await original(query, update, **kwargs)

    monkeypatch.setattr(collection, "update_one", fail_completion)
    await generation.run(second.id, "u1")
    assert (await generation.get("u1", second.id)).status == "failed"
    drafts = (await repo.get_entry("u1", entry.id)).ai_drafts
    assert [item["task_id"] for item in drafts] == [first.id]


class RecordingLLM:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return SimpleNamespace(content=[{"type": "reasoning", "text": "private reasoning"},
                                        {"type": "text", "text": "public draft"}])


async def configured_generator(provider="openai"):
    from app.services.stock_research.generation import ConfiguredResearchTextGenerator

    db = FakeDatabase()
    await db["system_configs"].insert_one({"is_active": True, "version": 1, "llm_configs": [{
        "provider": provider, "model_name": "chosen", "api_key": "configured-secret-key",
        "api_base": "https://configured.example/v1", "enabled": True,
        "temperature": 0.2, "max_tokens": 1234, "timeout": 45,
    }]})
    llm = RecordingLLM()
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return llm

    generator = ConfiguredResearchTextGenerator(db, llm_factory=factory)
    return generator, db, llm, factory_calls


@pytest.mark.asyncio
async def test_configured_generator_uses_exact_config_and_only_public_text():
    generator, _, llm, calls = await configured_generator()
    content = await generator.generate(user_id="u1", provider="openai", model_name="chosen",
                                       reasoning_effort="high", system_prompt="system", user_prompt="input")
    assert content == "public draft"
    assert calls == [{"provider": "openai", "model": "chosen", "backend_url": "https://configured.example/v1",
                      "temperature": 0.2, "max_tokens": 1234, "timeout": 45, "api_key": "configured-secret-key"}]
    assert llm.calls[0][1] == {"reasoning_effort": "high"}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model", [("wrong", "chosen"), ("openai", "missing")])
async def test_configured_generator_never_falls_back(provider, model):
    generator, _, _, calls = await configured_generator()
    with pytest.raises(ResearchError) as error:
        await generator.generate(user_id="u1", provider=provider, model_name=model,
                                 reasoning_effort=None, system_prompt="s", user_prompt="u")
    assert error.value.code == "INVALID_GENERATION_MODEL"
    assert calls == []


@pytest.mark.asyncio
async def test_disabled_model_cannot_be_used():
    generator, db, _, calls = await configured_generator()
    document = db["system_configs"].documents[0]
    document["llm_configs"][0]["enabled"] = False
    with pytest.raises(ResearchError) as error:
        await generator.generate(user_id="u1", provider="openai", model_name="chosen",
                                 reasoning_effort=None, system_prompt="s", user_prompt="u")
    assert error.value.code == "INVALID_GENERATION_MODEL"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["codex", "claude_code", "anthropic", "google", "deepseek"])
async def test_unsupported_effort_is_rejected_without_a_provider_call(provider):
    generator, _, _, calls = await configured_generator(provider)
    with pytest.raises(ResearchError) as error:
        await generator.generate(user_id="u1", provider=provider, model_name="chosen",
                                 reasoning_effort="high", system_prompt="s", user_prompt="u")
    assert error.value.code == "INVALID_GENERATION_SETTINGS"
    assert calls == []


@pytest.mark.asyncio
async def test_empty_oauth_token_never_falls_back_to_local_credentials(monkeypatch):
    from app.routers import oauth
    from app.services import oauth_service

    generator, _, _, calls = await configured_generator("codex")

    async def resolve(*args):
        return ""

    monkeypatch.setattr(oauth, "get_credentials_collection", lambda: object())
    monkeypatch.setattr(oauth_service, "resolve", resolve)
    with pytest.raises(ResearchError) as error:
        await generator.generate(user_id="u1", provider="codex", model_name="chosen",
                                 reasoning_effort=None, system_prompt="s", user_prompt="u")
    assert error.value.code == "GENERATION_CREDENTIALS_UNAVAILABLE"
    assert calls == []


@pytest.mark.asyncio
async def test_oauth_is_resolved_for_current_user_and_failure_is_sanitized(monkeypatch, caplog):
    from app.routers import oauth
    from app.services import oauth_service

    generator, _, _, calls = await configured_generator("codex")
    users = []

    async def resolve(collection, user_id, provider):
        users.append((user_id, provider))
        if user_id == "u2":
            raise RuntimeError("oauth-private-token")
        return "user-one-token"

    monkeypatch.setattr(oauth, "get_credentials_collection", lambda: object())
    monkeypatch.setattr(oauth_service, "resolve", resolve)
    for user_id in ("u1", "u2"):
        try:
            await generator.generate(user_id=user_id, provider="codex", model_name="chosen",
                                     reasoning_effort=None, system_prompt="s", user_prompt="u")
        except ResearchError as error:
            assert user_id == "u2"
            assert "oauth-private-token" not in str(error)
    assert users == [("u1", "codex"), ("u2", "codex")]
    assert len(calls) == 1
    assert calls[0]["api_key"] == "user-one-token"
    assert "oauth-private-token" not in caplog.text


def test_anthropic_factory_forwards_configured_api_key(monkeypatch):
    # Execute the actual factory while avoiding graph-module import side effects.
    # The lazy adapter imports are stubbed; no client or provider call is made.
    import sys

    root = Path(__file__).resolve().parents[3]
    tree = ast.parse((root / "tradingagents/graph/trading_graph.py").read_text())
    factory_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "create_llm_by_provider")
    monkeypatch.setitem(sys.modules, "tradingagents.llm_adapters.deepseek_adapter", SimpleNamespace(ChatDeepSeek=object))
    monkeypatch.setitem(sys.modules, "tradingagents.llm_adapters.openai_compatible_base", SimpleNamespace(create_openai_compatible_llm=object))
    calls = []

    def anthropic(**kwargs):
        calls.append(kwargs)
        return object()

    namespace = {"logger": SimpleNamespace(info=lambda *args: None), "ChatAnthropic": anthropic,
                 "instrument_llm_for_model_usage": lambda llm, **kwargs: llm}
    exec(compile(ast.Module(body=[factory_node], type_ignores=[]), "factory", "exec"), namespace)
    namespace["create_llm_by_provider"](provider="anthropic", model="claude-selected",
        backend_url="https://configured.example", temperature=0.2, max_tokens=1000,
        timeout=45, api_key="selected-config-key")
    assert calls[0]["api_key"] == "selected-config-key"
