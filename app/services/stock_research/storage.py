from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    Entry,
    EntryQuery,
    GenerationTask,
    Reference,
    ResearchPage,
    ResearchSecurityId,
    Revision,
    Workspace,
    WorkspaceQuery,
)


MAX_PAGE_SIZE = 200


def _canonical_security_id(security_id: str) -> str:
    return str(ResearchSecurityId.from_string(security_id))


def _canonical_market(market: str) -> str:
    return ResearchSecurityId.parse(market, "_").market


def _validate_pagination(page: int, page_size: int) -> None:
    if page < 1 or page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ResearchError(
            "INVALID_QUERY",
            f"pagination requires page >= 1 and page_size <= {MAX_PAGE_SIZE}",
        )


def _decision_trade_link_keys(references: tuple[Reference, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            reference.trade_link_key()
            for reference in references
            if reference.kind in {"real_trade", "paper_trade"}
        )
    )


def _with_derived_trade_link_keys(entry: Entry) -> Entry:
    keys = (
        _decision_trade_link_keys(entry.references)
        if entry.entry_type == "decision"
        else ()
    )
    return replace(entry, trade_link_keys=keys)


def _raise_trade_link_conflict(error: DuplicateKeyError) -> None:
    details = error.details or {}
    key_pattern = details.get("keyPattern", {})
    if "trade_link_keys" in key_pattern or "trade_link_keys" in str(error):
        raise ResearchError(
            "RESEARCH_CONFLICT", "trade is already linked to another decision"
        ) from None
    raise error


class StockResearchRepository:
    def __init__(self, db) -> None:
        self.db = db

    def _collection(self, name: str):
        return self.db[f"stock_research_{name}"]

    async def ensure_indexes(self) -> None:
        await self._collection("workspaces").create_index(
            [("user_id", ASCENDING), ("security_id", ASCENDING)],
            name="workspace_identity",
            unique=True,
        )
        entries = self._collection("entries")
        await entries.create_index(
            [("user_id", ASCENDING), ("id", ASCENDING)],
            name="entry_identity",
            unique=True,
        )
        await entries.create_index(
            [
                ("user_id", ASCENDING),
                ("security_ids", ASCENDING),
                ("entry_type", ASCENDING),
                ("deleted_at", ASCENDING),
                ("updated_at", DESCENDING),
            ],
            name="entry_listing",
        )
        await entries.create_index(
            [("user_id", ASCENDING), ("trade_link_keys", ASCENDING)],
            name="decision_trade_identity",
            unique=True,
            partialFilterExpression={"trade_link_keys.0": {"$exists": True}},
        )
        await self._collection("revisions").create_index(
            [
                ("user_id", ASCENDING),
                ("target_type", ASCENDING),
                ("target_id", ASCENDING),
                ("revision", ASCENDING),
            ],
            name="revision_identity",
            unique=True,
        )
        generation_tasks = self._collection("generation_tasks")
        await generation_tasks.create_index(
            [("user_id", ASCENDING), ("id", ASCENDING)],
            name="generation_task_identity",
            unique=True,
        )
        await generation_tasks.create_index(
            [
                ("user_id", ASCENDING),
                ("status", ASCENDING),
                ("created_at", ASCENDING),
            ],
            name="generation_task_status",
        )

    async def get_workspace(
        self, user_id: str, security_id: str
    ) -> Workspace | None:
        document = await self._collection("workspaces").find_one(
            {
                "user_id": user_id,
                "security_id": _canonical_security_id(security_id),
            }
        )
        return Workspace.from_document(document) if document is not None else None

    async def upsert_workspace(self, workspace: Workspace) -> Workspace:
        document = workspace.to_document()
        await self._collection("workspaces").update_one(
            {"user_id": document["user_id"], "security_id": document["security_id"]},
            {"$set": document},
            upsert=True,
        )
        return Workspace.from_document(document)

    async def list_workspaces(
        self, user_id: str, query: WorkspaceQuery
    ) -> ResearchPage[Workspace]:
        _validate_pagination(query.page, query.page_size)
        mongo_query = self._workspace_query(user_id, query)
        collection = self._collection("workspaces")
        total = await collection.count_documents(mongo_query)
        documents = (
            await collection.find(mongo_query)
            .sort([("updated_at", DESCENDING), ("security_id", ASCENDING)])
            .skip((query.page - 1) * query.page_size)
            .limit(query.page_size)
            .to_list(length=query.page_size)
        )
        return ResearchPage(
            tuple(Workspace.from_document(document) for document in documents),
            query.page,
            query.page_size,
            total,
        )

    async def list_workspace_candidates(
        self, user_id: str, query: WorkspaceQuery
    ) -> tuple[Workspace, ...]:
        _validate_pagination(query.page, query.page_size)
        documents = (
            await self._collection("workspaces")
            .find(self._workspace_query(user_id, query))
            .sort([("updated_at", DESCENDING), ("security_id", ASCENDING)])
            .to_list(length=None)
        )
        return tuple(Workspace.from_document(document) for document in documents)

    @staticmethod
    def _workspace_query(user_id: str, query: WorkspaceQuery) -> dict[str, object]:
        mongo_query: dict[str, object] = {"user_id": user_id}
        if query.market is not None:
            mongo_query["market"] = _canonical_market(query.market)
        if query.query and query.query.strip():
            pattern = re.escape(query.query.strip())
            mongo_query["$or"] = [
                {"code": {"$regex": pattern, "$options": "i"}},
                {"name": {"$regex": pattern, "$options": "i"}},
                {"body": {"$regex": pattern, "$options": "i"}},
            ]
        return mongo_query

    async def get_entry(
        self,
        user_id: str,
        entry_id: str,
        *,
        include_deleted: bool = False,
    ) -> Entry | None:
        query: dict[str, object] = {"user_id": user_id, "id": entry_id}
        if not include_deleted:
            query["deleted_at"] = None
        document = await self._collection("entries").find_one(query)
        return Entry.from_document(document) if document is not None else None

    async def insert_entry(self, entry: Entry) -> Entry:
        entry = _with_derived_trade_link_keys(entry)
        entry.validate()
        document = entry.to_document()
        try:
            await self._collection("entries").insert_one(document)
        except DuplicateKeyError as error:
            _raise_trade_link_conflict(error)
        return Entry.from_document(document)

    async def replace_entry(self, entry: Entry) -> Entry:
        entry = _with_derived_trade_link_keys(entry)
        entry.validate()
        document = entry.to_document()
        # Originals belong to the generation append path, never a human save.
        document.pop("ai_drafts", None)
        try:
            result = await self._collection("entries").update_one(
                {"user_id": entry.user_id, "id": entry.id},
                {"$set": document},
            )
        except DuplicateKeyError as error:
            _raise_trade_link_conflict(error)
        if result.matched_count == 0:
            raise ResearchError("RESEARCH_NOT_FOUND", "research entry not found")
        return await self.get_entry(entry.user_id, entry.id, include_deleted=True)

    async def insert_generation_task(self, task: GenerationTask) -> GenerationTask:
        document = task.to_document()
        await self._collection("generation_tasks").insert_one(document)
        return GenerationTask.from_document(document)

    async def get_generation_task(self, user_id: str, task_id: str) -> GenerationTask | None:
        document = await self._collection("generation_tasks").find_one({"user_id": user_id, "id": task_id})
        return GenerationTask.from_document(document) if document else None

    async def claim_generation_task(self, user_id: str, task_id: str) -> GenerationTask | None:
        document = await self._collection("generation_tasks").find_one_and_update(
            {"user_id": user_id, "id": task_id, "status": "pending"},
            {"$set": {"status": "running", "updated_at": datetime.now(UTC).isoformat()}},
            return_document=ReturnDocument.AFTER,
        )
        return GenerationTask.from_document(document) if document else None

    async def complete_generation_task(self, task: GenerationTask, content: str, generated_at: datetime) -> None:
        timestamp = generated_at.isoformat()
        draft = {
            "content": content, "provider": task.provider, "model_name": task.model_name,
            "reasoning_effort": task.reasoning_effort, "generated_at": timestamp,
            "prompt_version": task.prompt_version, "source_ids": list(task.source_ids),
            "references": task.context_snapshot["references"], "task_id": task.id,
        }
        entries = self._collection("entries")
        target = {"user_id": task.user_id, "id": task.target_entry_id}
        result = await entries.update_one(
            {**target, "deleted_at": None, "status": "draft"}, {"$push": {"ai_drafts": draft}},
        )
        if not result.matched_count:
            raise ResearchError("RESEARCH_CONFLICT", "generation target is no longer editable")
        try:
            result = await self._collection("generation_tasks").update_one(
                {"user_id": task.user_id, "id": task.id, "status": "running"},
                {"$set": {"status": "completed", "content": content, "generated_at": timestamp, "updated_at": timestamp}},
            )
            if not result.matched_count:
                raise ResearchError("RESEARCH_CONFLICT", "generation task is no longer running")
        except BaseException:
            # Two collections on standalone Mongo: undo only this task's append
            # if completion fails. Process crashes/restart recovery are out of scope.
            await entries.update_one(target, {"$pull": {"ai_drafts": {"task_id": task.id}}})
            raise

    async def fail_generation_task(self, user_id: str, task_id: str, error_code: str, error_message: str) -> None:
        await self._collection("generation_tasks").update_one(
            {"user_id": user_id, "id": task_id, "status": "running"},
            {"$set": {"status": "failed", "content": None, "error_code": error_code,
                      "error_message": error_message, "updated_at": datetime.now(UTC).isoformat()}},
        )

    async def replace_decision_trade_links(
        self,
        user_id: str,
        decision_id: str,
        references: tuple[Reference, ...],
        now: datetime,
    ) -> Entry:
        trade_link_keys = _decision_trade_link_keys(references)
        try:
            document = await self._collection("entries").find_one_and_update(
                {
                    "user_id": user_id,
                    "id": decision_id,
                    "entry_type": "decision",
                    "deleted_at": None,
                },
                [
                    {
                        "$set": {
                            "references": {
                                "$concatArrays": [
                                    {
                                        "$filter": {
                                            "input": {
                                                "$ifNull": ["$references", []]
                                            },
                                            "as": "reference",
                                            "cond": {
                                                "$not": [
                                                    {
                                                        "$in": [
                                                            "$$reference.kind",
                                                            [
                                                                "real_trade",
                                                                "paper_trade",
                                                            ],
                                                        ]
                                                    }
                                                ]
                                            },
                                        }
                                    },
                                    {
                                        "$literal": [
                                            item.to_document()
                                            for item in references
                                        ]
                                    },
                                ]
                            },
                            "trade_link_keys": list(trade_link_keys),
                            "updated_at": now.isoformat(),
                        }
                    }
                ],
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as error:
            _raise_trade_link_conflict(error)
        if document is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research entry not found")
        return Entry.from_document(document)

    async def remove_decision_trade_link(
        self,
        user_id: str,
        decision_id: str,
        reference: Reference,
        now: datetime,
    ) -> Entry:
        trade_link_key = reference.trade_link_key()
        document = await self._collection("entries").find_one_and_update(
            {
                "user_id": user_id,
                "id": decision_id,
                "entry_type": "decision",
                "deleted_at": None,
            },
            {
                "$pull": {
                    "references": {
                        "kind": reference.kind,
                        "source_id": reference.source_id,
                        "account_type": reference.account_type,
                    },
                    "trade_link_keys": trade_link_key,
                },
                "$set": {"updated_at": now.isoformat()},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research entry not found")
        return Entry.from_document(document)

    async def list_entries(
        self, user_id: str, query: EntryQuery
    ) -> ResearchPage[Entry]:
        _validate_pagination(query.page, query.page_size)
        mongo_query: dict[str, object] = {"user_id": user_id, "deleted_at": None}
        if query.security_id is not None:
            mongo_query["security_ids"] = _canonical_security_id(query.security_id)
        if query.entry_type is not None:
            mongo_query["entry_type"] = query.entry_type
        if query.scope is not None:
            mongo_query["scope"] = query.scope
        if query.status is not None:
            mongo_query["status"] = query.status
        else:
            mongo_query["archived_at"] = None
        if query.query and query.query.strip():
            pattern = re.escape(query.query.strip())
            mongo_query["$or"] = [
                {"title": {"$regex": pattern, "$options": "i"}},
                {"body": {"$regex": pattern, "$options": "i"}},
                {"topic": {"$regex": pattern, "$options": "i"}},
            ]
        collection = self._collection("entries")
        total = await collection.count_documents(mongo_query)
        documents = (
            await collection.find(mongo_query)
            .sort([("updated_at", DESCENDING), ("id", DESCENDING)])
            .skip((query.page - 1) * query.page_size)
            .limit(query.page_size)
            .to_list(length=query.page_size)
        )
        return ResearchPage(
            tuple(Entry.from_document(document) for document in documents),
            query.page,
            query.page_size,
            total,
        )

    async def append_revision(
        self,
        user_id: str,
        target_type: str,
        target_id: str,
        snapshot: dict[str, object],
        reason: str,
    ) -> Revision:
        collection = self._collection("revisions")
        counter_identity = {
            "user_id": user_id,
            "target_type": target_type,
            "target_id": target_id,
            "revision": 0,
            "_counter": True,
        }
        counter = await collection.find_one_and_update(
            counter_identity,
            {
                "$setOnInsert": {"id": f"counter:{uuid4().hex}"},
                "$inc": {"next_revision": 1},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if counter is None:
            raise ResearchError(
                "RESEARCH_STORAGE_UNAVAILABLE", "could not allocate revision"
            )
        revision = Revision(
            id=uuid4().hex,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            revision=int(counter["next_revision"]),
            snapshot=snapshot,
            reason=reason,
            created_at=datetime.now(UTC),
        )
        await collection.insert_one(revision.to_document())
        return revision

    async def list_revisions(
        self, user_id: str, target_type: str, target_id: str
    ) -> list[Revision]:
        documents = (
            await self._collection("revisions")
            .find(
                {
                    "user_id": user_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "_counter": {"$ne": True},
                }
            )
            .sort([("revision", ASCENDING)])
            .to_list(length=None)
        )
        return [Revision.from_document(document) for document in documents]

    async def get_revision(self, user_id: str, revision_id: str) -> Revision | None:
        document = await self._collection("revisions").find_one(
            {
                "user_id": user_id,
                "id": revision_id,
                "_counter": {"$ne": True},
            }
        )
        return Revision.from_document(document) if document is not None else None

    async def soft_delete_entry(
        self, user_id: str, entry_id: str, now: datetime
    ) -> None:
        timestamp = now.isoformat()
        await self._collection("entries").update_one(
            {"user_id": user_id, "id": entry_id, "deleted_at": None},
            {"$set": {"deleted_at": timestamp, "updated_at": timestamp}},
        )

    async def list_trash(
        self, user_id: str, page: int, page_size: int
    ) -> ResearchPage[Entry]:
        _validate_pagination(page, page_size)
        query = {"user_id": user_id, "deleted_at": {"$ne": None}}
        collection = self._collection("entries")
        total = await collection.count_documents(query)
        documents = (
            await collection.find(query)
            .sort([("deleted_at", DESCENDING), ("id", DESCENDING)])
            .skip((page - 1) * page_size)
            .limit(page_size)
            .to_list(length=page_size)
        )
        return ResearchPage(
            tuple(Entry.from_document(document) for document in documents),
            page,
            page_size,
            total,
        )

    async def restore_entry(self, user_id: str, entry_id: str) -> Entry:
        document = await self._collection("entries").find_one_and_update(
            {"user_id": user_id, "id": entry_id, "deleted_at": {"$ne": None}},
            {"$set": {"deleted_at": None, "updated_at": datetime.now(UTC).isoformat()}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research entry not found")
        return Entry.from_document(document)

    async def permanently_delete_entry(self, user_id: str, entry_id: str) -> None:
        await self._collection("entries").delete_one(
            {"user_id": user_id, "id": entry_id, "deleted_at": {"$ne": None}}
        )
