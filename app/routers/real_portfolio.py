"""Authenticated API contract for the personal real portfolio."""

import logging
from collections.abc import Awaitable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, TypeVar

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from app.core.config import settings
from app.core.database import get_mongo_db
from app.core.response import ok
from app.routers.auth_db import get_current_user
from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import (
    Holding,
    ImportHistoryItem,
    ImportSummary,
    Page,
    ParseWarning,
    PortfolioView,
    SecurityId,
    TradeFilters,
    TradeItem,
    canonical_decimal_string,
)
from app.services.real_portfolio.service import RealPortfolioService
from app.services.real_portfolio.storage import RealPortfolioRepository
from app.services.unified_stock_service import UnifiedStockService

router = APIRouter(prefix="/real-portfolio", tags=["real-portfolio"])
logger = logging.getLogger(__name__)

ERROR_STATUS = {
    "INVALID_ENCODING": 400,
    "UNSUPPORTED_FORMAT": 400,
    "SNAPSHOT_DATE_REQUIRED": 422,
    "DELIVERY_AS_OF_NOT_ALLOWED": 400,
    "SNAPSHOT_DATE_CONFLICT": 409,
    "NO_USABLE_ROWS": 400,
    "NO_FULL_SNAPSHOT": 404,
    "UPLOAD_TOO_LARGE": 413,
    "PORTFOLIO_STORAGE_UNAVAILABLE": 503,
}

_SAFE_ERROR_CONTEXT = {
    "SNAPSHOT_DATE_REQUIRED": ("source_type",),
    "SNAPSHOT_DATE_CONFLICT": ("observed_on",),
}
_T = TypeVar("_T")


def get_real_portfolio_service(
    db=Depends(get_mongo_db),  # noqa: B008 - FastAPI dependency declaration
) -> RealPortfolioService:
    repository = RealPortfolioRepository(db)
    quote_service = UnifiedStockService(db)
    return RealPortfolioService(
        repository, Path(settings.TRADINGAGENTS_DATA_DIR), quote_service
    )


def _internal_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={
            "code": "INTERNAL_ERROR",
            "message": "real portfolio request failed",
        },
    )


def _portfolio_error(error: PortfolioError) -> HTTPException:
    status_code = ERROR_STATUS.get(error.code)
    if status_code is None:
        logger.warning("Unmapped real portfolio error (PortfolioError)")
        return _internal_error()

    detail = {"code": error.code, "message": error.message}
    for key in _SAFE_ERROR_CONTEXT.get(error.code, ()):
        value = error.context.get(key)
        if isinstance(value, str):
            detail[key] = value
    return HTTPException(status_code=status_code, detail=detail)


async def _call_service(operation: Awaitable[_T]) -> _T:
    try:
        return await operation
    except PortfolioError as error:
        raise _portfolio_error(error) from None
    except Exception as error:
        logger.error(
            "Unexpected real portfolio service failure (%s)", type(error).__name__
        )
        raise _internal_error() from None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal(value: Decimal | None) -> str | None:
    return canonical_decimal_string(value) if value is not None else None


def _warning(item: ParseWarning) -> dict[str, object]:
    return {
        "type": item.warning_type,
        "message": item.message,
        "affects_quantity": item.affects_quantity,
        "impact_from": _iso(item.impact_from),
        "impact_through": _iso(item.impact_through),
    }


def _import_summary(item: ImportSummary) -> dict[str, object]:
    return {
        "status": item.status,
        "source_type": item.source_type,
        "format_id": item.format_id,
        "file_sha256_short": item.file_sha256_short,
        "source_rows": item.source_rows,
        "usable_rows": item.usable_rows,
        "new_facts": item.new_facts,
        "duplicate_facts": item.duplicate_facts,
        "conflicting_facts": item.conflicting_facts,
        "events": item.events,
        "partial_events": item.partial_events,
        "unclassified_events": item.unclassified_events,
        "warnings": [_warning(warning) for warning in item.warnings],
    }


def _api_market(security: SecurityId) -> str:
    return "CN" if security.market == "A" else security.market


def _holding(item: Holding) -> dict[str, object]:
    return {
        "security_id": str(item.security),
        "market": _api_market(item.security),
        "code": item.security.code,
        "name": item.broker_name,
        "quantity": _decimal(item.quantity),
        "available_quantity": _decimal(item.available_quantity),
        "reference_cost": _decimal(item.reference_cost),
        "reference_cost_currency": item.reference_cost_currency,
        "broker_market_price": _decimal(item.market_price),
        "broker_market_price_currency": item.market_price_currency,
        "snapshot_market_value": _decimal(item.market_value),
        "snapshot_market_value_currency": item.market_value_currency,
        "snapshot_unrealized_pnl": _decimal(item.total_profit_loss),
        "snapshot_pnl_currency": item.profit_loss_currency,
        "latest_quote_price": _decimal(item.latest_quote_price),
        "latest_quote_currency": item.latest_quote_currency,
        "quote_as_of": _iso(item.quote_as_of),
    }


def _positions(item: PortfolioView) -> dict[str, object]:
    return {
        "account_alias": "main",
        "requested_date": _iso(item.as_of),
        "anchor_date": _iso(item.anchor_date),
        "direction": item.direction,
        "completeness": item.completeness,
        "reported_coverage": [
            {"from": start.isoformat(), "through": end.isoformat()}
            for start, end in item.reported_coverage
        ],
        "warning_count": len(item.warnings),
        "warnings": [_warning(warning) for warning in item.warnings],
        "items": [_holding(holding) for holding in item.holdings],
    }


def _trade(item: TradeItem) -> dict[str, object]:
    security = item.security
    return {
        "id": item.id,
        "trade_date": _iso(item.trade_date),
        "settlement_date": _iso(item.settlement_date),
        "security_id": str(security) if security is not None else None,
        "market": _api_market(security) if security is not None else None,
        "code": security.code if security is not None else None,
        "name": item.name,
        "event_type": item.event_type,
        "operation_label": item.operation_label,
        "security_quantity": _decimal(item.security_quantity),
        "trade_currency": item.trade_currency,
        "cash_movements": [
            {"currency": movement.currency, "amount": _decimal(movement.amount)}
            for movement in item.cash_movements
        ],
        "completeness": item.completeness,
        "warnings": [_warning(warning) for warning in item.warnings],
    }


def _trade_page(page: Page[TradeItem]) -> dict[str, object]:
    return {
        "items": [_trade(item) for item in page.items],
        "page": page.page,
        "page_size": page.page_size,
        "total": page.total,
    }


def _history_item(item: ImportHistoryItem) -> dict[str, object]:
    return {
        "completed_at": _iso(item.completed_at),
        "source_type": item.source_type,
        "format_id": item.format_id,
        "source_filename": item.source_filename,
        "file_sha256_short": item.file_sha256_short,
        "row_count": item.row_count,
        "observed_on": _iso(item.observed_on),
        "coverage_from": _iso(item.coverage_from),
        "coverage_through": _iso(item.coverage_through),
        "status": item.status,
        "warning_count": item.warning_count,
    }


def _history_page(page: Page[ImportHistoryItem]) -> dict[str, object]:
    return {
        "items": [_history_item(item) for item in page.items],
        "page": page.page,
        "page_size": page.page_size,
        "total": page.total,
    }


@router.post("/import")
async def import_portfolio(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI request declaration
    as_of: date | None = Form(None),  # noqa: B008 - FastAPI request declaration
    dry_run: bool = Form(False),
    current_user: dict = Depends(  # noqa: B008 - FastAPI dependency declaration
        get_current_user
    ),
    service: RealPortfolioService = Depends(  # noqa: B008 - FastAPI dependency declaration
        get_real_portfolio_service
    ),
):
    if not getattr(request.app.state, "real_portfolio_import_ready", False):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PORTFOLIO_STORAGE_UNAVAILABLE",
                "message": "portfolio imports are unavailable",
            },
        )

    content = await file.read(settings.MAX_UPLOAD_SIZE + 1)
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "UPLOAD_TOO_LARGE",
                "message": "file exceeds the configured upload limit",
            },
        )

    operation = service.preview_file if dry_run else service.import_file
    summary = await _call_service(
        operation(
            user_id=str(current_user["id"]),
            filename=file.filename or "portfolio.xls",
            content=content,
            as_of=as_of,
        )
    )
    return ok(_import_summary(summary))


@router.get("/positions")
async def get_positions(
    as_of: date | None = None,
    current_user: dict = Depends(  # noqa: B008 - FastAPI dependency declaration
        get_current_user
    ),
    service: RealPortfolioService = Depends(  # noqa: B008 - FastAPI dependency declaration
        get_real_portfolio_service
    ),
):
    positions = await _call_service(
        service.get_positions(user_id=str(current_user["id"]), as_of=as_of)
    )
    return ok(_positions(positions))


@router.get("/trades")
async def get_trades(
    date_from: date | None = None,
    date_through: date | None = None,
    market: Literal["CN", "HK"] | None = None,
    security_id: str | None = Query(
        default=None, pattern=r"^(A:[0-9]{6}|HK:[0-9]{5})$"
    ),
    event_type: str | None = None,
    completeness: Literal["complete", "partial", "informational", "unclassified"]
    | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(  # noqa: B008 - FastAPI dependency declaration
        get_current_user
    ),
    service: RealPortfolioService = Depends(  # noqa: B008 - FastAPI dependency declaration
        get_real_portfolio_service
    ),
):
    filters = TradeFilters(
        date_from=date_from,
        date_through=date_through,
        market="A" if market == "CN" else market,
        security_id=SecurityId.parse(security_id) if security_id is not None else None,
        event_type=event_type,
        completeness=completeness,
    )
    trades = await _call_service(
        service.list_trades(
            user_id=str(current_user["id"]),
            filters=filters,
            page=page,
            page_size=page_size,
        )
    )
    return ok(_trade_page(trades))


@router.get("/imports")
async def get_imports(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(  # noqa: B008 - FastAPI dependency declaration
        get_current_user
    ),
    service: RealPortfolioService = Depends(  # noqa: B008 - FastAPI dependency declaration
        get_real_portfolio_service
    ),
):
    imports = await _call_service(
        service.list_imports(
            user_id=str(current_user["id"]), page=page, page_size=page_size
        )
    )
    return ok(_history_page(imports))
