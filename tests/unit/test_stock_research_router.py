from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo.errors import ConnectionFailure

from app.routers import stock_research
from app.routers.auth_db import get_current_user
from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    Entry,
    EntryPatch,
    EntryQuery,
    NewEntry,
    Reference,
    ResearchPage,
    Revision,
    ThesisPatch,
    Workspace,
    ResearchWorkspaceSummary,
    WorkspaceQuery,
)
from app.services.stock_research.references import ReferenceCandidate
from app.services.stock_research.service import StockResearchService
from app.services.stock_research.storage import StockResearchRepository
from tests.unit.stock_research.fakes import FakeDatabase


NOW = datetime(2026, 9, 8, 9, 30, tzinfo=UTC)


@dataclass(frozen=True)
class ServiceCall:
    operation: str
    args: tuple[object, ...]
    kwargs: dict[str, object]

    @property
    def user_id(self) -> str:
        value = self.kwargs.get("user_id", self.args[0] if self.args else None)
        return str(value)


class ServiceSpy:
    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []
        self.error: Exception | None = None
        self.results: dict[str, object] = {
            "get_workspace": workspace(),
            "get_or_create_workspace": workspace(),
            "save_thesis_draft": workspace(body="updated thesis"),
            "save_workspace_version": revision(),
            "list_workspaces": ResearchPage(
                (
                    ResearchWorkspaceSummary.from_workspace(
                        workspace(),
                        latest_entry_type="decision",
                        latest_entry_at=NOW,
                        has_real_holding=True,
                        has_paper_holding=False,
                        watchlisted=True,
                    ),
                ),
                2,
                10,
                11,
            ),
            "get_entry": entry(),
            "create_entry": entry(),
            "update_entry_draft": entry(body="updated entry"),
            "convert_entry": entry(entry_type="research", topic="moat"),
            "confirm_entry": entry(status="confirmed", confirmed_at=NOW),
            "archive_entry": entry(status="archived", archived_at=NOW),
            "delete_entry": None,
            "list_entries": ResearchPage((entry(),), 2, 10, 11),
            "get_revision": revision(),
            "list_revisions": [revision()],
            "restore_revision": revision(reason="revision_restored", number=2),
            "list_trash": ResearchPage((entry(deleted_at=NOW),), 2, 10, 11),
            "restore_entry": entry(),
            "permanently_delete_entry": None,
            "list_candidates": [reference_candidate()],
            "recommend_trade_links": [reference_candidate(kind="paper_trade")],
            "set_decision_trade_links": entry(entry_type="decision"),
            "delete_decision_trade_link": entry(entry_type="decision"),
            "get_decision_trade_links": [
                Reference.real_trade("real-1"),
                Reference.paper_trade("paper-1"),
            ],
        }

    def __getattr__(self, operation: str):
        async def call(*args: object, **kwargs: object) -> object:
            self.calls.append(ServiceCall(operation, args, kwargs))
            if self.error is not None:
                raise self.error
            return self.results[operation]

        return call


def workspace(*, body: str = "current thesis") -> Workspace:
    return Workspace(
        user_id="private-user",
        security_id="A:600519",
        market="A",
        code="600519",
        name="Kweichow Moutai",
        body=body,
        assumptions=("pricing power",),
        risks=("valuation",),
        tags=("consumer",),
        current_revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def entry(
    *,
    entry_type: str = "note",
    body: str = "entry body",
    topic: str | None = None,
    status: str = "draft",
    confirmed_at: datetime | None = None,
    archived_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> Entry:
    return Entry(
        id="entry-1",
        user_id="private-user",
        entry_type=entry_type,
        scope="stock",
        security_id="A:600519",
        security_ids=("A:600519",),
        title="Title",
        body=body,
        status=status,
        tags=("tag",),
        external_links=("https://example.test/source",),
        references=(Reference.analysis_report("report-1", "Annual report"),),
        topic=topic,
        confirmed_at=confirmed_at,
        archived_at=archived_at,
        deleted_at=deleted_at,
        created_at=NOW,
        updated_at=NOW,
    )


def revision(*, reason: str = "manual", number: int = 1) -> Revision:
    return Revision(
        id="revision-1",
        user_id="private-user",
        target_type="workspace",
        target_id="A:600519",
        revision=number,
        snapshot={
            "user_id": "private-user",
            "market": "A",
            "body": "version body",
        },
        reason=reason,
        created_at=NOW,
    )


def reference_candidate(
    *, kind: str = "real_trade"
) -> ReferenceCandidate:
    account_type = "real" if kind == "real_trade" else "paper"
    return ReferenceCandidate(
        user_id="private-user",
        security_id="A:600519",
        kind=kind,
        source_id=f"{account_type}-1",
        account_type=account_type,
        source_date=date(2026, 9, 7),
        label=f"{account_type} buy",
        snapshot={"quantity": "100"},
    )


def create_test_app(
    service: ServiceSpy | StockResearchService, *, authenticated: bool = True
) -> FastAPI:
    application = FastAPI()
    application.include_router(stock_research.router, prefix="/api")

    async def service_dependency() -> ServiceSpy | StockResearchService:
        return service

    application.dependency_overrides[
        stock_research.get_stock_research_service
    ] = service_dependency
    application.dependency_overrides[
        stock_research.get_reference_service
    ] = service_dependency
    if authenticated:

        async def authenticated_user() -> dict[str, object]:
            return {"id": "authenticated-user"}

        application.dependency_overrides[get_current_user] = authenticated_user
    return application


def create_test_client(application: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    )


async def test_workspace_route_uses_authenticated_user() -> None:
    service_spy = ServiceSpy()
    async with create_test_client(create_test_app(service_spy)) as client:
        response = await client.get("/api/research/workspaces/A:600519")

    assert response.status_code == 200
    assert response.json()["data"]["security_id"] == "A:600519"
    assert response.json()["data"]["market"] == "CN"
    assert "user_id" not in response.json()["data"]
    assert service_spy.calls[0].user_id == "authenticated-user"


async def test_invalid_entry_is_sanitized() -> None:
    service_spy = ServiceSpy()
    service_spy.error = ResearchError(
        "INVALID_ENTRY", "decision action is required", {"private": "secret"}
    )
    async with create_test_client(create_test_app(service_spy)) as client:
        response = await client.post(
            "/api/research/entries", json={"entry_type": "decision"}
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "INVALID_ENTRY",
            "message": "decision action is required",
        }
    }
    assert "secret" not in response.text


async def test_invalid_entry_security_keeps_structured_domain_error() -> None:
    service = StockResearchService(StockResearchRepository(FakeDatabase()))
    async with create_test_client(create_test_app(service)) as client:
        response = await client.post(
            "/api/research/entries",
            json={
                "entry_type": "note",
                "security_id": "not-a-security-id",
                "title": "Invalid security",
                "body": "Body",
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "INVALID_SECURITY",
            "message": "market and code are invalid",
        }
    }


async def test_request_body_cannot_override_authenticated_user() -> None:
    service_spy = ServiceSpy()
    async with create_test_client(create_test_app(service_spy)) as client:
        response = await client.post(
            "/api/research/workspaces",
            json={
                "user_id": "attacker",
                "market": "CN",
                "code": "600519",
                "name": "Moutai",
            },
        )

    assert response.status_code == 422
    assert service_spy.calls == []


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs"),
    [
        ("get", "/api/research/workspaces", {}),
        (
            "post",
            "/api/research/workspaces",
            {"json": {"market": "CN", "code": "600519", "name": "Moutai"}},
        ),
        ("get", "/api/research/workspaces/A:600519", {}),
        ("patch", "/api/research/workspaces/A:600519", {"json": {"body": "draft"}}),
        ("post", "/api/research/workspaces/A:600519/revisions", {"json": {"label": "v1"}}),
        ("get", "/api/research/entries", {}),
        (
            "post",
            "/api/research/entries",
            {
                "json": {
                    "entry_type": "note",
                    "security_id": "A:600519",
                    "title": "T",
                    "body": "B",
                }
            },
        ),
        ("get", "/api/research/entries/entry-1", {}),
        ("patch", "/api/research/entries/entry-1", {"json": {"body": "draft"}}),
        ("post", "/api/research/entries/entry-1/convert", {"json": {"target": "research"}}),
        ("post", "/api/research/entries/entry-1/confirm", {}),
        ("post", "/api/research/entries/entry-1/archive", {}),
        ("delete", "/api/research/entries/entry-1", {}),
        ("get", "/api/research/revisions?target_type=entry&target_id=entry-1", {}),
        ("get", "/api/research/revisions/revision-1", {}),
        ("post", "/api/research/revisions/revision-1/restore", {}),
        ("get", "/api/research/trash", {}),
        ("post", "/api/research/trash/entry-1/restore", {}),
        ("delete", "/api/research/trash/entry-1", {}),
        (
            "get",
            "/api/research/references?security_id=A:600519",
            {},
        ),
        (
            "get",
            "/api/research/links/recommendations?decision_id=d1",
            {},
        ),
        (
            "put",
            "/api/research/links/decisions/d1",
            {"json": {"references": []}},
        ),
        (
            "delete",
            "/api/research/links/decisions/d1/real_trade/real-1",
            {},
        ),
    ],
)
async def test_every_research_endpoint_requires_authentication(
    method: str, path: str, request_kwargs: dict[str, object]
) -> None:
    async with create_test_client(
        create_test_app(ServiceSpy(), authenticated=False)
    ) as client:
        response = await getattr(client, method)(path, **request_kwargs)

    assert response.status_code == 401


async def test_workspace_list_translates_filters_and_serializes_page() -> None:
    service_spy = ServiceSpy()
    async with create_test_client(create_test_app(service_spy)) as client:
        response = await client.get(
            "/api/research/workspaces",
            params={
                "market": "CN",
                "query": "Moutai",
                "real_holding": "true",
                "paper_holding": "false",
                "watchlisted": "true",
                "page": 2,
                "page_size": 10,
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 11
    assert response.json()["data"]["items"][0]["updated_at"] == NOW.isoformat()
    assert response.json()["data"]["items"][0]["thesis_summary"] == "current thesis"
    assert response.json()["data"]["items"][0]["latest_entry_type"] == "decision"
    assert response.json()["data"]["items"][0]["has_real_holding"] is True
    assert response.json()["data"]["items"][0]["has_paper_holding"] is False
    assert response.json()["data"]["items"][0]["watchlisted"] is True
    assert "user_id" not in response.json()["data"]["items"][0]
    assert service_spy.calls == [
        ServiceCall(
            "list_workspaces",
            (),
            {
                "user_id": "authenticated-user",
                "query": WorkspaceQuery(
                    market="A",
                    query="Moutai",
                    real_holding=True,
                    paper_holding=False,
                    watchlisted=True,
                    page=2,
                    page_size=10,
                ),
            },
        )
    ]


async def test_reference_and_link_routes_use_authenticated_scope() -> None:
    service_spy = ServiceSpy()
    async with create_test_client(create_test_app(service_spy)) as client:
        candidates = await client.get(
            "/api/research/references",
            params={
                "security_id": "A:600519",
                "date_from": "2026-08-01",
                "date_through": "2026-09-08",
            },
        )
        recommendations = await client.get(
            "/api/research/links/recommendations", params={"decision_id": "d1"}
        )
        linked = await client.put(
            "/api/research/links/decisions/d1",
            json={
                "references": [
                    {
                        "kind": "real_trade",
                        "source_id": "real-1",
                        "account_type": "real",
                    },
                    {
                        "kind": "paper_trade",
                        "source_id": "paper-1",
                        "account_type": "paper",
                    },
                ]
            },
        )
        unlinked = await client.delete(
            "/api/research/links/decisions/d1/real_trade/real-1"
        )

    assert all(
        response.status_code == 200
        for response in (candidates, recommendations, linked, unlinked)
    )
    assert "user_id" not in candidates.json()["data"][0]
    assert candidates.json()["data"][0]["account_type"] == "real"
    assert recommendations.json()["data"][0]["account_type"] == "paper"
    assert service_spy.calls == [
        ServiceCall(
            "list_candidates",
            (),
            {
                "user_id": "authenticated-user",
                "security_id": "A:600519",
                "date_from": date(2026, 8, 1),
                "date_through": date(2026, 9, 8),
            },
        ),
        ServiceCall(
            "recommend_trade_links",
            (),
            {"user_id": "authenticated-user", "decision_id": "d1"},
        ),
        ServiceCall(
            "set_decision_trade_links",
            (),
            {
                "user_id": "authenticated-user",
                "decision_id": "d1",
                "references": [
                    Reference.real_trade("real-1"),
                    Reference.paper_trade("paper-1"),
                ],
            },
        ),
        ServiceCall(
            "delete_decision_trade_link",
            (),
            {
                "user_id": "authenticated-user",
                "decision_id": "d1",
                "reference": Reference.real_trade("real-1"),
            },
        ),
    ]


async def test_workspace_mutations_translate_request_values() -> None:
    service_spy = ServiceSpy()
    async with create_test_client(create_test_app(service_spy)) as client:
        created = await client.post(
            "/api/research/workspaces",
            json={"market": "CN", "code": "600519", "name": "Moutai"},
        )
        patched = await client.patch(
            "/api/research/workspaces/A:600519",
            json={
                "body": "updated thesis",
                "assumptions": ["pricing power"],
                "risks": ["valuation"],
                "tags": ["consumer"],
            },
        )
        saved = await client.post(
            "/api/research/workspaces/A:600519/revisions",
            json={"label": "initial thesis"},
        )

    assert created.status_code == patched.status_code == saved.status_code == 200
    assert service_spy.calls == [
        ServiceCall(
            "get_or_create_workspace",
            (),
            {
                "user_id": "authenticated-user",
                "market": "CN",
                "code": "600519",
                "name": "Moutai",
            },
        ),
        ServiceCall(
            "save_thesis_draft",
            (),
            {
                "user_id": "authenticated-user",
                "security_id": "A:600519",
                "patch": ThesisPatch(
                    body="updated thesis",
                    assumptions=("pricing power",),
                    risks=("valuation",),
                    tags=("consumer",),
                ),
            },
        ),
        ServiceCall(
            "save_workspace_version",
            (),
            {
                "user_id": "authenticated-user",
                "security_id": "A:600519",
                "label": "initial thesis",
            },
        ),
    ]


async def test_entry_list_and_mutations_translate_the_complete_contract() -> None:
    service_spy = ServiceSpy()
    reference = {
        "kind": "decision",
        "source_id": "decision-0",
        "source_date": "2026-08-01",
        "label": "Earlier decision",
    }
    async with create_test_client(create_test_app(service_spy)) as client:
        listed = await client.get(
            "/api/research/entries",
            params={
                "security_id": "A:600519",
                "entry_type": "review",
                "status": "confirmed",
                "scope": "stock",
                "query": "pricing",
                "page": 2,
                "page_size": 10,
            },
        )
        created = await client.post(
            "/api/research/entries",
            json={
                "entry_type": "review",
                "scope": "stock",
                "security_id": "A:600519",
                "security_ids": ["A:600519"],
                "title": "Review",
                "body": "Body",
                "references": [reference],
                "review_kind": "decision",
            },
        )
        fetched = await client.get("/api/research/entries/entry-1")
        patched = await client.patch(
            "/api/research/entries/entry-1",
            json={"body": "updated entry", "references": [reference]},
        )
        converted = await client.post(
            "/api/research/entries/entry-1/convert",
            json={"target": "research", "topic": "moat"},
        )
        confirmed = await client.post("/api/research/entries/entry-1/confirm")
        archived = await client.post("/api/research/entries/entry-1/archive")
        deleted = await client.delete("/api/research/entries/entry-1")

    assert all(
        response.status_code == 200
        for response in (
            listed,
            created,
            fetched,
            patched,
            converted,
            confirmed,
            archived,
            deleted,
        )
    )
    assert deleted.json()["data"] is None
    assert listed.json()["data"]["items"][0]["decision_date"] is None
    assert "user_id" not in fetched.json()["data"]
    assert service_spy.calls[0] == ServiceCall(
        "list_entries",
        (),
        {
            "user_id": "authenticated-user",
            "query": EntryQuery(
                security_id="A:600519",
                entry_type="review",
                status="confirmed",
                scope="stock",
                query="pricing",
                page=2,
                page_size=10,
            ),
        },
    )
    new_entry = service_spy.calls[1].kwargs["request"]
    assert isinstance(new_entry, NewEntry)
    assert new_entry.review_kind == "decision"
    assert new_entry.decision_id is None
    assert new_entry.references == (
        Reference(
            kind="decision",
            source_id="decision-0",
            source_date=date(2026, 8, 1),
            label="Earlier decision",
        ),
    )
    assert service_spy.calls[2] == ServiceCall(
        "get_entry", (), {"user_id": "authenticated-user", "entry_id": "entry-1"}
    )
    assert service_spy.calls[3].kwargs["patch"] == EntryPatch(
        body="updated entry", references=new_entry.references
    )
    assert service_spy.calls[4] == ServiceCall(
        "convert_entry",
        (),
        {
            "user_id": "authenticated-user",
            "entry_id": "entry-1",
            "target": "research",
            "topic": "moat",
        },
    )
    assert [call.operation for call in service_spy.calls[5:]] == [
        "confirm_entry",
        "archive_entry",
        "delete_entry",
    ]


async def test_created_stock_entry_is_returned_by_its_primary_security_filter() -> None:
    service = StockResearchService(
        StockResearchRepository(FakeDatabase()),
        clock=lambda: NOW,
        id_factory=lambda: "entry-primary",
    )
    async with create_test_client(create_test_app(service)) as client:
        created = await client.post(
            "/api/research/entries",
            json={
                "entry_type": "note",
                "security_id": "CN:600519",
                "title": "Primary security",
                "body": "Created without an explicit security_ids list.",
            },
        )
        listed = await client.get(
            "/api/research/entries", params={"security_id": "A:600519"}
        )

    assert created.status_code == listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert [item["id"] for item in listed.json()["data"]["items"]] == [
        "entry-primary"
    ]
    assert created.json()["data"]["security_id"] == "A:600519"
    assert created.json()["data"]["security_ids"] == ["A:600519"]


async def test_created_portfolio_review_preserves_canonical_unique_securities() -> None:
    service = StockResearchService(
        StockResearchRepository(FakeDatabase()),
        clock=lambda: NOW,
        id_factory=lambda: "entry-portfolio",
    )
    async with create_test_client(create_test_app(service)) as client:
        created = await client.post(
            "/api/research/entries",
            json={
                "entry_type": "review",
                "scope": "portfolio",
                "security_id": "CN:600519",
                "security_ids": [
                    "A:600519",
                    "us:aapl",
                    "US:AAPL",
                    "HK:0700",
                ],
                "body": "Portfolio review",
                "review_kind": "routine",
            },
        )
        listed = await client.get(
            "/api/research/entries", params={"security_id": "us:aapl"}
        )

    assert created.status_code == listed.status_code == 200
    assert created.json()["data"]["security_id"] == "A:600519"
    assert created.json()["data"]["security_ids"] == [
        "A:600519",
        "US:AAPL",
        "HK:0700",
    ]
    assert [item["id"] for item in listed.json()["data"]["items"]] == [
        "entry-portfolio"
    ]


async def test_revision_and_trash_routes_translate_and_serialize() -> None:
    service_spy = ServiceSpy()
    async with create_test_client(create_test_app(service_spy)) as client:
        revisions = await client.get(
            "/api/research/revisions",
            params={"target_type": "workspace", "target_id": "A:600519"},
        )
        fetched = await client.get("/api/research/revisions/revision-1")
        restored = await client.post(
            "/api/research/revisions/revision-1/restore"
        )
        trash = await client.get(
            "/api/research/trash", params={"page": 2, "page_size": 10}
        )
        restored_entry = await client.post(
            "/api/research/trash/entry-1/restore"
        )
        deleted = await client.delete("/api/research/trash/entry-1")

    assert all(
        response.status_code == 200
        for response in (
            revisions,
            fetched,
            restored,
            trash,
            restored_entry,
            deleted,
        )
    )
    assert revisions.json()["data"][0]["created_at"] == NOW.isoformat()
    assert "user_id" not in fetched.json()["data"]
    assert "user_id" not in fetched.json()["data"]["snapshot"]
    assert fetched.json()["data"]["snapshot"]["market"] == "CN"
    assert restored.json()["data"]["reason"] == "revision_restored"
    assert trash.json()["data"]["items"][0]["deleted_at"] == NOW.isoformat()
    assert deleted.json()["data"] is None
    assert service_spy.calls == [
        ServiceCall(
            "list_revisions",
            (),
            {
                "user_id": "authenticated-user",
                "target_type": "workspace",
                "target_id": "A:600519",
            },
        ),
        ServiceCall(
            "get_revision",
            (),
            {"user_id": "authenticated-user", "revision_id": "revision-1"},
        ),
        ServiceCall(
            "restore_revision",
            (),
            {"user_id": "authenticated-user", "revision_id": "revision-1"},
        ),
        ServiceCall(
            "list_trash",
            (),
            {"user_id": "authenticated-user", "page": 2, "page_size": 10},
        ),
        ServiceCall(
            "restore_entry",
            (),
            {"user_id": "authenticated-user", "entry_id": "entry-1"},
        ),
        ServiceCall(
            "permanently_delete_entry",
            (),
            {"user_id": "authenticated-user", "entry_id": "entry-1"},
        ),
    ]


async def test_revision_routes_recursively_sanitize_snapshots_without_mutation() -> None:
    stored_snapshot = {
        "user_id": "revision-owner",
        "market": "A",
        "thesis_snapshot": {
            "user_id": "workspace-owner",
            "market": "A",
            "evidence": [
                {
                    "user_id": "source-owner",
                    "market": "A",
                    "signal": "A",
                }
            ],
        },
    }
    expected_stored_snapshot = deepcopy(stored_snapshot)
    stored_revision = Revision(
        id="nested-revision",
        user_id="private-user",
        target_type="entry",
        target_id="entry-1",
        revision=1,
        snapshot=stored_snapshot,
        reason="decision_confirmed",
        created_at=NOW,
    )
    service_spy = ServiceSpy()
    service_spy.results["list_revisions"] = [stored_revision]
    service_spy.results["get_revision"] = stored_revision

    async with create_test_client(create_test_app(service_spy)) as client:
        listed = await client.get(
            "/api/research/revisions",
            params={"target_type": "entry", "target_id": "entry-1"},
        )
        fetched = await client.get("/api/research/revisions/nested-revision")

    assert listed.status_code == fetched.status_code == 200
    for public_revision in (listed.json()["data"][0], fetched.json()["data"]):
        assert "user_id" not in public_revision
        assert "user_id" not in public_revision["snapshot"]
        assert public_revision["snapshot"]["market"] == "CN"
        thesis_snapshot = public_revision["snapshot"]["thesis_snapshot"]
        assert "user_id" not in thesis_snapshot
        assert thesis_snapshot["market"] == "CN"
        assert "user_id" not in thesis_snapshot["evidence"][0]
        assert thesis_snapshot["evidence"][0]["market"] == "CN"
        assert thesis_snapshot["evidence"][0]["signal"] == "A"

    assert stored_revision.snapshot == expected_stored_snapshot


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (ResearchError("RESEARCH_NOT_FOUND", "not found"), 404),
        (ResearchError("RESEARCH_CONFLICT", "conflict"), 409),
        (ResearchError("INVALID_ENTRY", "invalid"), 422),
        (ResearchError("RESEARCH_STORAGE_UNAVAILABLE", "unavailable"), 503),
    ],
)
async def test_known_research_errors_have_structured_responses(
    error: ResearchError, status_code: int
) -> None:
    service_spy = ServiceSpy()
    service_spy.error = error
    async with create_test_client(create_test_app(service_spy)) as client:
        response = await client.get("/api/research/workspaces/A:600519")

    assert response.status_code == status_code
    assert response.json() == {
        "detail": {"code": error.code, "message": error.message}
    }


async def test_storage_failure_is_sanitized_as_unavailable() -> None:
    service_spy = ServiceSpy()
    service_spy.error = ConnectionFailure("mongodb password leaked")
    async with create_test_client(create_test_app(service_spy)) as client:
        response = await client.get("/api/research/workspaces/A:600519")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "RESEARCH_STORAGE_UNAVAILABLE",
            "message": "research storage is unavailable",
        }
    }
    assert "mongodb" not in response.text


@pytest.mark.parametrize(
    "failure",
    [
        ResearchError("PRIVATE_FAILURE", "mongodb password leaked"),
        RuntimeError("mongodb password leaked"),
    ],
)
async def test_unknown_failures_log_only_exception_class(
    failure: Exception,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_spy = ServiceSpy()
    service_spy.error = failure
    route_logger = stock_research.logger
    monkeypatch.setattr(route_logger, "handlers", [caplog.handler])
    monkeypatch.setattr(route_logger, "propagate", False)

    with caplog.at_level("WARNING", logger=stock_research.__name__):
        async with create_test_client(create_test_app(service_spy)) as client:
            response = await client.get("/api/research/workspaces/A:600519")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "INTERNAL_ERROR",
            "message": "research request failed",
        }
    }
    assert "mongodb" not in response.text
    assert "mongodb" not in caplog.text
    assert "Traceback" not in caplog.text
    assert type(failure).__name__ in caplog.text


def test_service_dependency_builds_repository_from_database() -> None:
    database = object()

    service = stock_research.get_stock_research_service(database)

    assert service.repository.db is database


def test_quick_harness_selects_only_focused_research_tests(tmp_path) -> None:
    from scripts.harness import build_checks

    pytest_check = next(
        check
        for check in build_checks(tmp_path, tmp_path / "runtime")
        if check.name == "Service-free pytest suite"
    )

    assert "tests/unit/stock_research" in pytest_check.command
    assert "tests/unit/test_stock_research_router.py" in pytest_check.command
    assert "tests/unit" not in pytest_check.command
    assert "tests" not in pytest_check.command


async def test_startup_ensures_research_indexes_once() -> None:
    module = ast.parse(Path("app/main.py").read_text(encoding="utf-8"))
    lifespan = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    start = next(
        index
        for index, node in enumerate(lifespan.body)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Await)
        and isinstance(node.value.value, ast.Call)
        and getattr(node.value.value.func, "id", None) == "init_db"
    )
    end = next(
        index
        for index in range(start + 1, len(lifespan.body))
        if isinstance(lifespan.body[index], ast.ImportFrom)
        and lifespan.body[index].module == "app.core.redis_client"
    )
    function = ast.AsyncFunctionDef(
        name="startup",
        args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=lifespan.body[start:end],
        decorator_list=[],
    )
    tree = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    database = object()
    repository = SimpleNamespace(ensure_indexes=AsyncMock())
    repository_factory = MagicMock(return_value=repository)
    namespace = {
        "app": SimpleNamespace(state=SimpleNamespace()),
        "init_db": AsyncMock(),
        "get_mongo_db": MagicMock(return_value=database),
        "ensure_real_portfolio_indexes": AsyncMock(),
        "StockResearchRepository": repository_factory,
        "logger": MagicMock(),
    }
    exec(compile(tree, "app/main.py", "exec"), namespace)  # noqa: S102

    await namespace["startup"]()

    repository_factory.assert_called_once_with(database)
    repository.ensure_indexes.assert_awaited_once_with()
