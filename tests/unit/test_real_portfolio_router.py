from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.routers import real_portfolio
from app.routers.auth_db import get_current_user
from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import (
    CurrencyMovement,
    Holding,
    ImportHistoryItem,
    ImportSummary,
    Page,
    ParseWarning,
    PortfolioView,
    SecurityId,
    TradeFilters,
    TradeItem,
)
from app.services.real_portfolio.service import RealPortfolioService
from tests.unit.real_portfolio.fixtures import snapshot_bytes


def create_test_app(
    service: AsyncMock,
    *,
    authenticated: bool = True,
    import_ready: bool = True,
) -> FastAPI:
    application = FastAPI()
    application.state.real_portfolio_import_ready = import_ready
    application.include_router(real_portfolio.router, prefix="/api")

    async def service_dependency() -> AsyncMock:
        return service

    application.dependency_overrides[real_portfolio.get_real_portfolio_service] = (
        service_dependency
    )
    if authenticated:

        async def authenticated_user() -> dict[str, object]:
            return {
                "id": "user-1",
                "username": "personal",
                "email": "personal@example.test",
                "name": "personal",
                "is_admin": False,
                "roles": ["user"],
                "preferences": {},
            }

        application.dependency_overrides[get_current_user] = authenticated_user
    return application


def create_test_client(application: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    )


def test_service_dependency_uses_the_configured_data_root(monkeypatch) -> None:
    database = object()
    monkeypatch.setattr(
        real_portfolio.settings, "TRADINGAGENTS_DATA_DIR", "/srv/portfolio-data"
    )

    service = real_portfolio.get_real_portfolio_service(database)

    assert service.archive_root == Path("/srv/portfolio-data")


def warning() -> ParseWarning:
    return ParseWarning(
        warning_type="coverage_gap",
        import_id="private-import-id",
        line_number=88,
        impact_from=date(2026, 8, 1),
        impact_through=date(2026, 8, 31),
        message="reported delivery coverage is incomplete",
        affects_quantity=True,
    )


def import_summary(status: str = "imported") -> ImportSummary:
    return ImportSummary(
        status=status,
        source_type="snapshot",
        format_id="guotai-snapshot-v1",
        file_sha256_short="fb35e1e12345",
        source_rows=18,
        usable_rows=17,
        new_facts=16,
        duplicate_facts=1,
        conflicting_facts=0,
        events=2,
        partial_events=1,
        unclassified_events=0,
        warnings=(warning(),),
    )


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs"),
    [
        (
            "post",
            "/api/real-portfolio/import",
            {"files": {"file": ("position.xls", snapshot_bytes())}},
        ),
        ("get", "/api/real-portfolio/positions", {}),
        ("get", "/api/real-portfolio/trades", {}),
        ("get", "/api/real-portfolio/imports", {}),
    ],
)
async def test_every_real_portfolio_endpoint_requires_authentication(
    method: str, path: str, request_kwargs: dict[str, object]
) -> None:
    service = AsyncMock(spec=RealPortfolioService)
    async with create_test_client(
        create_test_app(service, authenticated=False)
    ) as client:
        response = await getattr(client, method)(path, **request_kwargs)

    assert response.status_code == 401


async def test_positions_use_authenticated_user_and_serialize_the_public_contract() -> (
    None
):
    service = AsyncMock(spec=RealPortfolioService)
    service.get_positions.return_value = PortfolioView(
        as_of=date(2026, 9, 6),
        anchor_date=date(2026, 9, 1),
        direction="forward",
        holdings=(
            Holding(
                security=SecurityId("A", "600519"),
                broker_name="Kweichow Moutai",
                route="Shanghai",
                quantity=Decimal("100.000"),
                available_quantity=Decimal("90.00"),
                frozen_quantity=Decimal(10),
                reference_cost=Decimal("1500.00"),
                reference_cost_currency="CNY",
                market_price=Decimal("1600.00"),
                market_price_currency="CNY",
                market_value=Decimal("160000.00"),
                market_value_currency="CNY",
                total_profit_loss=Decimal("10000.00"),
                profit_loss_currency="CNY",
                profit_loss_percent=Decimal("6.67"),
                daily_profit_loss=Decimal("200.00"),
                daily_profit_loss_percent=Decimal("0.13"),
                position_weight_percent=Decimal(50),
                same_day_buy=Decimal(0),
                same_day_sell=Decimal(0),
                profit_loss_price=Decimal(100),
                latest_quote_price=Decimal("1612.00"),
                latest_quote_currency="CNY",
                quote_as_of=datetime(2026, 9, 7, 15, 0, tzinfo=UTC),
            ),
        ),
        reported_coverage=((date(2026, 8, 1), date(2026, 9, 6)),),
        completeness="reported_coverage",
        warnings=(warning(),),
    )

    async with create_test_client(create_test_app(service)) as client:
        response = await client.get(
            "/api/real-portfolio/positions", params={"as_of": "2026-09-06"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["message"] == "ok"
    assert isinstance(payload["timestamp"], str)
    assert payload["data"] == {
        "account_alias": "main",
        "requested_date": "2026-09-06",
        "anchor_date": "2026-09-01",
        "direction": "forward",
        "completeness": "reported_coverage",
        "reported_coverage": [{"from": "2026-08-01", "through": "2026-09-06"}],
        "warning_count": 1,
        "warnings": [
            {
                "type": "coverage_gap",
                "message": "reported delivery coverage is incomplete",
                "affects_quantity": True,
                "impact_from": "2026-08-01",
                "impact_through": "2026-08-31",
            }
        ],
        "items": [
            {
                "security_id": "A:600519",
                "market": "CN",
                "code": "600519",
                "name": "Kweichow Moutai",
                "quantity": "100",
                "available_quantity": "90",
                "reference_cost": "1500",
                "reference_cost_currency": "CNY",
                "broker_market_price": "1600",
                "broker_market_price_currency": "CNY",
                "snapshot_market_value": "160000",
                "snapshot_market_value_currency": "CNY",
                "snapshot_unrealized_pnl": "10000",
                "snapshot_pnl_currency": "CNY",
                "latest_quote_price": "1612",
                "latest_quote_currency": "CNY",
                "quote_as_of": "2026-09-07T15:00:00+00:00",
            }
        ],
    }
    service.get_positions.assert_awaited_once_with(
        user_id="user-1", as_of=date(2026, 9, 6)
    )
    serialized = response.text
    assert "private-import-id" not in serialized
    assert "line_number" not in serialized


async def test_dry_run_uses_preview_and_never_dispatches_a_persisting_import() -> None:
    service = AsyncMock(spec=RealPortfolioService)
    service.preview_file.return_value = import_summary("preview")
    service.import_file.return_value = import_summary("imported")
    content = snapshot_bytes()

    async with create_test_client(create_test_app(service)) as client:
        response = await client.post(
            "/api/real-portfolio/import",
            files={"file": ("position.xls", content)},
            data={"as_of": "2026-09-06", "dry_run": "true"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "preview"
    assert response.json()["data"]["warnings"] == [
        {
            "type": "coverage_gap",
            "message": "reported delivery coverage is incomplete",
            "affects_quantity": True,
            "impact_from": "2026-08-01",
            "impact_through": "2026-08-31",
        }
    ]
    service.preview_file.assert_awaited_once_with(
        user_id="user-1",
        filename="position.xls",
        content=content,
        as_of=date(2026, 9, 6),
    )
    service.import_file.assert_not_awaited()


async def test_non_dry_run_dispatches_the_import_for_the_authenticated_user() -> None:
    service = AsyncMock(spec=RealPortfolioService)
    service.import_file.return_value = import_summary()
    content = snapshot_bytes()

    async with create_test_client(create_test_app(service)) as client:
        response = await client.post(
            "/api/real-portfolio/import",
            files={"file": ("../position.xls", content)},
            data={"as_of": "2026-09-06"},
        )

    assert response.status_code == 200
    service.import_file.assert_awaited_once_with(
        user_id="user-1",
        filename="../position.xls",
        content=content,
        as_of=date(2026, 9, 6),
    )
    service.preview_file.assert_not_awaited()


async def test_upload_reads_only_one_byte_past_the_configured_limit(
    monkeypatch,
) -> None:
    service = AsyncMock(spec=RealPortfolioService)
    read_sizes: list[int] = []
    original_read = StarletteUploadFile.read

    async def recording_read(self, size: int = -1) -> bytes:
        read_sizes.append(size)
        return await original_read(self, size)

    monkeypatch.setattr(real_portfolio.settings, "MAX_UPLOAD_SIZE", 4)
    monkeypatch.setattr(StarletteUploadFile, "read", recording_read)

    async with create_test_client(create_test_app(service)) as client:
        response = await client.post(
            "/api/real-portfolio/import",
            files={"file": ("position.xls", b"12345-and-more")},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == {
        "code": "UPLOAD_TOO_LARGE",
        "message": "file exceeds the configured upload limit",
    }
    assert read_sizes == [5]
    service.preview_file.assert_not_awaited()
    service.import_file.assert_not_awaited()


@pytest.mark.parametrize(
    ("code", "status_code", "context", "expected_context"),
    [
        ("INVALID_ENCODING", 400, {"private": "secret"}, {}),
        ("UNSUPPORTED_FORMAT", 400, {"private": "secret"}, {}),
        (
            "SNAPSHOT_DATE_REQUIRED",
            422,
            {"source_type": "snapshot", "private": "secret"},
            {"source_type": "snapshot"},
        ),
        ("DELIVERY_AS_OF_NOT_ALLOWED", 400, {"private": "secret"}, {}),
        (
            "SNAPSHOT_DATE_CONFLICT",
            409,
            {"observed_on": "2026-09-01", "private": "secret"},
            {"observed_on": "2026-09-01"},
        ),
        ("NO_USABLE_ROWS", 400, {"private": "secret"}, {}),
        ("UPLOAD_TOO_LARGE", 413, {"private": "secret"}, {}),
        ("PORTFOLIO_STORAGE_UNAVAILABLE", 503, {"private": "secret"}, {}),
    ],
)
async def test_prescribed_portfolio_errors_have_structured_sanitized_responses(
    code: str,
    status_code: int,
    context: dict[str, str],
    expected_context: dict[str, str],
) -> None:
    service = AsyncMock(spec=RealPortfolioService)
    service.import_file.side_effect = PortfolioError(code, "safe message", context)

    async with create_test_client(create_test_app(service)) as client:
        response = await client.post(
            "/api/real-portfolio/import",
            files={"file": ("position.xls", snapshot_bytes())},
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == {
        "code": code,
        "message": "safe message",
        **expected_context,
    }
    assert "secret" not in response.text


@pytest.mark.parametrize(
    "failure",
    [
        PortfolioError("PRIVATE_DATABASE_FAILURE", "mongodb password leaked"),
        RuntimeError("mongodb password leaked"),
    ],
)
async def test_unexpected_service_failures_do_not_leak_internal_details(
    failure: Exception,
) -> None:
    service = AsyncMock(spec=RealPortfolioService)
    service.get_positions.side_effect = failure

    async with create_test_client(create_test_app(service)) as client:
        response = await client.get("/api/real-portfolio/positions")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "INTERNAL_ERROR",
        "message": "real portfolio request failed",
    }
    assert "mongodb" not in response.text
    assert "PRIVATE_DATABASE_FAILURE" not in response.text


async def test_index_readiness_gates_imports_but_not_existing_portfolio_reads() -> None:
    service = AsyncMock(spec=RealPortfolioService)
    service.get_positions.return_value = PortfolioView(
        as_of=date(2026, 9, 6),
        anchor_date=None,
        direction="partial_snapshot",
        holdings=(),
        reported_coverage=(),
        completeness="incomplete",
        warnings=(),
    )

    async with create_test_client(
        create_test_app(service, import_ready=False)
    ) as client:
        positions = await client.get("/api/real-portfolio/positions")
        imported = await client.post(
            "/api/real-portfolio/import",
            files={"file": ("position.xls", snapshot_bytes())},
        )

    assert positions.status_code == 200
    assert imported.status_code == 503
    assert imported.json()["detail"] == {
        "code": "PORTFOLIO_STORAGE_UNAVAILABLE",
        "message": "portfolio imports are unavailable",
    }
    service.get_positions.assert_awaited_once_with(user_id="user-1", as_of=None)
    service.import_file.assert_not_awaited()


async def test_trade_filters_pagination_and_items_follow_the_public_contract() -> None:
    service = AsyncMock(spec=RealPortfolioService)
    service.list_trades.return_value = Page(
        items=(
            TradeItem(
                id="opaque-ui-key",
                trade_date=date(2026, 8, 12),
                settlement_date=date(2026, 8, 14),
                security=SecurityId("HK", "00700"),
                name="Tencent",
                event_type="trade",
                operation_label="security buy",
                security_quantity=Decimal("100.00"),
                trade_currency="HKD",
                cash_movements=(CurrencyMovement("CNY", Decimal("-35000.00")),),
                completeness="complete",
                warnings=(warning(),),
            ),
        ),
        page=2,
        page_size=25,
        total=26,
    )

    async with create_test_client(create_test_app(service)) as client:
        response = await client.get(
            "/api/real-portfolio/trades",
            params={
                "date_from": "2026-08-01",
                "date_through": "2026-08-31",
                "market": "CN",
                "security_id": "A:600519",
                "event_type": "trade",
                "completeness": "complete",
                "page": 2,
                "page_size": 25,
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "items": [
            {
                "id": "opaque-ui-key",
                "trade_date": "2026-08-12",
                "settlement_date": "2026-08-14",
                "security_id": "HK:00700",
                "market": "HK",
                "code": "00700",
                "name": "Tencent",
                "event_type": "trade",
                "operation_label": "security buy",
                "security_quantity": "100",
                "trade_currency": "HKD",
                "cash_movements": [{"currency": "CNY", "amount": "-35000"}],
                "completeness": "complete",
                "warnings": [
                    {
                        "type": "coverage_gap",
                        "message": "reported delivery coverage is incomplete",
                        "affects_quantity": True,
                        "impact_from": "2026-08-01",
                        "impact_through": "2026-08-31",
                    }
                ],
            }
        ],
        "page": 2,
        "page_size": 25,
        "total": 26,
    }
    service.list_trades.assert_awaited_once_with(
        user_id="user-1",
        filters=TradeFilters(
            date_from=date(2026, 8, 1),
            date_through=date(2026, 8, 31),
            market="A",
            security_id=SecurityId("A", "600519"),
            event_type="trade",
            completeness="complete",
        ),
        page=2,
        page_size=25,
    )


async def test_import_history_serializes_timestamps_dates_and_pagination() -> None:
    service = AsyncMock(spec=RealPortfolioService)
    service.list_imports.return_value = Page(
        items=(
            ImportHistoryItem(
                completed_at=datetime(2026, 9, 6, 10, 30, tzinfo=UTC),
                source_type="snapshot",
                format_id="guotai-snapshot-v1",
                source_filename="position.xls",
                file_sha256_short="fb35e1e12345",
                row_count=18,
                observed_on=date(2026, 9, 6),
                coverage_from=None,
                coverage_through=None,
                status="imported",
                warning_count=0,
            ),
        ),
        page=3,
        page_size=20,
        total=41,
    )

    async with create_test_client(create_test_app(service)) as client:
        response = await client.get(
            "/api/real-portfolio/imports", params={"page": 3, "page_size": 20}
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "items": [
            {
                "completed_at": "2026-09-06T10:30:00+00:00",
                "source_type": "snapshot",
                "format_id": "guotai-snapshot-v1",
                "source_filename": "position.xls",
                "file_sha256_short": "fb35e1e12345",
                "row_count": 18,
                "observed_on": "2026-09-06",
                "coverage_from": None,
                "coverage_through": None,
                "status": "imported",
                "warning_count": 0,
            }
        ],
        "page": 3,
        "page_size": 20,
        "total": 41,
    }
    service.list_imports.assert_awaited_once_with(
        user_id="user-1", page=3, page_size=20
    )


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/real-portfolio/trades", {"page": 0}),
        ("/api/real-portfolio/trades", {"page_size": 201}),
        ("/api/real-portfolio/imports", {"page": 0}),
        ("/api/real-portfolio/imports", {"page_size": 201}),
    ],
)
async def test_pagination_is_bounded_before_calling_the_service(
    path: str, params: dict[str, int]
) -> None:
    service = AsyncMock(spec=RealPortfolioService)
    async with create_test_client(create_test_app(service)) as client:
        response = await client.get(path, params=params)

    assert response.status_code == 422
    service.list_trades.assert_not_awaited()
    service.list_imports.assert_not_awaited()


def test_router_exposes_no_account_selector() -> None:
    service = AsyncMock(spec=RealPortfolioService)
    application = create_test_app(service)

    parameters = {
        parameter["name"]
        for path in application.openapi()["paths"].values()
        for operation in path.values()
        for parameter in operation.get("parameters", [])
    }

    assert "account" not in parameters
    assert "account_alias" not in parameters
