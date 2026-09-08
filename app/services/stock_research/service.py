from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Callable, Literal
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    Entry,
    EntryPatch,
    EntryQuery,
    NewEntry,
    Reference,
    ResearchPage,
    ResearchSecurityId,
    Revision,
    ThesisPatch,
    Workspace,
    WorkspaceQuery,
    utc_now,
)
from app.services.stock_research.storage import StockResearchRepository


class StockResearchService:
    def __init__(
        self,
        repository: StockResearchRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.id_factory = id_factory

    async def get_workspace(self, user_id: str, security_id: str) -> Workspace:
        workspace = await self.repository.get_workspace(user_id, security_id)
        if workspace is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research workspace not found")
        return workspace

    async def list_workspaces(
        self, user_id: str, query: WorkspaceQuery
    ) -> ResearchPage[Workspace]:
        return await self.repository.list_workspaces(user_id, query)

    async def get_or_create_workspace(
        self, user_id: str, market: str, code: str, name: str
    ) -> Workspace:
        security = ResearchSecurityId.parse(market, code)
        existing = await self.repository.get_workspace(user_id, str(security))
        if existing is not None:
            return existing
        now = self.clock()
        return await self.repository.upsert_workspace(
            Workspace(
                user_id=user_id,
                security_id=str(security),
                market=security.market,
                code=security.code,
                name=name,
                created_at=now,
                updated_at=now,
            )
        )

    async def save_thesis_draft(
        self, user_id: str, security_id: str, patch: ThesisPatch
    ) -> Workspace:
        workspace = await self.get_workspace(user_id, security_id)
        updated = replace(workspace, **patch.changes(), updated_at=self.clock())
        return await self.repository.upsert_workspace(updated)

    async def save_workspace_version(
        self, user_id: str, security_id: str, label: str
    ) -> Revision:
        workspace = await self.get_workspace(user_id, security_id)
        snapshot = workspace.to_document()
        snapshot["label"] = label
        revision = await self.repository.append_revision(
            user_id, "workspace", workspace.security_id, snapshot, "manual"
        )
        await self.repository.upsert_workspace(
            replace(
                workspace,
                current_revision=revision.revision,
                updated_at=self.clock(),
            )
        )
        return revision

    async def get_entry(
        self,
        user_id: str,
        entry_id: str,
        *,
        include_deleted: bool = False,
    ) -> Entry:
        return await self._get_entry(
            user_id, entry_id, include_deleted=include_deleted
        )

    async def list_entries(
        self, user_id: str, query: EntryQuery
    ) -> ResearchPage[Entry]:
        return await self.repository.list_entries(user_id, query)

    async def create_entry(self, user_id: str, request: NewEntry) -> Entry:
        now = self.clock()
        entry = Entry(
            id=self.id_factory(),
            user_id=user_id,
            entry_type=request.entry_type,
            scope=request.scope,
            security_id=request.security_id,
            security_ids=request.security_ids,
            title=request.title,
            body=request.body,
            tags=request.tags,
            external_links=request.external_links,
            references=request.references,
            topic=request.topic,
            conclusion=request.conclusion,
            decision_action=request.decision_action,
            decision_date=request.decision_date,
            planned_price=request.planned_price,
            target_allocation=request.target_allocation,
            horizon=request.horizon,
            review_kind=request.review_kind,
            decision_id=request.decision_id,
            scope_metadata=request.scope_metadata,
            created_at=now,
            updated_at=now,
        )
        return await self.repository.insert_entry(entry)

    async def update_entry_draft(
        self, user_id: str, entry_id: str, patch: EntryPatch
    ) -> Entry:
        entry = await self._get_entry(user_id, entry_id)
        if entry.status == "archived":
            raise ResearchError(
                "INVALID_ENTRY", "archived entry cannot be autosaved"
            )
        if entry.entry_type == "decision" and entry.status == "confirmed":
            raise ResearchError(
                "INVALID_ENTRY", "formal entry cannot be autosaved"
            )
        updated = replace(entry, **patch.changes(), updated_at=self.clock())
        updated.validate()
        if entry.entry_type == "review" and entry.status == "confirmed":
            revision = await self.repository.append_revision(
                user_id,
                "entry",
                entry.id,
                updated.to_document(),
                "review_confirmed",
            )
            updated = replace(updated, current_revision=revision.revision)
        return await self.repository.replace_entry(updated)

    async def convert_entry(
        self,
        user_id: str,
        entry_id: str,
        target: Literal["note", "research"],
        *,
        topic: str | None = None,
    ) -> Entry:
        entry = await self._get_entry(user_id, entry_id)
        if entry.entry_type not in {"note", "research"} or target not in {
            "note",
            "research",
        }:
            raise ResearchError(
                "INVALID_ENTRY", "only note and research entries can be converted"
            )
        changes: dict[str, object] = {
            "entry_type": target,
            "updated_at": self.clock(),
        }
        if target == "research" and topic is not None:
            changes["topic"] = topic
        converted = replace(entry, **changes)
        return await self.repository.replace_entry(converted)

    async def confirm_entry(self, user_id: str, entry_id: str) -> Entry:
        entry = await self._get_entry(user_id, entry_id)
        if entry.status == "confirmed":
            raise ResearchError("INVALID_ENTRY", "formal entry is already confirmed")
        if entry.entry_type not in {"decision", "review"}:
            raise ResearchError(
                "INVALID_ENTRY", "only decisions and reviews can be confirmed"
            )
        now = self.clock()
        changes: dict[str, object] = {
            "status": "confirmed",
            "confirmed_at": now,
            "updated_at": now,
        }
        reason = "review_confirmed"
        if entry.entry_type == "decision":
            if entry.security_id is None:
                raise ResearchError("INVALID_ENTRY", "decision security is required")
            workspace = await self.get_workspace(user_id, entry.security_id)
            changes["thesis_snapshot"] = workspace.to_document()
            reason = "decision_confirmed"
        confirmed = replace(entry, **changes)
        confirmed.validate()
        revision = await self.repository.append_revision(
            user_id, "entry", entry.id, confirmed.to_document(), reason
        )
        return await self.repository.replace_entry(
            replace(confirmed, current_revision=revision.revision)
        )

    async def restore_revision(self, user_id: str, revision_id: str) -> Revision:
        source = await self.repository.get_revision(user_id, revision_id)
        if source is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research revision not found")
        if source.target_type == "workspace":
            current = await self.get_workspace(user_id, source.target_id)
            restored = Workspace.from_document(source.snapshot)
            restored = replace(
                restored,
                user_id=current.user_id,
                security_id=current.security_id,
                market=current.market,
                code=current.code,
                name=current.name,
                created_at=current.created_at,
                updated_at=self.clock(),
            )
            snapshot = restored.to_document()
            revision = await self.repository.append_revision(
                user_id,
                "workspace",
                current.security_id,
                snapshot,
                "revision_restored",
            )
            await self.repository.upsert_workspace(
                replace(restored, current_revision=revision.revision)
            )
            return revision
        if source.target_type == "entry":
            current_entry = await self._get_entry(user_id, source.target_id)
            restored_entry = Entry.from_document(source.snapshot)
            restored_entry = replace(
                restored_entry,
                id=current_entry.id,
                user_id=current_entry.user_id,
                created_at=current_entry.created_at,
                deleted_at=current_entry.deleted_at,
                updated_at=self.clock(),
            )
            restored_entry.validate()
            revision = await self.repository.append_revision(
                user_id,
                "entry",
                current_entry.id,
                restored_entry.to_document(),
                "revision_restored",
            )
            await self.repository.replace_entry(
                replace(restored_entry, current_revision=revision.revision)
            )
            return revision
        raise ResearchError("INVALID_REVISION", "revision target type is invalid")

    async def get_revision(self, user_id: str, revision_id: str) -> Revision:
        revision = await self.repository.get_revision(user_id, revision_id)
        if revision is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research revision not found")
        return revision

    async def list_revisions(
        self, user_id: str, target_type: str, target_id: str
    ) -> list[Revision]:
        return await self.repository.list_revisions(user_id, target_type, target_id)

    async def apply_review_to_thesis(
        self, user_id: str, review_id: str, patch: ThesisPatch
    ) -> Revision:
        review = await self._get_entry(user_id, review_id)
        if review.entry_type != "review" or review.status != "confirmed":
            raise ResearchError("INVALID_ENTRY", "formal review is required")
        if review.security_id is None:
            raise ResearchError(
                "INVALID_ENTRY", "stock review security is required"
            )
        workspace = await self.get_workspace(user_id, review.security_id)
        updated = replace(workspace, **patch.changes(), updated_at=self.clock())
        revision = await self.repository.append_revision(
            user_id,
            "workspace",
            workspace.security_id,
            updated.to_document(),
            "review_applied_to_thesis",
        )
        await self.repository.upsert_workspace(
            replace(updated, current_revision=revision.revision)
        )
        return revision

    async def archive_entry(self, user_id: str, entry_id: str) -> Entry:
        entry = await self._get_entry(user_id, entry_id)
        now = self.clock()
        return await self.repository.replace_entry(
            replace(entry, status="archived", archived_at=now, updated_at=now)
        )

    async def delete_entry(self, user_id: str, entry_id: str) -> None:
        await self._get_entry(user_id, entry_id)
        await self.repository.soft_delete_entry(user_id, entry_id, self.clock())

    async def restore_entry(self, user_id: str, entry_id: str) -> Entry:
        entry = await self._get_entry(user_id, entry_id, include_deleted=True)
        if entry.deleted_at is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research entry not found")
        return await self.repository.restore_entry(user_id, entry_id)

    async def list_trash(
        self, user_id: str, page: int, page_size: int
    ) -> ResearchPage[Entry]:
        return await self.repository.list_trash(user_id, page, page_size)

    async def permanently_delete_entry(self, user_id: str, entry_id: str) -> None:
        await self._get_entry(user_id, entry_id, include_deleted=True)
        await self.repository.permanently_delete_entry(user_id, entry_id)

    async def set_decision_trade_links(
        self, user_id: str, decision_id: str, references: list[Reference]
    ) -> Entry:
        decision = await self._get_entry(user_id, decision_id)
        if decision.entry_type != "decision":
            raise ResearchError("INVALID_ENTRY", "trade links require a decision")
        confirmed: list[Reference] = []
        trade_link_keys: list[str] = []
        seen: set[str] = set()
        for reference in references:
            key = self._trade_link_key(reference)
            if key in seen:
                continue
            seen.add(key)
            confirmed.append(reference)
            trade_link_keys.append(key)
        non_trade_references = tuple(
            reference
            for reference in decision.references
            if reference.kind not in {"real_trade", "paper_trade"}
        )
        updated = replace(
            decision,
            references=(*non_trade_references, *confirmed),
            trade_link_keys=tuple(trade_link_keys),
            updated_at=self.clock(),
        )
        try:
            return await self.repository.replace_entry(updated)
        except DuplicateKeyError:
            raise ResearchError(
                "RESEARCH_CONFLICT", "trade is already linked to another decision"
            ) from None

    async def get_decision_trade_links(
        self, user_id: str, decision_id: str
    ) -> list[Reference]:
        decision = await self._get_entry(user_id, decision_id)
        if decision.entry_type != "decision":
            raise ResearchError("INVALID_ENTRY", "trade links require a decision")
        return [
            reference
            for reference in decision.references
            if reference.kind in {"real_trade", "paper_trade"}
        ]

    @staticmethod
    def _trade_link_key(reference: Reference) -> str:
        expected_account = {
            "real_trade": "real",
            "paper_trade": "paper",
        }.get(reference.kind)
        if (
            expected_account is None
            or reference.account_type != expected_account
            or not reference.source_id.strip()
        ):
            raise ResearchError("INVALID_ENTRY", "trade reference is invalid")
        return json.dumps(
            [reference.kind, reference.account_type, reference.source_id],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    async def _get_entry(
        self,
        user_id: str,
        entry_id: str,
        *,
        include_deleted: bool = False,
    ) -> Entry:
        entry = await self.repository.get_entry(
            user_id, entry_id, include_deleted=include_deleted
        )
        if entry is None:
            raise ResearchError("RESEARCH_NOT_FOUND", "research entry not found")
        return entry
