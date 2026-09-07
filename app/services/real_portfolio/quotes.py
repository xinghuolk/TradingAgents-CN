"""Optional current-quote enrichment for real portfolio holdings."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from app.services.real_portfolio.models import (
    LatestQuote,
    PortfolioView,
    SecurityId,
)

if TYPE_CHECKING:
    from app.services.unified_stock_service import UnifiedStockService

logger = logging.getLogger(__name__)

_API_MARKETS = {"A": "CN", "HK": "HK"}
_CURRENCIES = {"A": "CNY", "HK": "HKD"}


def _positive_price(document: Mapping[str, object]) -> Decimal | None:
    for field in ("price", "current_price", "close"):
        value = document.get(field)
        if value is None or isinstance(value, bool):
            continue
        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if price.is_finite() and price > 0:
            return price
    return None


def _safe_datetime(value: object) -> datetime | None:
    if type(value) is datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if type(value) is date:
        return datetime.combine(value, time.min, tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _observed_at(document: Mapping[str, object]) -> datetime | None:
    for field in ("updated_at", "trade_date"):
        observed_at = _safe_datetime(document.get(field))
        if observed_at is not None:
            return observed_at
    return None


async def _load_quote(
    quote_service: "UnifiedStockService", security: SecurityId
) -> tuple[SecurityId, LatestQuote | None]:
    try:
        document = await quote_service.get_stock_quote(
            _API_MARKETS[security.market], security.code
        )
        if not isinstance(document, Mapping):
            return security, None
        price = _positive_price(document)
        if price is None:
            return security, None
        return security, LatestQuote(
            price=price,
            currency=_CURRENCIES[security.market],
            observed_at=_observed_at(document),
        )
    except Exception as error:  # noqa: BLE001 - quote enrichment is best effort
        logger.warning(
            "Latest portfolio quote unavailable for %s: %s",
            security,
            type(error).__name__,
        )
        return security, None


async def load_latest_quotes(
    quote_service: "UnifiedStockService | None", securities: Sequence[SecurityId]
) -> Mapping[SecurityId, LatestQuote]:
    """Read cached quotes without allowing a quote failure to fail holdings."""
    if quote_service is None:
        return {}
    unique = tuple(dict.fromkeys(securities))
    loaded = await asyncio.gather(
        *(_load_quote(quote_service, security) for security in unique)
    )
    return {security: quote for security, quote in loaded if quote is not None}


def attach_latest_quotes(
    view: PortfolioView, quotes: Mapping[SecurityId, LatestQuote]
) -> PortfolioView:
    """Attach only current quote fields, leaving broker valuation untouched."""
    return replace(
        view,
        holdings=tuple(
            replace(
                holding,
                latest_quote_price=quote.price,
                latest_quote_currency=quote.currency,
                quote_as_of=quote.observed_at,
            )
            if (quote := quotes.get(holding.security)) is not None
            else holding
            for holding in view.holdings
        ),
    )
