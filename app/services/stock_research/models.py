from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, date, datetime
from typing import Generic, Literal, Mapping, TypeVar
from uuid import uuid4

from app.services.stock_research.errors import ResearchError


StoredMarket = Literal["A", "HK", "US"]
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

ENTRY_TYPES = {"note", "research", "decision", "review"}
ENTRY_STATUSES = {"draft", "confirmed", "archived"}
SCOPES = {"stock", "portfolio"}
DECISION_ACTIONS = {"buy", "add", "reduce", "sell", "observe"}
REVIEW_KINDS = {"routine", "decision"}
REFERENCE_KINDS = {
    "analysis_report",
    "real_trade",
    "paper_trade",
    "decision",
    "holding_date",
}
GENERATION_STATUSES = {"pending", "running", "completed", "failed"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date_from_document(value: object) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _datetime_from_document(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _document_fields(
    record: type[object], document: Mapping[str, object]
) -> dict[str, object]:
    names = {item.name for item in fields(record)}
    return {name: deepcopy(value) for name, value in document.items() if name in names}


@dataclass(frozen=True, slots=True)
class ResearchSecurityId:
    market: Literal["A", "HK", "US"]
    code: str

    @classmethod
    def parse(cls, market: str, code: str) -> ResearchSecurityId:
        if not isinstance(market, str) or not isinstance(code, str):
            raise ResearchError("INVALID_SECURITY", "market and code are invalid")
        canonical_market = "A" if market.upper() in {"A", "CN"} else market.upper()
        canonical_code = code.strip().upper()
        if canonical_market not in {"A", "HK", "US"} or not canonical_code:
            raise ResearchError("INVALID_SECURITY", "market and code are invalid")
        return cls(canonical_market, canonical_code)

    @classmethod
    def from_string(cls, value: str) -> ResearchSecurityId:
        if not isinstance(value, str):
            raise ResearchError("INVALID_SECURITY", "market and code are invalid")
        try:
            market, code = value.split(":", maxsplit=1)
        except ValueError:
            raise ResearchError(
                "INVALID_SECURITY", "market and code are invalid"
            ) from None
        return cls.parse(market, code)

    def __str__(self) -> str:
        return f"{self.market}:{self.code}"


@dataclass(frozen=True, slots=True)
class Reference:
    kind: ReferenceKind
    source_id: str
    account_type: AccountType | None = None
    source_date: date | None = None
    label: str | None = None

    @classmethod
    def analysis_report(cls, source_id: str, label: str | None = None) -> Reference:
        return cls("analysis_report", source_id, label=label)

    @classmethod
    def real_trade(cls, source_id: str, label: str | None = None) -> Reference:
        return cls("real_trade", source_id, "real", label=label)

    @classmethod
    def paper_trade(cls, source_id: str, label: str | None = None) -> Reference:
        return cls("paper_trade", source_id, "paper", label=label)

    def to_document(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source_id": self.source_id,
            "account_type": self.account_type,
            "source_date": _iso_date(self.source_date),
            "label": self.label,
        }

    def trade_link_key(self) -> str:
        expected_account = {
            "real_trade": "real",
            "paper_trade": "paper",
        }.get(self.kind)
        if (
            expected_account is None
            or self.account_type != expected_account
            or not self.source_id.strip()
        ):
            raise ResearchError("INVALID_ENTRY", "trade reference is invalid")
        return json.dumps(
            [self.kind, self.account_type, self.source_id],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> Reference:
        return cls(
            kind=document["kind"],  # type: ignore[arg-type]
            source_id=str(document["source_id"]),
            account_type=document.get("account_type"),  # type: ignore[arg-type]
            source_date=_date_from_document(document.get("source_date")),
            label=document.get("label"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class Workspace:
    user_id: str
    security_id: str
    market: StoredMarket
    code: str
    name: str
    body: str = ""
    assumptions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    external_links: tuple[str, ...] = ()
    current_revision: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def _canonical_security(self) -> ResearchSecurityId:
        identifier = ResearchSecurityId.from_string(self.security_id)
        fields_identifier = ResearchSecurityId.parse(self.market, self.code)
        if identifier != fields_identifier:
            raise ResearchError(
                "INVALID_SECURITY", "workspace security fields are inconsistent"
            )
        return identifier

    def to_document(self) -> dict[str, object]:
        identifier = self._canonical_security()
        return {
            "user_id": self.user_id,
            "security_id": str(identifier),
            "market": identifier.market,
            "code": identifier.code,
            "name": self.name,
            "body": self.body,
            "assumptions": list(self.assumptions),
            "risks": list(self.risks),
            "invalidation_conditions": list(self.invalidation_conditions),
            "open_questions": list(self.open_questions),
            "tags": list(self.tags),
            "external_links": list(self.external_links),
            "current_revision": self.current_revision,
            "created_at": _iso_datetime(self.created_at),
            "updated_at": _iso_datetime(self.updated_at),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> Workspace:
        values = _document_fields(cls, document)
        for name in (
            "assumptions",
            "risks",
            "invalidation_conditions",
            "open_questions",
            "tags",
            "external_links",
        ):
            values[name] = tuple(values.get(name, ()))
        values["created_at"] = (
            _datetime_from_document(values.get("created_at")) or utc_now()
        )
        values["updated_at"] = (
            _datetime_from_document(values.get("updated_at")) or utc_now()
        )
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class WorkspaceDirectoryFacts:
    latest_entry_type: EntryType | None = None
    latest_entry_at: datetime | None = None
    has_real_holding: bool = False
    has_paper_holding: bool = False
    watchlisted: bool = False


@dataclass(frozen=True, slots=True)
class ResearchWorkspaceSummary:
    user_id: str
    security_id: str
    market: StoredMarket
    code: str
    name: str
    thesis_summary: str
    updated_at: datetime
    latest_entry_type: EntryType | None = None
    latest_entry_at: datetime | None = None
    has_real_holding: bool = False
    has_paper_holding: bool = False
    watchlisted: bool = False

    @classmethod
    def from_workspace(
        cls,
        workspace: Workspace,
        *,
        latest_entry_type: EntryType | None = None,
        latest_entry_at: datetime | None = None,
        has_real_holding: bool = False,
        has_paper_holding: bool = False,
        watchlisted: bool = False,
    ) -> ResearchWorkspaceSummary:
        return cls(
            user_id=workspace.user_id,
            security_id=workspace.security_id,
            market=workspace.market,
            code=workspace.code,
            name=workspace.name,
            thesis_summary=" ".join(workspace.body.split()),
            updated_at=workspace.updated_at,
            latest_entry_type=latest_entry_type,
            latest_entry_at=latest_entry_at,
            has_real_holding=has_real_holding,
            has_paper_holding=has_paper_holding,
            watchlisted=watchlisted,
        )

    def to_document(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "security_id": self.security_id,
            "market": self.market,
            "code": self.code,
            "name": self.name,
            "thesis_summary": self.thesis_summary,
            "updated_at": _iso_datetime(self.updated_at),
            "latest_entry_type": self.latest_entry_type,
            "latest_entry_at": _iso_datetime(self.latest_entry_at),
            "has_real_holding": self.has_real_holding,
            "has_paper_holding": self.has_paper_holding,
            "watchlisted": self.watchlisted,
        }


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    user_id: str
    entry_type: EntryType
    scope: ScopeType
    security_id: str | None = None
    security_ids: tuple[str, ...] = ()
    title: str = ""
    body: str = ""
    status: EntryStatus = "draft"
    tags: tuple[str, ...] = ()
    external_links: tuple[str, ...] = ()
    references: tuple[Reference, ...] = ()
    topic: str | None = None
    conclusion: str | None = None
    decision_action: DecisionAction | None = None
    decision_date: date | None = None
    planned_price: str | None = None
    target_allocation: str | None = None
    horizon: str | None = None
    thesis_snapshot: dict[str, object] | None = None
    review_kind: ReviewKind | None = None
    decision_id: str | None = None
    scope_metadata: dict[str, object] = field(default_factory=dict)
    ai_drafts: tuple[dict[str, object], ...] = ()
    source_metadata: dict[str, object] = field(default_factory=dict)
    trade_link_keys: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    current_revision: int = 0
    confirmed_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def new_note(
        cls,
        *,
        user_id: str,
        security: ResearchSecurityId,
        title: str,
        body: str,
        warnings: tuple[str, ...] = (),
    ) -> Entry:
        security_id = str(security)
        entry = cls(
            id=uuid4().hex,
            user_id=user_id,
            entry_type="note",
            scope="stock",
            security_id=security_id,
            security_ids=(security_id,),
            title=title,
            body=body,
            warnings=warnings,
        )
        entry.validate()
        return entry

    @classmethod
    def new_decision(
        cls,
        *,
        user_id: str,
        security: ResearchSecurityId,
        action: DecisionAction,
        decision_date: date,
        body: str,
        warnings: tuple[str, ...] = (),
    ) -> Entry:
        security_id = str(security)
        entry = cls(
            id=uuid4().hex,
            user_id=user_id,
            entry_type="decision",
            scope="stock",
            security_id=security_id,
            security_ids=(security_id,),
            body=body,
            decision_action=action,
            decision_date=decision_date,
            warnings=warnings,
        )
        entry.validate()
        return entry

    def validate(self) -> tuple[str, ...]:
        if self.entry_type not in ENTRY_TYPES:
            raise ResearchError("INVALID_ENTRY", "entry type is invalid")
        if self.scope not in SCOPES:
            raise ResearchError("INVALID_ENTRY", "entry scope is invalid")
        try:
            if self.security_id is not None:
                ResearchSecurityId.from_string(self.security_id)
            for security_id in self.security_ids:
                ResearchSecurityId.from_string(security_id)
        except ResearchError:
            raise ResearchError("INVALID_ENTRY", "entry security is invalid") from None
        if self.scope == "stock" and self.security_id is None:
            raise ResearchError("INVALID_ENTRY", "stock entry security is required")
        if self.entry_type == "note" and (
            not self.title.strip() or not self.body.strip()
        ):
            raise ResearchError("INVALID_ENTRY", "note title and body are required")
        if self.entry_type == "decision":
            if self.decision_action not in DECISION_ACTIONS:
                raise ResearchError("INVALID_ENTRY", "decision action is required")
            if type(self.decision_date) is not date:
                raise ResearchError("INVALID_ENTRY", "decision date is required")
        if self.review_kind is not None and self.review_kind not in REVIEW_KINDS:
            raise ResearchError("INVALID_ENTRY", "review kind is invalid")
        return self.warnings

    def to_document(self) -> dict[str, object]:
        security_id = (
            str(ResearchSecurityId.from_string(self.security_id))
            if self.security_id is not None
            else None
        )
        security_ids = [
            str(ResearchSecurityId.from_string(value)) for value in self.security_ids
        ]
        return {
            "id": self.id,
            "user_id": self.user_id,
            "entry_type": self.entry_type,
            "scope": self.scope,
            "security_id": security_id,
            "security_ids": security_ids,
            "title": self.title,
            "body": self.body,
            "status": self.status,
            "tags": list(self.tags),
            "external_links": list(self.external_links),
            "references": [reference.to_document() for reference in self.references],
            "topic": self.topic,
            "conclusion": self.conclusion,
            "decision_action": self.decision_action,
            "decision_date": _iso_date(self.decision_date),
            "planned_price": self.planned_price,
            "target_allocation": self.target_allocation,
            "horizon": self.horizon,
            "thesis_snapshot": deepcopy(self.thesis_snapshot),
            "review_kind": self.review_kind,
            "decision_id": self.decision_id,
            "scope_metadata": deepcopy(self.scope_metadata),
            "ai_drafts": deepcopy(list(self.ai_drafts)),
            "source_metadata": deepcopy(self.source_metadata),
            "trade_link_keys": list(self.trade_link_keys),
            "warnings": list(self.warnings),
            "current_revision": self.current_revision,
            "confirmed_at": _iso_datetime(self.confirmed_at),
            "archived_at": _iso_datetime(self.archived_at),
            "deleted_at": _iso_datetime(self.deleted_at),
            "created_at": _iso_datetime(self.created_at),
            "updated_at": _iso_datetime(self.updated_at),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> Entry:
        values = _document_fields(cls, document)
        for name in (
            "security_ids",
            "tags",
            "external_links",
            "trade_link_keys",
            "warnings",
        ):
            values[name] = tuple(values.get(name, ()))
        values["references"] = tuple(
            Reference.from_document(item)
            for item in values.get("references", ())  # type: ignore[union-attr]
        )
        values["ai_drafts"] = tuple(deepcopy(values.get("ai_drafts", ())))
        values["decision_date"] = _date_from_document(values.get("decision_date"))
        for name in (
            "confirmed_at",
            "archived_at",
            "deleted_at",
            "created_at",
            "updated_at",
        ):
            values[name] = _datetime_from_document(values.get(name))
        values["created_at"] = values.get("created_at") or utc_now()
        values["updated_at"] = values.get("updated_at") or utc_now()
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ThesisPatch:
    body: str | None = None
    assumptions: tuple[str, ...] | None = None
    risks: tuple[str, ...] | None = None
    invalidation_conditions: tuple[str, ...] | None = None
    open_questions: tuple[str, ...] | None = None
    tags: tuple[str, ...] | None = None
    external_links: tuple[str, ...] | None = None

    def changes(self) -> dict[str, object]:
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if getattr(self, item.name) is not None
        }


@dataclass(frozen=True, slots=True)
class EntryPatch:
    title: str | None = None
    body: str | None = None
    tags: tuple[str, ...] | None = None
    external_links: tuple[str, ...] | None = None
    references: tuple[Reference, ...] | None = None
    topic: str | None = None
    conclusion: str | None = None
    decision_action: DecisionAction | None = None
    decision_date: date | None = None
    planned_price: str | None = None
    target_allocation: str | None = None
    horizon: str | None = None
    review_kind: ReviewKind | None = None
    decision_id: str | None = None
    scope_metadata: dict[str, object] | None = None

    def changes(self) -> dict[str, object]:
        return {
            item.name: deepcopy(getattr(self, item.name))
            for item in fields(self)
            if getattr(self, item.name) is not None
        }


@dataclass(frozen=True, slots=True)
class NewEntry:
    entry_type: EntryType
    scope: ScopeType
    security_id: str | None = None
    security_ids: tuple[str, ...] = ()
    title: str = ""
    body: str = ""
    tags: tuple[str, ...] = ()
    external_links: tuple[str, ...] = ()
    references: tuple[Reference, ...] = ()
    topic: str | None = None
    conclusion: str | None = None
    decision_action: DecisionAction | None = None
    decision_date: date | None = None
    planned_price: str | None = None
    target_allocation: str | None = None
    horizon: str | None = None
    review_kind: ReviewKind | None = None
    decision_id: str | None = None
    scope_metadata: dict[str, object] = field(default_factory=dict)

    @staticmethod
    def _security_id(value: str) -> str:
        return str(ResearchSecurityId.from_string(value))

    @classmethod
    def note(cls, security_id: str, title: str, body: str) -> NewEntry:
        canonical = cls._security_id(security_id)
        return cls(
            entry_type="note",
            scope="stock",
            security_id=canonical,
            security_ids=(canonical,),
            title=title,
            body=body,
        )

    @classmethod
    def research(
        cls,
        security_id: str,
        title: str,
        body: str,
        *,
        topic: str | None = None,
    ) -> NewEntry:
        canonical = cls._security_id(security_id)
        return cls(
            entry_type="research",
            scope="stock",
            security_id=canonical,
            security_ids=(canonical,),
            title=title,
            body=body,
            topic=topic,
        )

    @classmethod
    def decision(
        cls,
        security_id: str,
        action: DecisionAction,
        decision_date: date,
        *,
        body: str = "",
    ) -> NewEntry:
        canonical = cls._security_id(security_id)
        return cls(
            entry_type="decision",
            scope="stock",
            security_id=canonical,
            security_ids=(canonical,),
            body=body,
            decision_action=action,
            decision_date=decision_date,
        )

    @classmethod
    def routine_review(
        cls,
        *,
        scope: ScopeType,
        body: str,
        security_id: str | None = None,
        security_ids: tuple[str, ...] = (),
    ) -> NewEntry:
        canonical_primary = (
            cls._security_id(security_id) if security_id is not None else None
        )
        canonical_ids = tuple(cls._security_id(value) for value in security_ids)
        if canonical_primary is not None and canonical_primary not in canonical_ids:
            canonical_ids = (canonical_primary, *canonical_ids)
        return cls(
            entry_type="review",
            scope=scope,
            security_id=canonical_primary,
            security_ids=canonical_ids,
            body=body,
            review_kind="routine",
        )

    @classmethod
    def decision_review(
        cls,
        *,
        scope: ScopeType,
        body: str,
        security_id: str | None = None,
        security_ids: tuple[str, ...] = (),
        decision_id: str | None = None,
    ) -> NewEntry:
        request = cls.routine_review(
            scope=scope,
            body=body,
            security_id=security_id,
            security_ids=security_ids,
        )
        return replace(
            request,
            review_kind="decision",
            decision_id=decision_id,
        )


@dataclass(frozen=True, slots=True)
class Revision:
    id: str
    user_id: str
    target_type: str
    target_id: str
    revision: int
    snapshot: dict[str, object]
    reason: str
    created_at: datetime

    def to_document(self) -> dict[str, object]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "revision": self.revision,
            "snapshot": deepcopy(self.snapshot),
            "reason": self.reason,
            "created_at": _iso_datetime(self.created_at),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> Revision:
        values = _document_fields(cls, document)
        values["created_at"] = (
            _datetime_from_document(values.get("created_at")) or utc_now()
        )
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class GenerationTask:
    id: str
    user_id: str
    target_entry_id: str
    draft_kind: str
    provider: str
    model_name: str
    reasoning_effort: str | None = None
    references: tuple[Reference, ...] = ()
    source_ids: tuple[str, ...] = ()
    context_snapshot: dict[str, object] = field(default_factory=dict)
    prompt_version: str = "research-draft-v1"
    status: GenerationStatus = "pending"
    content: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    generated_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_document(self) -> dict[str, object]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "target_entry_id": self.target_entry_id,
            "draft_kind": self.draft_kind,
            "provider": self.provider,
            "model_name": self.model_name,
            "reasoning_effort": self.reasoning_effort,
            "references": [reference.to_document() for reference in self.references],
            "source_ids": list(self.source_ids),
            "context_snapshot": deepcopy(self.context_snapshot),
            "prompt_version": self.prompt_version,
            "status": self.status,
            "content": self.content,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "generated_at": _iso_datetime(self.generated_at),
            "created_at": _iso_datetime(self.created_at),
            "updated_at": _iso_datetime(self.updated_at),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> GenerationTask:
        values = _document_fields(cls, document)
        values["references"] = tuple(
            Reference.from_document(item)
            for item in values.get("references", ())  # type: ignore[union-attr]
        )
        values["source_ids"] = tuple(values.get("source_ids", ()))
        for name in ("generated_at", "created_at", "updated_at"):
            values[name] = _datetime_from_document(values.get(name))
        values["created_at"] = values.get("created_at") or utc_now()
        values["updated_at"] = values.get("updated_at") or utc_now()
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class WorkspaceQuery:
    market: StoredMarket | None = None
    query: str | None = None
    real_holding: bool | None = None
    paper_holding: bool | None = None
    watchlisted: bool | None = None
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True, slots=True)
class EntryQuery:
    security_id: str | None = None
    entry_type: EntryType | None = None
    status: EntryStatus | None = None
    scope: ScopeType | None = None
    query: str | None = None
    page: int = 1
    page_size: int = 20


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ResearchPage(Generic[T]):
    items: tuple[T, ...]
    page: int
    page_size: int
    total: int
