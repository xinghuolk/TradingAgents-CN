"""Authenticated CRUD API for personal stock research documents."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from datetime import date
from pathlib import Path
from typing import Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import PyMongoError

from app.core.config import settings
from app.core.database import get_mongo_db
from app.core.response import ok
from app.routers.auth_db import get_current_user
from app.services.real_portfolio.service import RealPortfolioService
from app.services.real_portfolio.storage import RealPortfolioRepository
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
)
from app.services.stock_research.references import (
    AnalysisReportAdapter,
    PaperTradeAdapter,
    ReferenceCandidate,
    ReferenceService,
)
from app.services.stock_research.service import StockResearchService
from app.services.stock_research.storage import StockResearchRepository


router = APIRouter(prefix="/research", tags=["research"])
logger = logging.getLogger(__name__)

ERROR_STATUS = {
    "RESEARCH_NOT_FOUND": 404,
    "RESEARCH_CONFLICT": 409,
    "INVALID_ENTRY": 422,
    "INVALID_SECURITY": 422,
    "INVALID_QUERY": 422,
    "INVALID_REVISION": 422,
    "RESEARCH_STORAGE_UNAVAILABLE": 503,
}
_T = TypeVar("_T")


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateWorkspaceRequest(StrictRequest):
    market: str
    code: str
    name: str


class ThesisPatchRequest(StrictRequest):
    body: str | None = None
    assumptions: list[str] | None = None
    risks: list[str] | None = None
    invalidation_conditions: list[str] | None = None
    open_questions: list[str] | None = None
    tags: list[str] | None = None
    external_links: list[str] | None = None

    def to_domain(self) -> ThesisPatch:
        return ThesisPatch(
            body=self.body,
            assumptions=tuple(self.assumptions) if self.assumptions is not None else None,
            risks=tuple(self.risks) if self.risks is not None else None,
            invalidation_conditions=(
                tuple(self.invalidation_conditions)
                if self.invalidation_conditions is not None
                else None
            ),
            open_questions=(
                tuple(self.open_questions) if self.open_questions is not None else None
            ),
            tags=tuple(self.tags) if self.tags is not None else None,
            external_links=(
                tuple(self.external_links) if self.external_links is not None else None
            ),
        )


class SaveRevisionRequest(StrictRequest):
    label: str = ""


class ReferenceRequest(StrictRequest):
    kind: Literal[
        "analysis_report", "real_trade", "paper_trade", "decision", "holding_date"
    ]
    source_id: str
    account_type: Literal["real", "paper"] | None = None
    source_date: date | None = None
    label: str | None = None

    def to_domain(self) -> Reference:
        return Reference(
            kind=self.kind,
            source_id=self.source_id,
            account_type=self.account_type,
            source_date=self.source_date,
            label=self.label,
        )


class CreateEntryRequest(StrictRequest):
    entry_type: Literal["note", "research", "decision", "review"]
    scope: Literal["stock", "portfolio"] = "stock"
    security_id: str | None = None
    security_ids: list[str] = Field(default_factory=list)
    title: str = ""
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    external_links: list[str] = Field(default_factory=list)
    references: list[ReferenceRequest] = Field(default_factory=list)
    topic: str | None = None
    conclusion: str | None = None
    decision_action: Literal["buy", "add", "reduce", "sell", "observe"] | None = None
    decision_date: date | None = None
    planned_price: str | None = None
    target_allocation: str | None = None
    horizon: str | None = None
    review_kind: Literal["routine", "decision"] | None = None
    decision_id: str | None = None
    scope_metadata: dict[str, object] = Field(default_factory=dict)

    def to_domain(self) -> NewEntry:
        canonical_security_id = (
            str(ResearchSecurityId.from_string(self.security_id))
            if self.security_id is not None
            else None
        )
        canonical_security_ids = tuple(
            dict.fromkeys(
                (
                    *(
                        (canonical_security_id,)
                        if canonical_security_id is not None
                        else ()
                    ),
                    *(
                        str(ResearchSecurityId.from_string(security_id))
                        for security_id in self.security_ids
                    ),
                )
            )
        )
        return NewEntry(
            entry_type=self.entry_type,
            scope=self.scope,
            security_id=canonical_security_id,
            security_ids=canonical_security_ids,
            title=self.title,
            body=self.body,
            tags=tuple(self.tags),
            external_links=tuple(self.external_links),
            references=tuple(item.to_domain() for item in self.references),
            topic=self.topic,
            conclusion=self.conclusion,
            decision_action=self.decision_action,
            decision_date=self.decision_date,
            planned_price=self.planned_price,
            target_allocation=self.target_allocation,
            horizon=self.horizon,
            review_kind=self.review_kind,
            decision_id=self.decision_id,
            scope_metadata=dict(self.scope_metadata),
        )


class EntryPatchRequest(StrictRequest):
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    external_links: list[str] | None = None
    references: list[ReferenceRequest] | None = None
    topic: str | None = None
    conclusion: str | None = None
    decision_action: Literal["buy", "add", "reduce", "sell", "observe"] | None = None
    decision_date: date | None = None
    planned_price: str | None = None
    target_allocation: str | None = None
    horizon: str | None = None
    review_kind: Literal["routine", "decision"] | None = None
    decision_id: str | None = None
    scope_metadata: dict[str, object] | None = None

    def to_domain(self) -> EntryPatch:
        return EntryPatch(
            title=self.title,
            body=self.body,
            tags=tuple(self.tags) if self.tags is not None else None,
            external_links=(
                tuple(self.external_links) if self.external_links is not None else None
            ),
            references=(
                tuple(item.to_domain() for item in self.references)
                if self.references is not None
                else None
            ),
            topic=self.topic,
            conclusion=self.conclusion,
            decision_action=self.decision_action,
            decision_date=self.decision_date,
            planned_price=self.planned_price,
            target_allocation=self.target_allocation,
            horizon=self.horizon,
            review_kind=self.review_kind,
            decision_id=self.decision_id,
            scope_metadata=(
                dict(self.scope_metadata) if self.scope_metadata is not None else None
            ),
        )


class ConvertEntryRequest(StrictRequest):
    target: Literal["note", "research"]
    topic: str | None = None


class SetDecisionTradeLinksRequest(StrictRequest):
    references: list[ReferenceRequest] = Field(default_factory=list)


def get_stock_research_service(
    db=Depends(get_mongo_db),  # noqa: B008 - FastAPI dependency declaration
) -> StockResearchService:
    return StockResearchService(StockResearchRepository(db))


def get_reference_service(
    db=Depends(get_mongo_db),  # noqa: B008 - FastAPI dependency declaration
) -> ReferenceService:
    return ReferenceService(
        StockResearchRepository(db),
        AnalysisReportAdapter(db),
        RealPortfolioService(
            RealPortfolioRepository(db), Path(settings.TRADINGAGENTS_DATA_DIR)
        ),
        PaperTradeAdapter(db),
    )


def _internal_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={"code": "INTERNAL_ERROR", "message": "research request failed"},
    )


def _research_error(error: ResearchError) -> HTTPException:
    status_code = ERROR_STATUS.get(error.code)
    if status_code is None:
        logger.warning("Unmapped stock research error (ResearchError)")
        return _internal_error()
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": error.message},
    )


async def _call_service(operation: Awaitable[_T]) -> _T:
    try:
        return await operation
    except ResearchError as error:
        raise _research_error(error) from None
    except PyMongoError as error:
        logger.error(
            "Stock research storage failure (%s)", type(error).__name__
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RESEARCH_STORAGE_UNAVAILABLE",
                "message": "research storage is unavailable",
            },
        ) from None
    except Exception as error:
        logger.error(
            "Unexpected stock research service failure (%s)", type(error).__name__
        )
        raise _internal_error() from None


def _public_document(item: Workspace | Entry | Revision) -> dict[str, object]:
    def sanitize(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: (
                    "CN"
                    if key == "market" and nested == "A"
                    else sanitize(nested)
                )
                for key, nested in value.items()
                if key != "user_id"
            }
        if isinstance(value, list):
            return [sanitize(nested) for nested in value]
        return value

    document = item.to_document()
    return {
        key: "CN" if key == "market" and value == "A" else sanitize(value)
        for key, value in document.items()
        if key != "user_id"
    }


def _page(page: ResearchPage[Workspace] | ResearchPage[Entry]) -> dict[str, object]:
    return {
        "items": [_public_document(item) for item in page.items],
        "page": page.page,
        "page_size": page.page_size,
        "total": page.total,
    }


def _public_reference(item: ReferenceCandidate) -> dict[str, object]:
    document = item.to_document()
    document.pop("user_id", None)
    return document


def _user_id(current_user: dict) -> str:
    return str(current_user["id"])


@router.get("/references")
async def list_reference_candidates(
    security_id: str,
    date_from: date | None = None,
    date_through: date | None = None,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: ReferenceService = Depends(get_reference_service),  # noqa: B008
):
    result = await _call_service(
        service.list_candidates(
            user_id=_user_id(current_user),
            security_id=security_id,
            date_from=date_from,
            date_through=date_through,
        )
    )
    return ok([_public_reference(item) for item in result])


@router.get("/links/recommendations")
async def recommend_trade_links(
    decision_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: ReferenceService = Depends(get_reference_service),  # noqa: B008
):
    result = await _call_service(
        service.recommend_trade_links(
            user_id=_user_id(current_user), decision_id=decision_id
        )
    )
    return ok([_public_reference(item) for item in result])


@router.put("/links/decisions/{decision_id}")
async def set_decision_trade_links(
    decision_id: str,
    payload: SetDecisionTradeLinksRequest,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.set_decision_trade_links(
            user_id=_user_id(current_user),
            decision_id=decision_id,
            references=[item.to_domain() for item in payload.references],
        )
    )
    return ok(_public_document(result))


@router.delete("/links/decisions/{decision_id}/{kind}/{source_id}")
async def delete_decision_trade_link(
    decision_id: str,
    kind: Literal["real_trade", "paper_trade"],
    source_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    user_id = _user_id(current_user)
    current = await _call_service(
        service.get_decision_trade_links(user_id=user_id, decision_id=decision_id)
    )
    result = await _call_service(
        service.set_decision_trade_links(
            user_id=user_id,
            decision_id=decision_id,
            references=[
                reference
                for reference in current
                if not (
                    reference.kind == kind and reference.source_id == source_id
                )
            ],
        )
    )
    return ok(_public_document(result))


@router.get("/workspaces")
async def list_workspaces(
    market: str | None = None,
    query: str | None = None,
    real_holding: bool | None = None,
    paper_holding: bool | None = None,
    watchlisted: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    canonical_market = None
    if market is not None:
        canonical_market = "A" if market.upper() == "CN" else market.upper()
    result = await _call_service(
        service.list_workspaces(
            user_id=_user_id(current_user),
            query=WorkspaceQuery(
                market=canonical_market,  # type: ignore[arg-type]
                query=query,
                real_holding=real_holding,
                paper_holding=paper_holding,
                watchlisted=watchlisted,
                page=page,
                page_size=page_size,
            ),
        )
    )
    return ok(_page(result))


@router.post("/workspaces")
async def create_workspace(
    payload: CreateWorkspaceRequest,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.get_or_create_workspace(
            user_id=_user_id(current_user),
            market=payload.market,
            code=payload.code,
            name=payload.name,
        )
    )
    return ok(_public_document(result))


@router.get("/workspaces/{security_id}")
async def get_workspace(
    security_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.get_workspace(
            user_id=_user_id(current_user), security_id=security_id
        )
    )
    return ok(_public_document(result))


@router.patch("/workspaces/{security_id}")
async def update_workspace(
    security_id: str,
    payload: ThesisPatchRequest,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.save_thesis_draft(
            user_id=_user_id(current_user),
            security_id=security_id,
            patch=payload.to_domain(),
        )
    )
    return ok(_public_document(result))


@router.post("/workspaces/{security_id}/revisions")
async def save_workspace_revision(
    security_id: str,
    payload: SaveRevisionRequest,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.save_workspace_version(
            user_id=_user_id(current_user),
            security_id=security_id,
            label=payload.label,
        )
    )
    return ok(_public_document(result))


@router.get("/entries")
async def list_entries(
    security_id: str | None = None,
    entry_type: Literal["note", "research", "decision", "review"] | None = None,
    status: Literal["draft", "confirmed", "archived"] | None = None,
    scope: Literal["stock", "portfolio"] | None = None,
    query: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.list_entries(
            user_id=_user_id(current_user),
            query=EntryQuery(
                security_id=security_id,
                entry_type=entry_type,
                status=status,
                scope=scope,
                query=query,
                page=page,
                page_size=page_size,
            ),
        )
    )
    return ok(_page(result))


@router.post("/entries")
async def create_entry(
    payload: CreateEntryRequest,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    try:
        request = payload.to_domain()
    except ResearchError as error:
        raise _research_error(error) from None
    result = await _call_service(
        service.create_entry(
            user_id=_user_id(current_user), request=request
        )
    )
    return ok(_public_document(result))


@router.get("/entries/{entry_id}")
async def get_entry(
    entry_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.get_entry(user_id=_user_id(current_user), entry_id=entry_id)
    )
    return ok(_public_document(result))


@router.patch("/entries/{entry_id}")
async def update_entry(
    entry_id: str,
    payload: EntryPatchRequest,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.update_entry_draft(
            user_id=_user_id(current_user),
            entry_id=entry_id,
            patch=payload.to_domain(),
        )
    )
    return ok(_public_document(result))


@router.post("/entries/{entry_id}/convert")
async def convert_entry(
    entry_id: str,
    payload: ConvertEntryRequest,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.convert_entry(
            user_id=_user_id(current_user),
            entry_id=entry_id,
            target=payload.target,
            topic=payload.topic,
        )
    )
    return ok(_public_document(result))


@router.post("/entries/{entry_id}/confirm")
async def confirm_entry(
    entry_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.confirm_entry(user_id=_user_id(current_user), entry_id=entry_id)
    )
    return ok(_public_document(result))


@router.post("/entries/{entry_id}/archive")
async def archive_entry(
    entry_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.archive_entry(user_id=_user_id(current_user), entry_id=entry_id)
    )
    return ok(_public_document(result))


@router.delete("/entries/{entry_id}")
async def delete_entry(
    entry_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    await _call_service(
        service.delete_entry(user_id=_user_id(current_user), entry_id=entry_id)
    )
    return ok()


@router.get("/revisions")
async def list_revisions(
    target_type: Literal["workspace", "entry"],
    target_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.list_revisions(
            user_id=_user_id(current_user),
            target_type=target_type,
            target_id=target_id,
        )
    )
    return ok([_public_document(item) for item in result])


@router.get("/revisions/{revision_id}")
async def get_revision(
    revision_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.get_revision(
            user_id=_user_id(current_user), revision_id=revision_id
        )
    )
    return ok(_public_document(result))


@router.post("/revisions/{revision_id}/restore")
async def restore_revision(
    revision_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.restore_revision(
            user_id=_user_id(current_user), revision_id=revision_id
        )
    )
    return ok(_public_document(result))


@router.get("/trash")
async def list_trash(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.list_trash(
            user_id=_user_id(current_user), page=page, page_size=page_size
        )
    )
    return ok(_page(result))


@router.post("/trash/{entry_id}/restore")
async def restore_entry(
    entry_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    result = await _call_service(
        service.restore_entry(user_id=_user_id(current_user), entry_id=entry_id)
    )
    return ok(_public_document(result))


@router.delete("/trash/{entry_id}")
async def permanently_delete_entry(
    entry_id: str,
    current_user: dict = Depends(get_current_user),  # noqa: B008
    service: StockResearchService = Depends(get_stock_research_service),  # noqa: B008
):
    await _call_service(
        service.permanently_delete_entry(
            user_id=_user_id(current_user), entry_id=entry_id
        )
    )
    return ok()
