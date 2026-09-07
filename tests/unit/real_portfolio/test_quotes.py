from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.services.real_portfolio.formats import parse_portfolio_file
from app.services.real_portfolio.holdings import build_portfolio_view
from app.services.real_portfolio.models import ImportedFacts, LatestQuote, SecurityId
from app.services.real_portfolio.quotes import attach_latest_quotes, load_latest_quotes
from app.services.real_portfolio.reconciliation import reconcile_imports
from app.services.unified_stock_service import UnifiedStockService
from tests.unit.real_portfolio.fixtures import snapshot_bytes


class QuoteCollection:
    def __init__(self, expected_code, document):
        self.expected_code = expected_code
        self.document = document

    async def find_one(self, query, projection):
        if query != {"code": self.expected_code} or projection != {"_id": 0}:
            raise AssertionError(f"unexpected quote query: {query}, {projection}")
        return self.document


class QuoteDatabase:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]


@pytest.mark.asyncio
async def test_quotes_use_the_real_unified_service_local_collection_contract():
    database = QuoteDatabase(
        {
            "market_quotes": QuoteCollection(
                "000001",
                {
                    "code": "000001",
                    "current_price": "12.3400",
                    "updated_at": datetime(2026, 9, 7, 7, 30, tzinfo=UTC),
                },
            ),
            "market_quotes_hk": QuoteCollection(
                "00700",
                {
                    "code": "00700",
                    "close": 320.5,
                    "trade_date": "2026-09-06",
                },
            ),
        }
    )
    service = UnifiedStockService(database)

    quotes = await load_latest_quotes(
        service, (SecurityId("A", "000001"), SecurityId("HK", "00700"))
    )

    assert str(quotes[SecurityId("A", "000001")].price) == "12.3400"
    assert quotes[SecurityId("A", "000001")].currency == "CNY"
    assert quotes[SecurityId("A", "000001")].observed_at == datetime(
        2026, 9, 7, 7, 30, tzinfo=UTC
    )
    assert str(quotes[SecurityId("HK", "00700")].price) == "320.5"
    assert quotes[SecurityId("HK", "00700")].currency == "HKD"
    assert quotes[SecurityId("HK", "00700")].observed_at == datetime(
        2026, 9, 6, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_quote_failure_does_not_fail_holdings():
    quote_service = AsyncMock(spec=UnifiedStockService)
    quote_service.get_stock_quote.side_effect = RuntimeError("provider unavailable")

    quotes = await load_latest_quotes(quote_service, (SecurityId("HK", "00700"),))

    assert quotes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        {"price": "0", "updated_at": "2026-09-07T12:00:00+00:00"},
        {"price": "-1", "updated_at": "2026-09-07T12:00:00+00:00"},
        {"price": "NaN", "updated_at": "2026-09-07T12:00:00+00:00"},
        {"price": True, "updated_at": "2026-09-07T12:00:00+00:00"},
        {"price": "not-a-price", "updated_at": "2026-09-07T12:00:00+00:00"},
        {"updated_at": "2026-09-07T12:00:00+00:00"},
    ],
)
async def test_quotes_ignore_missing_or_non_positive_numeric_prices(document):
    quote_service = AsyncMock(spec=UnifiedStockService)
    quote_service.get_stock_quote.return_value = document

    quotes = await load_latest_quotes(quote_service, (SecurityId("A", "000001"),))

    assert quotes == {}


@pytest.mark.asyncio
async def test_quotes_fall_back_to_the_next_positive_price_field_and_safe_date():
    quote_service = AsyncMock(spec=UnifiedStockService)
    quote_service.get_stock_quote.return_value = {
        "price": "invalid",
        "current_price": "0",
        "close": Decimal("15.60"),
        "updated_at": "not-a-date",
        "trade_date": date(2026, 9, 5),
    }

    quotes = await load_latest_quotes(quote_service, (SecurityId("A", "000001"),))

    quote = quotes[SecurityId("A", "000001")]
    assert quote.price == Decimal("15.60")
    assert quote.observed_at == datetime(2026, 9, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_failed_quote_keeps_broker_snapshot_valuation_unchanged():
    parsed = parse_portfolio_file(snapshot_bytes(), as_of=date(2026, 9, 6))
    portfolio = reconcile_imports(
        user_id="user-1",
        account_alias="main",
        imports=(ImportedFacts("snapshot", 1, parsed),),
    )
    view = build_portfolio_view(portfolio, date(2026, 9, 6))
    original = view.holdings[0]
    quote_service = AsyncMock(spec=UnifiedStockService)
    quote_service.get_stock_quote.side_effect = RuntimeError("provider unavailable")

    quotes = await load_latest_quotes(quote_service, (original.security,))
    enriched = attach_latest_quotes(view, quotes)

    assert enriched.holdings[0].latest_quote_price is None
    assert enriched.holdings[0].market_price == original.market_price
    assert enriched.holdings[0].market_value == original.market_value
    assert enriched.holdings[0].total_profit_loss == original.total_profit_loss


def test_quote_attachment_changes_only_current_quote_fields():
    parsed = parse_portfolio_file(snapshot_bytes(), as_of=date(2026, 9, 6))
    portfolio = reconcile_imports(
        user_id="user-1",
        account_alias="main",
        imports=(ImportedFacts("snapshot", 1, parsed),),
    )
    view = build_portfolio_view(portfolio, date(2026, 9, 6))
    original = view.holdings[0]
    observed_at = datetime(2026, 9, 7, 7, 30, tzinfo=UTC)

    enriched = attach_latest_quotes(
        view,
        {original.security: LatestQuote(Decimal("12.45"), "CNY", observed_at)},
    )

    assert enriched.holdings[0].latest_quote_price == Decimal("12.45")
    assert enriched.holdings[0].latest_quote_currency == "CNY"
    assert enriched.holdings[0].quote_as_of == observed_at
    assert enriched.holdings[0].market_price == original.market_price
    assert enriched.holdings[0].market_value == original.market_value
    assert enriched.holdings[0].total_profit_loss == original.total_profit_loss
