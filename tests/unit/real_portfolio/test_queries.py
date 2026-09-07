from collections import defaultdict
from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.formats import parse_portfolio_file
from app.services.real_portfolio.models import (
    CurrencyMovement,
    ImportedFacts,
    ImportSummary,
    Page,
    ReconciledPortfolio,
    SecurityId,
    TradeFilters,
    TradeItem,
)
from app.services.real_portfolio.reconciliation import reconcile_imports
from app.services.real_portfolio.service import RealPortfolioService
from app.services.real_portfolio.storage import (
    RealPortfolioRepository,
    ensure_real_portfolio_indexes,
)
from tests.unit.real_portfolio.fixtures import (
    delivery_bytes,
    delivery_row,
    hk_pair,
    snapshot_bytes,
)
from tests.unit.real_portfolio.test_storage import (
    MemoryCollection,
    facts,
    publish,
    rebuild,
    summary,
)


@pytest.fixture
async def database():
    db = defaultdict(MemoryCollection)
    await ensure_real_portfolio_indexes(db)
    return db


def portfolio_with_full_snapshots(*observed_dates: str) -> ReconciledPortfolio:
    imports = tuple(
        ImportedFacts(
            import_id=f"snapshot-{sequence}",
            import_sequence=sequence,
            parsed=parse_portfolio_file(
                snapshot_bytes(), as_of=date.fromisoformat(observed_on)
            ),
        )
        for sequence, observed_on in enumerate(observed_dates, start=1)
    )
    return reconcile_imports(user_id="user-1", account_alias="main", imports=imports)


@pytest.mark.asyncio
async def test_positions_default_to_latest_full_snapshot(tmp_path):
    repository = AsyncMock(spec=RealPortfolioRepository)
    repository.load_active_portfolio.return_value = portfolio_with_full_snapshots(
        "2026-08-31", "2026-09-06"
    )
    service = RealPortfolioService(repository, tmp_path, quote_service=None)

    view = await service.get_positions(user_id="user-1", as_of=None)

    assert view.as_of == date(2026, 9, 6)
    assert view.completeness == "authoritative"


@pytest.mark.asyncio
async def test_positions_use_explicit_historical_date(tmp_path):
    repository = AsyncMock(spec=RealPortfolioRepository)
    repository.load_active_portfolio.return_value = portfolio_with_full_snapshots(
        "2026-09-06"
    )
    service = RealPortfolioService(repository, tmp_path, quote_service=None)

    view = await service.get_positions(user_id="user-1", as_of=date(2026, 9, 1))

    assert view.as_of == date(2026, 9, 1)
    assert view.anchor_date == date(2026, 9, 6)
    assert view.direction == "reverse"


@pytest.mark.asyncio
@pytest.mark.parametrize("as_of", [None, date(2026, 9, 1)])
async def test_positions_without_a_full_snapshot_raise_a_safe_error(tmp_path, as_of):
    repository = AsyncMock(spec=RealPortfolioRepository)
    repository.load_active_portfolio.return_value = reconcile_imports(
        user_id="user-1", account_alias="main", imports=()
    )
    service = RealPortfolioService(repository, tmp_path, quote_service=None)

    with pytest.raises(PortfolioError) as caught:
        await service.get_positions(user_id="private-user", as_of=as_of)

    assert caught.value.code == "NO_FULL_SNAPSHOT"
    assert caught.value.message == "no full portfolio snapshot"
    assert "private-user" not in repr(caught.value.context)


@pytest.mark.asyncio
async def test_query_service_fixes_account_and_enforces_page_bounds(tmp_path):
    repository = AsyncMock(spec=RealPortfolioRepository)
    expected_filters = TradeFilters(market="HK")

    async def list_active_trades(**query):
        if query != {
            "user_id": "user-1",
            "account_alias": "main",
            "filters": expected_filters,
            "page": 2,
            "page_size": 25,
        }:
            raise AssertionError(f"unexpected trade query: {query}")
        return Page((), 2, 25, 0)

    async def list_imports(**query):
        if query != {
            "user_id": "user-1",
            "account_alias": "main",
            "page": 3,
            "page_size": 20,
        }:
            raise AssertionError(f"unexpected import query: {query}")
        return Page((), 3, 20, 0)

    repository.list_active_trades.side_effect = list_active_trades
    repository.list_imports.side_effect = list_imports
    service = RealPortfolioService(repository, tmp_path, quote_service=None)

    trades = await service.list_trades(
        user_id="user-1", filters=expected_filters, page=2, page_size=25
    )
    imports = await service.list_imports(user_id="user-1", page=3, page_size=20)

    assert trades == Page((), 2, 25, 0)
    assert imports == Page((), 3, 20, 0)
    for page, page_size in ((0, 50), (1, 0), (1, 201)):
        with pytest.raises(ValueError, match="pagination"):
            await service.list_imports(user_id="user-1", page=page, page_size=page_size)


@pytest.mark.asyncio
async def test_active_trade_query_filters_orders_and_sanitizes(database):
    repository = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(
        delivery_bytes(
            delivery_row(成交编号="older", 成交日期="2026-08-31"),
            delivery_row(成交编号="newer", 成交日期="2026-09-01"),
        )
    )
    import_doc = await facts(repository, parsed)
    await publish(repository, import_doc, "active-generation")
    current = next(
        document
        for document in database["real_portfolio_events"].documents
        if document["trade_date"] == "2026-09-01"
    )
    internal_event_id = current["event_id"]
    recognizable_event_id = "private-internal-event-id-for-newer-trade"
    current["event_id"] = recognizable_event_id
    for collection_name in ("real_portfolio_postings", "real_portfolio_event_evidence"):
        for document in database[collection_name].documents:
            if document["event_id"] == internal_event_id:
                document["event_id"] = recognizable_event_id
    inactive_doc = {
        **database["real_portfolio_events"].documents[0],
        "derived_generation": "inactive-generation",
        "event_id": "inactive-private-event-id",
        "trade_date": "2026-09-02",
    }
    database["real_portfolio_events"].documents.append(inactive_doc)

    page = await repository.list_active_trades(
        user_id="user-1",
        account_alias="main",
        filters=TradeFilters(
            date_from=date(2026, 8, 31),
            date_through=date(2026, 9, 1),
            market="A",
            security_id=SecurityId("A", "000001"),
            event_type="trade",
            completeness="complete",
        ),
        page=1,
        page_size=1,
    )

    assert page.total == 2
    assert page.items[0].trade_date == date(2026, 9, 1)
    assert page.items[0].id == (
        "80524da779d6ba48419629e90534296fc99d0788d3062c5c44c9fef67b5f0418"
    )
    assert "private-internal-event-id" not in repr(page)
    assert "inactive-private-event-id" not in repr(page)
    assert "older" not in repr(page)
    assert "newer" not in repr(page)


@pytest.mark.asyncio
async def test_position_read_pins_every_collection_and_parse_warning_revision(database):
    repository = RealPortfolioRepository(database)
    snapshot = parse_portfolio_file(snapshot_bytes(), as_of=date(2026, 9, 1))
    snapshot_doc = await facts(repository, snapshot)
    await publish(repository, snapshot_doc, "snapshot-source")
    await repository.mark_imported(
        import_id=snapshot_doc["import_id"],
        generation="snapshot-source",
        summary=summary(snapshot),
    )
    parsed = parse_portfolio_file(
        delivery_bytes(hk_pair()[0], delivery_row(成交数量="bad"))
    )
    delivery_doc = await facts(repository, parsed)
    active, _ = await publish(repository, delivery_doc, "active-generation")
    await repository.mark_imported(
        import_id=delivery_doc["import_id"],
        generation="active-generation",
        summary=summary(parsed),
    )
    replacement = replace(
        parsed,
        warnings=(replace(parsed.warnings[0], message="pending parse warning"),),
    )
    await facts(repository, replacement)
    pending = await rebuild(repository, delivery_doc)
    await repository.write_generation(
        user_id="user-1",
        account_alias="main",
        generation="pending-generation",
        portfolio=pending,
    )
    active_snapshot_id = next(
        snapshot["snapshot_id"]
        for snapshot in database["real_portfolio_snapshots"].documents
        if snapshot["derived_generation"] == "active-generation"
    )
    pending_position = next(
        position
        for position in database["real_portfolio_snapshot_positions"].documents
        if position["derived_generation"] == "pending-generation"
    )
    pending_position["snapshot_id"] = active_snapshot_id
    pending_warning = next(
        warning
        for warning in database["real_portfolio_warnings"].documents
        if warning.get("derived_generation") == "pending-generation"
    )
    pending_warning["message"] = "pending derived warning"

    loaded = await repository.load_active_portfolio(
        user_id="user-1", account_alias="main"
    )

    assert loaded == active
    assert "pending parse warning" not in repr(loaded)
    assert "pending derived warning" not in repr(loaded)


@pytest.mark.asyncio
async def test_trade_page_redacts_warning_source_references(database):
    repository = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(delivery_bytes(hk_pair()[0]))
    import_doc = await facts(repository, parsed)
    await publish(repository, import_doc, "warnings-generation")

    page = await repository.list_active_trades(
        user_id="user-1",
        account_alias="main",
        filters=TradeFilters(),
        page=1,
        page_size=50,
    )

    assert page.items[0].warnings
    assert page.items[0].warnings[0].import_id is None
    assert page.items[0].warnings[0].line_number is None
    assert import_doc["import_id"] not in repr(page)


@pytest.mark.asyncio
async def test_trade_page_never_exposes_source_identifiers(tmp_path):
    repository = AsyncMock(spec=RealPortfolioRepository)
    repository.list_active_trades.return_value = Page(
        items=(
            TradeItem(
                id="ui-key",
                trade_date=date(2026, 9, 1),
                settlement_date=None,
                security=SecurityId("A", "000001"),
                name="anonymous",
                event_type="trade",
                operation_label="security buy",
                security_quantity=Decimal(100),
                trade_currency="CNY",
                cash_movements=(CurrencyMovement("CNY", Decimal(-1001)),),
                completeness="complete",
                warnings=(),
            ),
        ),
        page=1,
        page_size=50,
        total=1,
    )
    service = RealPortfolioService(repository, tmp_path, quote_service=None)

    page = await service.list_trades(
        user_id="user-1", filters=TradeFilters(), page=1, page_size=50
    )

    serialized = repr(page)
    assert "transaction_fingerprint" not in serialized
    assert "contract_fingerprint" not in serialized
    assert "line_number" not in serialized


@pytest.mark.asyncio
async def test_import_history_uses_completed_time_for_stable_pagination(database):
    repository = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(delivery_bytes())
    first = await facts(repository, parsed)
    second = await facts(
        repository,
        parse_portfolio_file(delivery_bytes(delivery_row(成交编号="second"))),
    )
    summary = ImportSummary(
        "imported",
        parsed.source_type,
        parsed.format_id,
        parsed.file_sha256[:12],
        1,
        1,
        1,
        0,
        0,
        1,
        0,
        0,
        (),
    )
    await repository.mark_imported(
        import_id=first["import_id"], generation="g", summary=summary
    )
    await repository.mark_imported(
        import_id=second["import_id"], generation="g", summary=summary
    )
    await database["real_portfolio_imports"].update_one(
        {"import_id": first["import_id"]},
        {"$set": {"completed_at": "2026-09-07T10:00:00+00:00"}},
    )
    await database["real_portfolio_imports"].update_one(
        {"import_id": second["import_id"]},
        {"$set": {"completed_at": "2026-09-07T10:00:00+00:00"}},
    )

    first_page = await repository.list_imports(
        user_id="user-1", account_alias="main", page=1, page_size=1
    )
    second_page = await repository.list_imports(
        user_id="user-1", account_alias="main", page=2, page_size=1
    )

    assert first_page.total == 2
    assert first_page.items[0].completed_at.isoformat() == "2026-09-07T10:00:00+00:00"
    assert first_page.items[0].file_sha256_short == second["file_sha256"][:12]
    assert second_page.items[0].file_sha256_short == first["file_sha256"][:12]


@pytest.mark.asyncio
async def test_repository_rejects_unbounded_pages_before_querying(database):
    repository = RealPortfolioRepository(database)

    with pytest.raises(ValueError, match="pagination"):
        await repository.list_active_trades(
            user_id="user-1",
            account_alias="main",
            filters=TradeFilters(),
            page=1,
            page_size=201,
        )
    with pytest.raises(ValueError, match="pagination"):
        await repository.list_imports(
            user_id="user-1", account_alias="main", page=1, page_size=201
        )
