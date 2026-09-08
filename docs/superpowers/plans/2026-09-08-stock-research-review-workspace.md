# Stock Research and Review Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a personal, document-first stock research workspace that connects theses, notes, research, decisions, manual reviews, existing portfolio facts, and traceable AI drafts inside TradingAgents-CN.

**Architecture:** Add an authenticated `/api/research` module backed by four MongoDB collections and a Vue research surface at `/research` and `/research/:code`. Domain and repository code own validation, user scoping, revisions, soft deletion, references, and generation-task persistence; routers only translate HTTP requests, while the frontend uses one typed API client and reusable editors. AI generation reuses configured LLM providers through an injected generator and the application's existing in-process background-task pattern.

**Tech Stack:** Python 3.11, FastAPI, Pydantic/dataclasses, Motor/PyMongo-style collections, pytest, Vue 3, TypeScript, Element Plus, `marked`, Vite, Playwright smoke checks.

**Spec:** `docs/superpowers/specs/2026-09-08-stock-research-review-workspace-design.md`

## Global Constraints

- TradingAgents-CN remains the only product entry; do not create or port a separate `../stock` application, SQLite store, Markdown file store, or scheduler.
- Scope includes `/research`, `/research/:code`, theses, notes, research, decisions, on-demand routine/decision reviews, references, revisions, trash, and AI drafts.
- Scope excludes reminders, tasks, automatic review creation, daily quote refresh, broker connections, attachment upload, historical `../stock` data migration, and AI model-effectiveness evaluation.
- Use MongoDB collections `stock_research_workspaces`, `stock_research_entries`, `stock_research_revisions`, and `stock_research_generation_tasks`.
- Use `market:code` as `security_id`; accept `CN`/`A`, `HK`, and `US`, canonicalizing `CN` to `A` in stored identifiers while returning `CN`, `HK`, or `US` through the API.
- Route `/research/:code` carries the market in `?market=CN|HK|US`; never infer an ambiguous market from the code.
- Every repository read and write includes the authenticated `user_id`; references never own, mutate, or cascade-delete source reports, holdings, or trades.
- Real and paper account facts remain visibly separate and are never combined into one holding, return, or P&L figure.
- AI output is an immutable draft, not a formal decision or review; record provider, model name, reasoning effort, generated time, prompt version, source IDs, and references, but never hidden chain-of-thought.
- Never silently switch an unavailable AI model. A failed task records failure and does not create an empty draft or alter human-authored content.
- Autosave updates the current draft only. Revisions are created only by explicit version save, decision/review confirmation, thesis application, or version restoration.
- Keep validation suitable for personal use: notes require title and body; decisions require action and date; other incomplete non-critical fields produce warnings rather than blocking saves.
- Run integration checks that need MongoDB, Redis, market-data providers, OAuth, or LLM credentials only when explicitly opted in per `docs/testing.md`.

## Shared Contracts

Use these names unchanged throughout the plan:

```python
Market = Literal["CN", "HK", "US"]
EntryType = Literal["note", "research", "decision", "review"]
EntryStatus = Literal["draft", "confirmed", "archived"]
ScopeType = Literal["stock", "portfolio"]
DecisionAction = Literal["buy", "add", "reduce", "sell", "observe"]
ReviewKind = Literal["routine", "decision"]
AccountType = Literal["real", "paper"]
ReferenceKind = Literal[
    "analysis_report", "real_trade", "paper_trade", "decision", "holding_date"
]
GenerationStatus = Literal["pending", "running", "completed", "failed"]
```

All API responses use the existing `ok(data)` wrapper. Domain failures use:

```json
{"detail":{"code":"INVALID_ENTRY","message":"decision action is required"}}
```

The frontend `ApiClient` unwraps the success envelope as it already does for existing APIs.

## File Map

**Backend domain and persistence**

- Create `app/services/stock_research/models.py`: canonical identifiers, typed domain records, request normalization, snapshots, references, and pagination values.
- Create `app/services/stock_research/errors.py`: stable, sanitized research error codes.
- Create `app/services/stock_research/storage.py`: four-collection repository, indexes, user-scoped CRUD, atomic revision allocation, and link uniqueness.
- Create `app/services/stock_research/service.py`: workspace, entry, confirmation, revision, trash, conversion, and thesis-application workflows.
- Create `app/services/stock_research/references.py`: read-only adapters for reports, real/paper trades, holding dates, and recommended links.
- Create `app/services/stock_research/prompts.py`: versioned note/research/review prompt construction from frozen context.
- Create `app/services/stock_research/generation.py`: persisted asynchronous task orchestration and injected LLM generator.
- Create `app/services/stock_research/__init__.py`: public exports only.
- Create `app/routers/stock_research.py`: authenticated `/research` HTTP contract and safe error mapping.
- Modify `app/main.py`: include the router and initialize research indexes in lifespan.
- Modify `scripts/harness.py`: include research unit tests in the service-free quick gate.

**Backend tests**

- Create `tests/unit/stock_research/fakes.py`: local async Mongo collection/database fake supporting the operators used by the repository.
- Create `tests/unit/stock_research/test_models.py`: identifier and lightweight validation tests.
- Create `tests/unit/stock_research/test_storage.py`: indexes, user isolation, revision allocation, soft delete, and decision-trade link tests.
- Create `tests/unit/stock_research/test_service.py`: workflows and revision-trigger rules.
- Create `tests/unit/stock_research/test_references.py`: source scoping, unavailable snapshots, account separation, and recommendations.
- Create `tests/unit/stock_research/test_generation.py`: frozen input, metadata, failure, and no-overwrite behavior.
- Create `tests/unit/test_stock_research_router.py`: authentication, resources, response envelopes, and sanitized failures.

**Frontend**

- Create `frontend/src/api/stockResearch.ts`: shared TypeScript contracts and all `/api/research` calls.
- Create `frontend/src/views/Research/index.vue`: searchable/filterable research directory and global routine-review creation.
- Create `frontend/src/views/Research/Workspace.vue`: document-first individual-stock workspace and responsive section navigation.
- Create `frontend/src/components/Research/ResearchMarkdownEditor.vue`: Markdown edit/preview surface with explicit save-state display.
- Create `frontend/src/components/Research/ResearchEntryList.vue`: section-specific record list and selection.
- Create `frontend/src/components/Research/ResearchReferencePicker.vue`: grouped, unavailable-aware source selection.
- Create `frontend/src/components/Research/DecisionEditor.vue`: minimal decision form and confirmation.
- Create `frontend/src/components/Research/ReviewEditor.vue`: routine/decision review creation, confirmation, and thesis-diff application.
- Create `frontend/src/components/Research/GenerationDialog.vue`: model, reasoning effort, context, submit, and task status UI.
- Create `frontend/src/composables/useResearchAutosave.ts`: debounced serialized saves, dirty state, retry, and stale-response protection.
- Modify `frontend/src/router/index.ts`: `/research` and `/research/:code` routes.
- Modify `frontend/src/components/Layout/SidebarMenu.vue`: global research navigation item.
- Modify `frontend/src/views/Stocks/Detail.vue`: research-workspace link and recent-research timestamp.

**Documentation**

- Modify `ARCHITECTURE.md`: research module ownership, persistence, and source-read boundaries.
- Modify `docs/testing.md`: research quick tests and opt-in generation smoke check.

---

### Task 1: Domain Models, Validation, and MongoDB Repository

**Files:**
- Create: `app/services/stock_research/__init__.py`
- Create: `app/services/stock_research/models.py`
- Create: `app/services/stock_research/errors.py`
- Create: `app/services/stock_research/storage.py`
- Create: `tests/unit/stock_research/__init__.py`
- Create: `tests/unit/stock_research/fakes.py`
- Create: `tests/unit/stock_research/test_models.py`
- Create: `tests/unit/stock_research/test_storage.py`

**Interfaces:**
- Consumes: the async collection API returned by `app.core.database.get_mongo_db`.
- Produces: `ResearchSecurityId.parse(market, code)`, `Reference`, `Workspace`, `Entry`, `Revision`, `GenerationTask`, `ResearchPage[T]`, `ResearchError`, and `StockResearchRepository` methods used by every later backend task.

- [ ] **Step 1: Write failing identifier and lightweight-validation tests**

```python
def test_security_id_canonicalizes_cn_and_supports_us():
    assert str(ResearchSecurityId.parse("CN", "600519")) == "A:600519"
    assert ResearchSecurityId.parse("US", "aapl").code == "AAPL"

@pytest.mark.parametrize("action", ["buy", "add", "reduce", "sell", "observe"])
def test_decision_minimum_is_action_and_date(action):
    entry = Entry.new_decision(
        user_id="u1", security=ResearchSecurityId.parse("CN", "600519"),
        action=action, decision_date=date(2026, 9, 8), body="",
    )
    assert entry.decision_action == action

def test_note_requires_title_and_body():
    with pytest.raises(ResearchError, match="title and body"):
        Entry.new_note(user_id="u1", security=ResearchSecurityId.parse("CN", "600519"), title="", body="")
```

- [ ] **Step 2: Run the model tests and verify the missing module failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_models.py -q`

Expected: FAIL during collection because `app.services.stock_research.models` does not exist.

- [ ] **Step 3: Implement exact domain types and validation**

```python
@dataclass(frozen=True)
class ResearchSecurityId:
    market: Literal["A", "HK", "US"]
    code: str

    @classmethod
    def parse(cls, market: str, code: str) -> "ResearchSecurityId":
        canonical_market = "A" if market.upper() in {"A", "CN"} else market.upper()
        canonical_code = code.strip().upper()
        if canonical_market not in {"A", "HK", "US"} or not canonical_code:
            raise ResearchError("INVALID_SECURITY", "market and code are invalid")
        return cls(canonical_market, canonical_code)

    def __str__(self) -> str:
        return f"{self.market}:{self.code}"
```

Define dataclasses with `to_document()`/`from_document()` for the fields in spec section 6. `Entry.validate()` applies only these blocking rules: valid type/scope/security; note has non-blank title and body; decision has allowed action and ISO date; decision review has a decision reference when the user selected that review kind. Return missing non-critical source records as `warnings: tuple[str, ...]`, never as validation errors.

- [ ] **Step 4: Write failing repository tests for indexes, isolation, revisions, and soft deletion**

```python
@pytest.mark.asyncio
async def test_indexes_and_revision_numbers_are_user_scoped(fake_db):
    repo = StockResearchRepository(fake_db)
    await repo.ensure_indexes()
    first = await repo.append_revision("u1", "workspace", "A:600519", {"body": "v1"}, "manual")
    second = await repo.append_revision("u1", "workspace", "A:600519", {"body": "v2"}, "manual")
    assert (first.revision, second.revision) == (1, 2)
    assert ("user_id", "security_id") in fake_db["stock_research_workspaces"].unique_keys

@pytest.mark.asyncio
async def test_entry_reads_never_cross_users_and_delete_is_soft(fake_db):
    repo = StockResearchRepository(fake_db)
    await repo.insert_entry(make_note(user_id="u1", entry_id="e1"))
    assert await repo.get_entry("u2", "e1") is None
    await repo.soft_delete_entry("u1", "e1", now=NOW)
    assert await repo.get_entry("u1", "e1") is None
    assert (await repo.list_trash("u1", page=1, page_size=20)).items[0].id == "e1"
```

- [ ] **Step 5: Implement the local fake and repository primitives**

Implement a repository-local fake rather than importing production behavior from another test. It must support `create_index`, `find_one`, `insert_one`, `update_one`, `find_one_and_update`, `delete_one`, and filtered/sorted/paginated `find`; filters must cover `$in`, `$ne`, `$exists`, array membership, and deleted/archived timestamps.

Expose these exact public signatures on `StockResearchRepository`:

```text
ensure_indexes(self) -> None
get_workspace(self, user_id: str, security_id: str) -> Workspace | None
upsert_workspace(self, workspace: Workspace) -> Workspace
list_workspaces(self, user_id: str, query: WorkspaceQuery) -> ResearchPage[Workspace]
get_entry(self, user_id: str, entry_id: str, *, include_deleted: bool = False) -> Entry | None
insert_entry(self, entry: Entry) -> Entry
replace_entry(self, entry: Entry) -> Entry
list_entries(self, user_id: str, query: EntryQuery) -> ResearchPage[Entry]
append_revision(self, user_id: str, target_type: str, target_id: str, snapshot: dict[str, object], reason: str) -> Revision
list_revisions(self, user_id: str, target_type: str, target_id: str) -> list[Revision]
soft_delete_entry(self, user_id: str, entry_id: str, now: datetime) -> None
list_trash(self, user_id: str, page: int, page_size: int) -> ResearchPage[Entry]
restore_entry(self, user_id: str, entry_id: str) -> Entry
permanently_delete_entry(self, user_id: str, entry_id: str) -> None
```

`append_revision` uses a counter on the target document or a Mongo transaction-free atomic `$inc` plus the unique revision index. Add exact indexes: unique workspace `(user_id, security_id)`; unique entry `(user_id, id)`; entries `(user_id, security_ids, entry_type, deleted_at, updated_at)`; unique revision `(user_id, target_type, target_id, revision)`; unique generation task `(user_id, id)`; generation status `(user_id, status, created_at)`.

- [ ] **Step 6: Run Task 1 tests**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_models.py tests/unit/stock_research/test_storage.py -q`

Expected: PASS with no network or database service.

- [ ] **Step 7: Commit Task 1**

```bash
git add app/services/stock_research tests/unit/stock_research
git commit -m "feat(research): add research domain storage"
```

### Task 2: Workspace, Entry, Revision, and Trash Workflows

**Files:**
- Create: `app/services/stock_research/service.py`
- Create: `tests/unit/stock_research/test_service.py`
- Modify: `app/services/stock_research/__init__.py`

**Interfaces:**
- Consumes: `StockResearchRepository`, Task 1 domain records, and injected `clock: Callable[[], datetime]`/`id_factory: Callable[[], str]`.
- Produces: `StockResearchService` methods `get_or_create_workspace`, `save_thesis_draft`, `save_workspace_version`, `create_entry`, `update_entry_draft`, `convert_entry`, `confirm_entry`, `restore_revision`, `archive_entry`, `delete_entry`, `restore_entry`, and `permanently_delete_entry`.

- [ ] **Step 1: Write failing workflow tests**

```python
@pytest.mark.asyncio
async def test_autosave_does_not_create_revision_but_manual_save_does(service, repo):
    workspace = await service.get_or_create_workspace("u1", "CN", "600519", "贵州茅台")
    await service.save_thesis_draft("u1", workspace.security_id, ThesisPatch(body="draft"))
    assert await repo.list_revisions("u1", "workspace", workspace.security_id) == []
    revision = await service.save_workspace_version("u1", workspace.security_id, "首次论点")
    assert revision.revision == 1

@pytest.mark.asyncio
async def test_note_research_conversion_keeps_identity_and_body(service):
    note = await service.create_entry("u1", NewEntry.note("A:600519", "护城河", "正文"))
    research = await service.convert_entry("u1", note.id, "research", topic="竞争优势")
    assert (research.id, research.body, research.entry_type) == (note.id, "正文", "research")

@pytest.mark.asyncio
async def test_confirm_decision_freezes_thesis_and_cannot_be_autosaved(service):
    decision = await service.create_entry("u1", NewEntry.decision("A:600519", "buy", date(2026, 9, 8)))
    confirmed = await service.confirm_entry("u1", decision.id)
    assert confirmed.thesis_snapshot["security_id"] == "A:600519"
    with pytest.raises(ResearchError, match="formal entry"):
        await service.update_entry_draft("u1", decision.id, EntryPatch(body="overwrite"))
```

- [ ] **Step 2: Run the workflow tests and verify they fail**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_service.py -q`

Expected: FAIL because `StockResearchService` is not implemented.

- [ ] **Step 3: Implement service workflows and revision reasons**

Expose these exact public signatures on `StockResearchService`:

```text
get_workspace(self, user_id: str, security_id: str) -> Workspace
get_or_create_workspace(self, user_id: str, market: str, code: str, name: str) -> Workspace
save_thesis_draft(self, user_id: str, security_id: str, patch: ThesisPatch) -> Workspace
save_workspace_version(self, user_id: str, security_id: str, label: str) -> Revision
create_entry(self, user_id: str, request: NewEntry) -> Entry
update_entry_draft(self, user_id: str, entry_id: str, patch: EntryPatch) -> Entry
convert_entry(self, user_id: str, entry_id: str, target: Literal["note", "research"], *, topic: str | None = None) -> Entry
confirm_entry(self, user_id: str, entry_id: str) -> Entry
restore_revision(self, user_id: str, revision_id: str) -> Revision
apply_review_to_thesis(self, user_id: str, review_id: str, patch: ThesisPatch) -> Revision
archive_entry(self, user_id: str, entry_id: str) -> Entry
delete_entry(self, user_id: str, entry_id: str) -> None
restore_entry(self, user_id: str, entry_id: str) -> Entry
permanently_delete_entry(self, user_id: str, entry_id: str) -> None
set_decision_trade_links(self, user_id: str, decision_id: str, references: list[Reference]) -> Entry
get_decision_trade_links(self, user_id: str, decision_id: str) -> list[Reference]
```

Use revision reasons `manual`, `decision_confirmed`, `review_confirmed`, `review_applied_to_thesis`, and `revision_restored`. Confirming a decision snapshots the current thesis. Confirming a review creates an entry revision; later edits to a confirmed review create a new revision before replacing the current confirmed body. Restoring an old snapshot writes it as a new latest revision. Archive, soft delete, restore, and permanent delete affect only the research entry.

- [ ] **Step 4: Add negative tests for ownership and non-cascading deletion**

```python
@pytest.mark.asyncio
async def test_mutations_require_owner_and_delete_does_not_call_sources(service, source_spy):
    entry = await service.create_entry("u1", NewEntry.note("A:600519", "标题", "正文"))
    with pytest.raises(ResearchError, match="not found"):
        await service.archive_entry("u2", entry.id)
    await service.delete_entry("u1", entry.id)
    assert source_spy.calls == []
```

- [ ] **Step 5: Run Task 2 tests**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_service.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/services/stock_research tests/unit/stock_research/test_service.py
git commit -m "feat(research): add research document workflows"
```

### Task 3: Authenticated CRUD API and Application Registration

**Files:**
- Create: `app/routers/stock_research.py`
- Create: `tests/unit/test_stock_research_router.py`
- Modify: `app/main.py`
- Modify: `scripts/harness.py`

**Interfaces:**
- Consumes: `get_current_user`, `get_mongo_db`, `ok`, `StockResearchRepository`, and `StockResearchService`.
- Produces: authenticated workspace, entry, revision, and trash routes under `/api/research`.

- [ ] **Step 1: Write failing router contract tests with dependency overrides**

```python
def test_workspace_route_uses_authenticated_user(client, service_spy):
    response = client.get("/api/research/workspaces/A:600519")
    assert response.status_code == 200
    assert response.json()["data"]["security_id"] == "A:600519"
    assert service_spy.calls[0].user_id == "authenticated-user"

def test_invalid_entry_is_sanitized(client, service_spy):
    service_spy.error = ResearchError("INVALID_ENTRY", "decision action is required")
    response = client.post("/api/research/entries", json={"entry_type": "decision"})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "INVALID_ENTRY", "message": "decision action is required"}}
```

- [ ] **Step 2: Run the router tests and verify the missing-router failure**

Run: `python -m pytest -c tests/pytest.ini tests/unit/test_stock_research_router.py -q`

Expected: FAIL because `app.routers.stock_research` is absent.

- [ ] **Step 3: Implement request models, serializers, dependencies, and CRUD routes**

Implement this route table exactly:

```text
GET    /api/research/workspaces
POST   /api/research/workspaces
GET    /api/research/workspaces/{security_id}
PATCH  /api/research/workspaces/{security_id}
POST   /api/research/workspaces/{security_id}/revisions
GET    /api/research/entries
POST   /api/research/entries
GET    /api/research/entries/{entry_id}
PATCH  /api/research/entries/{entry_id}
POST   /api/research/entries/{entry_id}/convert
POST   /api/research/entries/{entry_id}/confirm
POST   /api/research/entries/{entry_id}/archive
DELETE /api/research/entries/{entry_id}
GET    /api/research/revisions
GET    /api/research/revisions/{revision_id}
POST   /api/research/revisions/{revision_id}/restore
GET    /api/research/trash
POST   /api/research/trash/{entry_id}/restore
DELETE /api/research/trash/{entry_id}
```

List parameters include `market`, `security_id`, `entry_type`, `status`, `query`, `real_holding`, `paper_holding`, `watchlisted`, `page`, and `page_size` where relevant. Return `404 RESEARCH_NOT_FOUND`, `409 RESEARCH_CONFLICT`, `422 INVALID_ENTRY`, and `503 RESEARCH_STORAGE_UNAVAILABLE`; log exception class only for unknown failures and return a generic `500 INTERNAL_ERROR`.

- [ ] **Step 4: Register router, indexes, and the quick-test selection**

In `app/main.py`, import the router, call `await StockResearchRepository(db).ensure_indexes()` beside existing startup index creation, and include it with `app.include_router(stock_research.router, prefix="/api")`. In `scripts/harness.py`, append `tests/unit/stock_research` and `tests/unit/test_stock_research_router.py` to the existing service-free pytest selection without broadening to all tests.

- [ ] **Step 5: Run the API and existing quick tests**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research tests/unit/test_stock_research_router.py -q`

Expected: PASS without MongoDB because dependencies are overridden and repository tests use the fake.

Run: `python scripts/harness.py --structural-only`

Expected: PASS under Python 3.11. If the active interpreter is older, record the interpreter mismatch and do not treat `ModuleNotFoundError: tomllib` as a product failure.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/routers/stock_research.py app/main.py scripts/harness.py tests/unit/test_stock_research_router.py
git commit -m "feat(research): expose research document API"
```

### Task 4: Source References and Decision-to-Trade Links

**Files:**
- Create: `app/services/stock_research/references.py`
- Create: `tests/unit/stock_research/test_references.py`
- Modify: `app/services/stock_research/storage.py`
- Modify: `app/services/stock_research/service.py`
- Modify: `app/routers/stock_research.py`
- Modify: `tests/unit/test_stock_research_router.py`

**Interfaces:**
- Consumes: exact authenticated `user_id`, existing analysis-report collection access, `RealPortfolioService`, paper trade collection adapter, and Task 1 `Reference`.
- Produces: `ReferenceService.list_candidates`, `ReferenceService.recommend_trade_links`, `StockResearchService.set_decision_trade_links`, `/references`, and `/links` routes.

- [ ] **Step 1: Write failing tests for source scoping and account separation**

```python
@pytest.mark.asyncio
async def test_candidates_are_owned_and_keep_real_and_paper_separate(reference_service):
    items = await reference_service.list_candidates("u1", "A:600519", date(2026, 8, 1), date(2026, 9, 8))
    assert {item.user_id for item in items} == {"u1"}
    assert {(item.kind, item.account_type) for item in items} >= {
        ("real_trade", "real"), ("paper_trade", "paper")
    }

@pytest.mark.asyncio
async def test_missing_source_keeps_snapshot_warning(reference_service):
    resolved = await reference_service.resolve("u1", Reference(kind="analysis_report", source_id="gone", label="旧报告"))
    assert resolved.available is False
    assert resolved.label == "旧报告"
```

- [ ] **Step 2: Run the reference tests and verify they fail**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_references.py -q`

Expected: FAIL because `ReferenceService` is missing.

- [ ] **Step 3: Implement read-only source adapters and recommendations**

Expose these exact public signatures on `ReferenceService`:

```text
list_candidates(self, user_id: str, security_id: str, date_from: date | None, date_through: date | None) -> list[ReferenceCandidate]
resolve(self, user_id: str, reference: Reference) -> ResolvedReference
recommend_trade_links(self, user_id: str, decision_id: str) -> list[ReferenceCandidate]
```

Query analysis reports with exact `user_id`; read real positions/trades through `RealPortfolioService`; read paper trades through a small read-only adapter under `app/`; emit holding-date references as snapshots. Recommend trades by matching security and a bounded date window around the decision, then sort by absolute date distance. Never update source documents.

- [ ] **Step 4: Enforce one-decision-per-trade while permitting unlinked trades**

```python
@pytest.mark.asyncio
async def test_one_trade_links_to_at_most_one_decision(service):
    await service.set_decision_trade_links("u1", "d1", [Reference.real_trade("t1")])
    with pytest.raises(ResearchError, match="already linked"):
        await service.set_decision_trade_links("u1", "d2", [Reference.real_trade("t1")])
    await service.set_decision_trade_links("u1", "d1", [])
    assert await service.get_decision_trade_links("u1", "d1") == []
```

Store confirmed links on the decision entry and enforce unique `(user_id, reference.kind, reference.account_type, reference.source_id)` using a dedicated normalized `trade_link_keys` array with a unique multikey index. Multiple trades may reference one decision; deleting the link leaves the source trade untouched.

- [ ] **Step 5: Add and test reference/link routes**

```text
GET    /api/research/references?security_id=A:600519&date_from=2026-08-01&date_through=2026-09-08
GET    /api/research/links/recommendations?decision_id=d1
PUT    /api/research/links/decisions/d1
DELETE /api/research/links/decisions/d1/{kind}/{source_id}
```

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_references.py tests/unit/stock_research/test_storage.py tests/unit/test_stock_research_router.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add app/services/stock_research app/routers/stock_research.py tests/unit/stock_research tests/unit/test_stock_research_router.py
git commit -m "feat(research): connect research references and trades"
```

### Task 5: Typed Frontend API, Routes, Navigation, and Research Directory

**Files:**
- Create: `frontend/src/api/stockResearch.ts`
- Create: `frontend/src/views/Research/index.vue`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/components/Layout/SidebarMenu.vue`

**Interfaces:**
- Consumes: Task 3 API envelopes through existing `ApiClient` and Task 4 holding/reference summary fields.
- Produces: `stockResearchApi`, `/research`, lazy-loaded workspace route, and a directory that navigates with explicit market query.

- [ ] **Step 1: Define exact TypeScript contracts and API methods**

```ts
export type ResearchMarket = 'CN' | 'HK' | 'US'
export type ResearchEntryType = 'note' | 'research' | 'decision' | 'review'
export type DecisionAction = 'buy' | 'add' | 'reduce' | 'sell' | 'observe'
export type ReviewKind = 'routine' | 'decision'

export interface ResearchWorkspaceSummary {
  security_id: string
  market: ResearchMarket
  code: string
  name: string
  thesis_summary: string
  updated_at: string
  latest_entry_type: ResearchEntryType | null
  latest_entry_at: string | null
  has_real_holding: boolean
  has_paper_holding: boolean
  watchlisted: boolean
}

export const stockResearchApi = {
  listWorkspaces: (query: WorkspaceQuery) => ApiClient.get<ResearchPage<ResearchWorkspaceSummary>>('/api/research/workspaces', query),
  createWorkspace: (input: CreateWorkspaceInput) => ApiClient.post<ResearchWorkspace>('/api/research/workspaces', input),
  getWorkspace: (securityId: string) => ApiClient.get<ResearchWorkspace>(`/api/research/workspaces/${encodeURIComponent(securityId)}`),
  createEntry: (input: CreateEntryInput) => ApiClient.post<ResearchEntry>('/api/research/entries', input)
}
```

Add all Task 3 methods in the same object: workspace patch/version, entry list/get/patch/convert/confirm/archive/delete, revision list/get/restore, and trash list/restore/delete. Add Task 4 reference and link methods. Do not create a second Axios instance.

- [ ] **Step 2: Add route records and sidebar navigation**

```ts
{
  path: '/research',
  name: 'ResearchDirectory',
  component: () => import('@/views/Research/index.vue'),
  meta: { title: '研究与复盘', requiresAuth: true }
},
{
  path: '/research/:code',
  name: 'ResearchWorkspace',
  component: () => import('@/views/Research/Workspace.vue'),
  meta: { title: '个股研究', requiresAuth: true }
}
```

Use the existing Element Plus icon set in `SidebarMenu.vue`; label the item `研究与复盘` and route it to `/research`.

- [ ] **Step 3: Build the directory with search and explicit filters**

The directory renders one row per security with name/code/market, thesis summary, last update, last entry, distinct `真实持仓` and `模拟持仓` tags, and a watchlist indicator. Bind search plus market/real/paper/watchlist controls to `listWorkspaces`. Clicking a row navigates using:

```ts
router.push({ name: 'ResearchWorkspace', params: { code: item.code }, query: { market: item.market } })
```

The primary action creates/opens a workspace. `新建例行复盘` creates a portfolio-scoped draft with default scope metadata `{ include_market: true, include_real_holdings: true, include_paper_holdings: false }` and lets the user adjust it before save.

- [ ] **Step 4: Build and inspect route chunks**

Run: `npm --prefix frontend run bundle`

Expected: Vite succeeds and emits chunks containing both research views. Existing Sass or bundle-size warnings are non-blocking.

- [ ] **Step 5: Commit Task 5**

```bash
git add frontend/src/api/stockResearch.ts frontend/src/views/Research/index.vue frontend/src/router/index.ts frontend/src/components/Layout/SidebarMenu.vue
git commit -m "feat(research): add research directory navigation"
```

### Task 6: Document-First Workspace, Autosave, Versions, and Trash

**Files:**
- Create: `frontend/src/views/Research/Workspace.vue`
- Create: `frontend/src/components/Research/ResearchMarkdownEditor.vue`
- Create: `frontend/src/components/Research/ResearchEntryList.vue`
- Create: `frontend/src/composables/useResearchAutosave.ts`
- Modify: `frontend/src/api/stockResearch.ts`

**Interfaces:**
- Consumes: route `params.code`, required `query.market`, and Task 5 API methods.
- Produces: thesis/note/research workspace, reliable debounced autosave, manual revisions, conversion, archive, trash, and responsive navigation.

- [ ] **Step 1: Implement serialized autosave with stale-response protection**

```ts
export function useResearchAutosave<T>(save: (value: T) => Promise<T>, delayMs = 1500) {
  const state = ref<'saved' | 'dirty' | 'saving' | 'failed'>('saved')
  let requested = 0
  let completed = 0
  let latest: T | null = null

  async function flush() {
    if (!latest || completed === requested) return
    const version = requested
    const value = latest
    state.value = 'saving'
    try {
      await save(value)
      completed = version
      state.value = completed === requested ? 'saved' : 'dirty'
      if (completed !== requested) await flush()
    } catch {
      state.value = 'failed'
    }
  }

  return { state, schedule, flush, retry: flush }
}
```

`schedule(value)` stores the latest value, increments `requested`, sets `dirty`, and resets a `delayMs` timer. Flush on editor change debounce and before route/record changes. A failed save retains `latest` and exposes a visible retry command.

- [ ] **Step 2: Build the Markdown editor and stable layout**

`ResearchMarkdownEditor.vue` provides edit/preview segmented controls, a textarea editor, rendered Markdown preview using the already-installed `marked`, and save state (`已保存`, `保存中`, `未保存`, `保存失败`). Use a fixed toolbar height and responsive width constraints so mode changes do not shift the page. Render external links safely and disable raw HTML in Markdown.

- [ ] **Step 3: Build workspace section navigation and thesis editing**

Require `market` from the query; if absent or invalid, show a market selector rather than guessing. Resolve `security_id`, load/create the workspace, and show top actions `返回行情`, `保存版本`, and `新建`. Desktop navigation is left-side; narrow screens use a top tab/menu. Use the exact sections `当前论点`, `笔记`, `调研`, `决策`, `复盘`.

The thesis editor includes Markdown body plus editable lists for assumptions, risks, invalidation conditions, open questions, tags, and external links. Autosave calls `patchWorkspace`; `保存版本` requests an optional short label and calls the revision endpoint.

- [ ] **Step 4: Add note/research lists, conversion, versions, and trash**

Notes require title and body; research adds optional topic, references, and conclusion. Convert with one API call and preserve entry ID/body. List revisions as read-only snapshots; restoring asks for confirmation and creates a new revision. Archive remains discoverable through status filters. Delete moves to trash; trash supports restore and explicit permanent delete, with no automatic expiry copy or behavior.

- [ ] **Step 5: Build and manually smoke the save states**

Run: `npm --prefix frontend run bundle`

Expected: PASS.

Start the existing development stack per `docs/development.md`, then use Playwright to verify at desktop `1440x900` and mobile `390x844`: navigation does not overlap the editor; edit/preview works; rapid edits persist the last value; a rejected save shows `保存失败` and retry; save-version adds a revision; note-to-research conversion preserves the body; trash restore returns the entry.

- [ ] **Step 6: Commit Task 6**

```bash
git add frontend/src/views/Research/Workspace.vue frontend/src/components/Research frontend/src/composables/useResearchAutosave.ts frontend/src/api/stockResearch.ts
git commit -m "feat(research): add document research workspace"
```

### Task 7: Decisions and On-Demand Reviews

**Files:**
- Create: `frontend/src/components/Research/ResearchReferencePicker.vue`
- Create: `frontend/src/components/Research/DecisionEditor.vue`
- Create: `frontend/src/components/Research/ReviewEditor.vue`
- Modify: `frontend/src/views/Research/Workspace.vue`
- Modify: `frontend/src/views/Research/index.vue`
- Modify: `frontend/src/api/stockResearch.ts`
- Modify: `app/services/stock_research/service.py`
- Modify: `tests/unit/stock_research/test_service.py`

**Interfaces:**
- Consumes: Task 2 confirmation/application workflows, Task 4 references/links, and Task 6 editors.
- Produces: human-confirmed decisions, manually created routine/decision reviews, formal review revisions, and explicit thesis-diff application.

- [ ] **Step 1: Add failing service tests for both review kinds and thesis application**

```python
@pytest.mark.asyncio
async def test_routine_review_needs_no_decision(service):
    review = await service.create_entry("u1", NewEntry.routine_review(scope="portfolio", body="收盘复盘"))
    assert review.review_kind == "routine"
    assert review.decision_id is None

@pytest.mark.asyncio
async def test_review_does_not_change_thesis_until_explicit_apply(service):
    review = await service.create_entry("u1", NewEntry.routine_review(scope="stock", security_id="A:600519", body="失效条件改变"))
    await service.confirm_entry("u1", review.id)
    assert (await service.get_workspace("u1", "A:600519")).body == "旧论点"
    revision = await service.apply_review_to_thesis("u1", review.id, ThesisPatch(body="新论点"))
    assert revision.reason == "review_applied_to_thesis"
```

- [ ] **Step 2: Run and then satisfy review-domain tests**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_service.py -q`

Expected before implementation: FAIL on missing review factory/application behavior. Expected after implementing the exact behaviors above: PASS.

- [ ] **Step 3: Build the decision editor with a low-friction confirmation boundary**

Use an action selector for `买入`, `加仓`, `减仓`, `卖出`, `继续观察` and a date input as the only required controls. Body, planned price, target allocation, horizon, references, and recommended real/paper trade links remain optional. `确认决策` shows the current thesis snapshot summary and creates a formal decision; after confirmation the editor becomes read-only and offers `创建新决策`, never autosave overwrite.

- [ ] **Step 4: Build on-demand review creation and concise templates**

Create no review automatically. The user chooses `例行复盘` or `决策复盘`; decision review may select a decision and its related facts. Use these four Markdown headings for both the initial human template and AI prompt structure:

```markdown
## 市场与持仓表现

## 重要事实、公告和调研变化

## 当前论点变化

## 后续观察
```

From `/research`, routine review defaults to market plus real holdings. From a stock workspace, it defaults to that security. Keep market, holdings, funds, global markets, limit-up concepts, and financial metrics optional. Confirmation creates a formal revision. Editing a formal review creates another revision. Do not display reminder, due-date, completion, or overdue controls.

- [ ] **Step 5: Implement explicit review-to-thesis diff confirmation**

`应用到当前论点` opens a side-by-side or inline diff of current thesis and the proposed patch. Only the confirm command calls `applyReviewToThesis`; cancel performs no write. On success refresh both thesis and revision list.

- [ ] **Step 6: Verify backend, bundle, and workflows**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_service.py tests/unit/stock_research/test_references.py -q`

Expected: PASS.

Run: `npm --prefix frontend run bundle`

Expected: PASS.

Playwright smoke: confirm one minimal decision with only action/date; create a routine review without a decision; create a decision review with references; verify confirmation adds revisions; verify a review does not change the thesis until the diff is explicitly accepted; verify real and paper references use distinct labels.

- [ ] **Step 7: Commit Task 7**

```bash
git add app/services/stock_research/service.py tests/unit/stock_research/test_service.py frontend/src/components/Research frontend/src/views/Research frontend/src/api/stockResearch.ts
git commit -m "feat(research): add decisions and manual reviews"
```

### Task 8: Persisted AI Draft Generation Tasks

**Files:**
- Create: `app/services/stock_research/prompts.py`
- Create: `app/services/stock_research/generation.py`
- Create: `tests/unit/stock_research/test_generation.py`
- Modify: `app/services/stock_research/storage.py`
- Modify: `app/routers/stock_research.py`
- Modify: `tests/unit/test_stock_research_router.py`

**Interfaces:**
- Consumes: configured `LLMConfig`, `create_llm_by_provider`, user OAuth resolution used by `AnalysisService._inject_oauth_token_if_needed_async`, Task 4 reference snapshots, and Task 1 generation-task storage.
- Produces: `ResearchTextGenerator`, `ConfiguredResearchTextGenerator`, `ResearchGenerationService`, prompt version `research-draft-v1`, and generation-task POST/GET routes.

- [ ] **Step 1: Write failing generation tests with an injected fake generator**

```python
@pytest.mark.asyncio
async def test_task_freezes_context_and_records_model_metadata(generation_service, generator, repo):
    task = await generation_service.submit(
        user_id="u1", target_entry_id="e1", draft_kind="review",
        provider="openai", model_name="gpt-5", reasoning_effort="high",
        references=[Reference.analysis_report("r1", "报告")],
    )
    await generation_service.run(task.id, "u1")
    stored = await repo.get_generation_task("u1", task.id)
    assert stored.status == "completed"
    assert stored.model_name == "gpt-5"
    assert stored.reasoning_effort == "high"
    assert stored.prompt_version == "research-draft-v1"
    assert stored.context_snapshot["references"][0]["source_id"] == "r1"

@pytest.mark.asyncio
async def test_failure_does_not_create_draft_or_touch_human_body(generation_service, generator, repo):
    generator.error = RuntimeError("provider unavailable")
    task = await generation_service.submit(user_id="u1", target_entry_id="e1", draft_kind="note", provider="openai", model_name="gpt-5", reasoning_effort=None, references=[])
    await generation_service.run(task.id, "u1")
    assert (await repo.get_generation_task("u1", task.id)).status == "failed"
    assert (await repo.get_entry("u1", "e1")).body == "human body"
    assert (await repo.get_entry("u1", "e1")).ai_drafts == []
```

- [ ] **Step 2: Run the generation tests and verify they fail**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_generation.py -q`

Expected: FAIL because generation orchestration is absent.

- [ ] **Step 3: Implement versioned prompts and the generator protocol**

```python
class ResearchTextGenerator(Protocol):
    async def generate(
        self, *, user_id: str, provider: str, model_name: str,
        reasoning_effort: str | None, system_prompt: str, user_prompt: str,
    ) -> str:
        raise NotImplementedError

PROMPT_VERSION = "research-draft-v1"
```

Build deterministic prompts from the target kind, current thesis snapshot, selected source snapshots, and the concise four-section review template. `ConfiguredResearchTextGenerator` resolves the exact selected model and user OAuth token using existing helpers, passes reasoning effort only when supported by the provider adapter, and raises a sanitized failure if unavailable. It must not select a fallback model and must not request or persist hidden reasoning text.

- [ ] **Step 4: Implement task lifecycle and immutable AI draft attachment**

Expose these exact public signatures on `ResearchGenerationService`:

```text
submit(self, *, user_id: str, target_entry_id: str, draft_kind: str, provider: str, model_name: str, reasoning_effort: str | None, references: list[Reference]) -> GenerationTask
run(self, task_id: str, user_id: str) -> None
get(self, user_id: str, task_id: str) -> GenerationTask
```

At submission, resolve and freeze only necessary inputs and reference display snapshots. Persist `pending`, transition to `running`, then either atomically append immutable `{content, provider, model_name, reasoning_effort, generated_at, prompt_version, source_ids, references, task_id}` to `entry.ai_drafts` and mark `completed`, or store sanitized `error_code`/`error_message` and mark `failed`. Human `body` is never part of the task update. No restart recovery or separate worker service is added in this iteration.

- [ ] **Step 5: Expose generation task routes using the existing process model**

```text
POST /api/research/generation-tasks
GET  /api/research/generation-tasks/{task_id}
```

POST persists the task before scheduling `asyncio.create_task(service.run(task.id, user_id))`, then returns `202` with the task. GET is user-scoped. Add router tests proving another user receives `404`, invalid provider/model errors are sanitized, and a failed task does not return generated content.

- [ ] **Step 6: Run Task 8 tests**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research/test_generation.py tests/unit/test_stock_research_router.py -q`

Expected: PASS using injected fakes and no LLM/network call.

- [ ] **Step 7: Commit Task 8**

```bash
git add app/services/stock_research app/routers/stock_research.py tests/unit/stock_research/test_generation.py tests/unit/test_stock_research_router.py
git commit -m "feat(research): add traceable AI draft tasks"
```

### Task 9: AI Generation UI, Stock Detail Entry Point, Documentation, and End-to-End Verification

**Files:**
- Create: `frontend/src/components/Research/GenerationDialog.vue`
- Modify: `frontend/src/components/Research/ResearchMarkdownEditor.vue`
- Modify: `frontend/src/components/Research/DecisionEditor.vue`
- Modify: `frontend/src/components/Research/ReviewEditor.vue`
- Modify: `frontend/src/views/Research/Workspace.vue`
- Modify: `frontend/src/views/Stocks/Detail.vue`
- Modify: `frontend/src/api/stockResearch.ts`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/testing.md`

**Interfaces:**
- Consumes: Task 8 task API and existing configured-model options exposed by the application's current settings/config API.
- Produces: user-controlled AI draft generation, immutable-original/human-edit separation, stock-detail navigation, documented boundaries, and final smoke evidence.

- [ ] **Step 1: Add generation API contracts and dialog**

```ts
export interface GenerationTask {
  id: string
  target_entry_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  provider: string
  model_name: string
  reasoning_effort: string | null
  prompt_version: string
  source_ids: string[]
  references: ResearchReference[]
  generated_at: string | null
  content: string | null
  error_message: string | null
}
```

`GenerationDialog.vue` loads configured models, defaults to the user's last selection in local preferences, and lets the user change provider/model, reasoning effort, and preselected references. Submit once, poll GET with bounded two-second polling while the dialog is open, stop on completed/failed/unmount, and offer explicit retry by submitting a new task. Never silently change the selection.

- [ ] **Step 2: Keep original AI draft separate from human text**

On success, show a read-only `AI 原始草稿` panel with provider, model name, reasoning effort, generation time, prompt version, task/source IDs, and references. `采用到正文` copies content into the independently editable human body only after confirmation; it does not mutate the stored original. Hide any model-ranking, score, win-rate, outcome-attribution, or edit-distance UI.

- [ ] **Step 3: Link stock detail to the workspace**

In `frontend/src/views/Stocks/Detail.vue`, add a `研究工作区` command beside existing header actions. Navigate with the page's explicit current market and code. Fetch a lightweight workspace summary to show `最近研究：YYYY-MM-DD HH:mm`; when no workspace exists show `尚未建立研究` without auto-creating one.

- [ ] **Step 4: Document ownership and testing boundaries**

Add to `ARCHITECTURE.md`: research owns its four collections and revisions/links, reads but never mutates analysis/portfolio/paper sources, uses in-process persisted generation tasks without restart recovery, and keeps real/paper facts separate. Add to `docs/testing.md`: the exact service-free research pytest paths; `npm --prefix frontend run bundle`; Playwright viewport/workflow checks; and an explicitly opt-in live-model smoke command that requires configured credentials.

- [ ] **Step 5: Run focused and repository verification**

Run: `python -m pytest -c tests/pytest.ini tests/unit/stock_research tests/unit/test_stock_research_router.py -q`

Expected: PASS.

Run: `npm --prefix frontend run bundle`

Expected: PASS with only documented existing warnings.

Run: `python scripts/harness.py`

Expected: under Python 3.11 with installed frontend dependencies, compilation passes, the service-free suite including research passes, and Vite builds. Do not invoke opt-in MongoDB, Redis, provider, OAuth, or LLM tests as part of this gate.

- [ ] **Step 6: Run the Playwright smoke matrix**

At `1440x900` and `390x844`, verify: research directory search/filter; stock-detail navigation with explicit market; thesis autosave/retry/manual version; note/research conversion; minimal decision confirmation and thesis snapshot; routine review without decision; decision review with real/paper references visibly distinct; review confirmation/application diff; trash restore/delete; AI model/reasoning/reference selection; failed generation preserving human text; completed generation retaining original metadata; and no overlapping/clipped controls. Capture screenshots for the directory, workspace editor, decision confirmation, review diff, and AI original-draft metadata.

- [ ] **Step 7: Inspect scope and commit final integration**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: only the files named in this plan are staged for these task commits; preserve unrelated pre-existing changes.

```bash
git add frontend/src/api/stockResearch.ts frontend/src/components/Research frontend/src/views/Research/Workspace.vue frontend/src/views/Stocks/Detail.vue ARCHITECTURE.md docs/testing.md
git commit -m "feat(research): complete research and review workspace"
```

## Final Acceptance Checklist

- [ ] `/research` is a directory with search and market/real/paper/watchlist filters, not a dashboard or automatic timeline.
- [ ] `/research/:code?market=...` is document-first and keeps `/stocks/:code` focused on market detail.
- [ ] Thesis autosave creates no revision; all five specified explicit events create revisions.
- [ ] Notes and research convert in place; decisions require only action/date; formal decisions cannot be autosave-overwritten.
- [ ] Reviews are created only on demand and can be routine or decision reviews; no reminder/task/completion model exists.
- [ ] Review confirmation does not silently rewrite the thesis; applying a reviewed change requires a visible diff and confirmation.
- [ ] References remain user-scoped, tolerate unavailable sources, never own source data, and label real/paper facts separately.
- [ ] One trade links to at most one decision; unlinked trades remain valid and available to routine reviews.
- [ ] AI generation records provider/model/reasoning effort/time/prompt/source metadata, keeps original and human text separate, and stores no hidden chain-of-thought or model-effectiveness metrics.
- [ ] Soft deletion, restore, permanent deletion, archive, version view, and restore-as-new-version work without source-data cascades.
- [ ] No `../stock` data migration, daily market refresh, new scheduler, attachment subsystem, or broker execution connection was introduced.
- [ ] Focused Python tests, frontend bundle, Python 3.11 harness, and desktop/mobile Playwright smoke checks have recorded passing evidence before merge.
