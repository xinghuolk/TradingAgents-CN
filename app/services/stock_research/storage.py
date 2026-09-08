from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from pymongo import ASCENDING, DESCENDING, ReturnDocument

from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    Entry,
    EntryQuery,
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
        mongo_query: dict[str, object] = {"user_id": user_id}
        if query.market is not None:
            mongo_query["market"] = _canonical_market(query.market)
        for name in ("real_holding", "paper_holding", "watchlisted"):
            value = getattr(query, name)
            if value is not None:
                mongo_query[f"has_{name}" if name != "watchlisted" else name] = value
        if query.query and query.query.strip():
            pattern = re.escape(query.query.strip())
            mongo_query["$or"] = [
                {"code": {"$regex": pattern, "$options": "i"}},
                {"name": {"$regex": pattern, "$options": "i"}},
                {"body": {"$regex": pattern, "$options": "i"}},
            ]
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
        entry.validate()
        document = entry.to_document()
        await self._collection("entries").insert_one(document)
        return Entry.from_document(document)

    async def replace_entry(self, entry: Entry) -> Entry:
        entry.validate()
        document = entry.to_document()
        result = await self._collection("entries").update_one(
            {"user_id": entry.user_id, "id": entry.id},
            {"$set": document},
        )
        if result.matched_count == 0:
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
