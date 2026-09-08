from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import (
    Holding,
    Page,
    PortfolioView,
    SecurityId,
    TradeFilters,
    TradeItem,
)
from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import Entry, EntryPatch, NewEntry, Reference
from app.services.stock_research.references import (
    AnalysisReportAdapter,
    PaperTradeAdapter,
    ReferenceService,
)
from app.services.stock_research.service import StockResearchService
from app.services.stock_research.storage import StockResearchRepository
from tests.unit.stock_research.fakes import FakeDatabase


NOW = datetime(2026, 9, 8, 9, 30, tzinfo=UTC)


def decision(user_id: str, decision_id: str) -> Entry:
    return Entry(
        id=decision_id,
        user_id=user_id,
        entry_type="decision",
        scope="stock",
        security_id="A:600519",
        security_ids=("A:600519",),
        decision_action="buy",
        decision_date=date(2026, 9, 8),
        created_at=NOW,
        updated_at=NOW,
    )


def real_trade(trade_id: str, trade_date: date) -> TradeItem:
    return TradeItem(
        id=trade_id,
        trade_date=trade_date,
        settlement_date=trade_date,
        security=SecurityId("A", "600519"),
        name="Guizhou Moutai",
        event_type="trade",
        operation_label="buy",
        security_quantity=Decimal("100"),
        trade_currency="CNY",
        cash_movements=(),
        completeness="complete",
        warnings=(),
    )


class FakeRealPortfolioService:
    def __init__(self) -> None:
        self.trades = (
            real_trade("real-u1", date(2026, 9, 6)),
            real_trade("real-outside", date(2026, 7, 31)),
        )

    async def list_trades(
        self,
        *,
        user_id: str,
        filters: TradeFilters,
        page: int,
        page_size: int,
    ) -> Page[TradeItem]:
        items = self.trades if user_id == "u1" else ()
        items = tuple(
            item
            for item in items
            if item.security == filters.security_id
            and (filters.date_from is None or item.trade_date >= filters.date_from)
            and (
                filters.date_through is None
                or item.trade_date <= filters.date_through
            )
        )
        start = (page - 1) * page_size
        return Page(items[start : start + page_size], page, page_size, len(items))

    async def get_positions(
        self, *, user_id: str, as_of: date | None
    ) -> PortfolioView:
        requested = as_of or date(2026, 9, 8)
        holdings = (
            Holding(
                security=SecurityId("A", "600519"),
                broker_name="GTJA",
                route="main",
                quantity=Decimal("100"),
                available_quantity=Decimal("100"),
                frozen_quantity=Decimal("0"),
                reference_cost=Decimal("1400"),
                reference_cost_currency="CNY",
                market_price=Decimal("1500"),
                market_price_currency="CNY",
                market_value=Decimal("150000"),
                market_value_currency="CNY",
                total_profit_loss=Decimal("10000"),
                profit_loss_currency="CNY",
                profit_loss_percent=Decimal("7.14"),
                daily_profit_loss=None,
                daily_profit_loss_percent=None,
                position_weight_percent=None,
                same_day_buy=None,
                same_day_sell=None,
                profit_loss_price=None,
            ),
        ) if user_id == "u1" else ()
        return PortfolioView(
            as_of=requested,
            anchor_date=requested,
            direction="exact",
            holdings=holdings,
            reported_coverage=((requested, requested),),
            completeness="authoritative",
            warnings=(),
        )


class MissingRealPortfolioService(FakeRealPortfolioService):
    async def get_positions(
        self, *, user_id: str, as_of: date | None
    ) -> PortfolioView:
        raise PortfolioError("NO_FULL_SNAPSHOT", "no full portfolio snapshot")


@pytest.fixture
async def reference_service() -> ReferenceService:
    db = FakeDatabase()
    await db["analysis_reports"].insert_one(
        {
            "_id": "report-u1",
            "user_id": "u1",
            "analysis_id": "analysis-u1",
            "stock_symbol": "600519",
            "stock_name": "Guizhou Moutai",
            "analysis_date": "2026-09-01",
            "created_at": NOW,
        }
    )
    await db["analysis_reports"].insert_one(
        {
            "_id": "report-us",
            "user_id": "u1",
            "analysis_id": "analysis-us",
            "stock_symbol": "600519",
            "stock_name": "different market",
            "market_type": "US",
            "analysis_date": "2026-09-02",
            "created_at": NOW,
        }
    )
    await db["analysis_reports"].insert_one(
        {
            "_id": "report-u2",
            "user_id": "u2",
            "analysis_id": "analysis-u2",
            "stock_symbol": "600519",
            "stock_name": "private report",
            "analysis_date": "2026-09-02",
            "created_at": NOW,
        }
    )
    await db["paper_trades"].insert_one(
        {
            "_id": "paper-u1",
            "user_id": "u1",
            "code": "600519",
            "market": "CN",
            "side": "buy",
            "quantity": 100,
            "price": 1500.0,
            "timestamp": "2026-09-07T10:00:00+00:00",
        }
    )
    await db["paper_trades"].insert_one(
        {
            "_id": "paper-u2",
            "user_id": "u2",
            "code": "600519",
            "market": "CN",
            "side": "sell",
            "quantity": 100,
            "price": 1510.0,
            "timestamp": "2026-09-07T11:00:00+00:00",
        }
    )
    return ReferenceService(
        StockResearchRepository(db),
        AnalysisReportAdapter(db),
        FakeRealPortfolioService(),
        PaperTradeAdapter(db),
    )


@pytest.mark.asyncio
async def test_candidates_are_owned_and_keep_real_and_paper_separate(
    reference_service: ReferenceService,
) -> None:
    items = await reference_service.list_candidates(
        "u1", "A:600519", date(2026, 8, 1), date(2026, 9, 8)
    )

    assert {item.user_id for item in items} == {"u1"}
    assert {(item.kind, item.account_type) for item in items} >= {
        ("real_trade", "real"),
        ("paper_trade", "paper"),
    }
    assert {item.source_id for item in items}.isdisjoint(
        {"analysis-u2", "analysis-us", "paper-u2", "real-outside"}
    )
    assert any(
        item.kind == "holding_date" and item.source_date == date(2026, 9, 8)
        for item in items
    )


@pytest.mark.asyncio
async def test_missing_source_keeps_snapshot_warning(
    reference_service: ReferenceService,
) -> None:
    resolved = await reference_service.resolve(
        "u1",
        Reference(
            kind="analysis_report",
            source_id="gone",
            source_date=date(2026, 8, 1),
            label="old report",
        ),
    )

    assert resolved.available is False
    assert resolved.label == "old report"
    assert resolved.source_date == date(2026, 8, 1)


@pytest.mark.asyncio
async def test_resolve_does_not_collapse_real_and_paper_account_types(
    reference_service: ReferenceService,
) -> None:
    resolved = await reference_service.resolve(
        "u1",
        Reference(
            kind="paper_trade",
            source_id="paper-u1",
            account_type="real",
            source_date=date(2026, 9, 7),
            label="saved real snapshot",
        ),
    )

    assert resolved.available is False
    assert resolved.account_type == "real"
    assert resolved.label == "saved real snapshot"


@pytest.mark.asyncio
async def test_missing_real_snapshot_does_not_hide_other_reference_sources(
    reference_service: ReferenceService,
) -> None:
    reference_service.real_portfolio = MissingRealPortfolioService()

    items = await reference_service.list_candidates(
        "u1", "A:600519", date(2026, 8, 1), date(2026, 9, 8)
    )

    assert {item.source_id for item in items} >= {
        "analysis-u1",
        "paper-u1",
        "real-u1",
    }
    assert all(item.kind != "holding_date" for item in items)


@pytest.mark.asyncio
async def test_trade_recommendations_are_bounded_and_nearest_first(
    reference_service: ReferenceService,
) -> None:
    await reference_service.repository.insert_entry(decision("u1", "d1"))

    items = await reference_service.recommend_trade_links("u1", "d1")

    assert [item.source_id for item in items] == ["paper-u1", "real-u1"]
    assert all(item.kind in {"real_trade", "paper_trade"} for item in items)


@pytest.mark.asyncio
async def test_candidates_include_an_interval_decision_beyond_the_first_page(
    reference_service: ReferenceService,
) -> None:
    for number in range(201):
        await reference_service.repository.insert_entry(
            replace(
                decision("u1", f"d{number:03d}"),
                decision_date=(
                    date(2026, 9, 1) if number == 0 else date(2026, 1, 1)
                ),
            )
        )

    items = await reference_service.list_candidates(
        "u1", "A:600519", date(2026, 9, 1), date(2026, 9, 1)
    )

    assert "d000" in {
        item.source_id for item in items if item.kind == "decision"
    }


@pytest.mark.asyncio
async def test_one_trade_links_to_at_most_one_decision_and_unlink_releases_it() -> None:
    db = FakeDatabase()
    repository = StockResearchRepository(db)
    await repository.ensure_indexes()
    await repository.insert_entry(decision("u1", "d1"))
    await repository.insert_entry(decision("u1", "d2"))
    await repository.insert_entry(decision("u2", "d3"))
    service = StockResearchService(repository, clock=lambda: NOW)

    linked = await service.set_decision_trade_links(
        "u1",
        "d1",
        [Reference.real_trade("t1"), Reference.paper_trade("t1")],
    )
    await service.set_decision_trade_links("u2", "d3", [Reference.real_trade("t1")])

    assert [(item.kind, item.account_type) for item in linked.references] == [
        ("real_trade", "real"),
        ("paper_trade", "paper"),
    ]
    with pytest.raises(ResearchError, match="already linked"):
        await service.set_decision_trade_links(
            "u1", "d2", [Reference.real_trade("t1")]
        )

    await service.set_decision_trade_links("u1", "d1", [])
    assert await service.get_decision_trade_links("u1", "d1") == []
    assert (
        await service.set_decision_trade_links(
            "u1", "d2", [Reference.real_trade("t1")]
        )
    ).id == "d2"


@pytest.mark.asyncio
async def test_dollar_prefixed_trade_link_values_are_stored_and_unlinked_literally(
) -> None:
    repository = StockResearchRepository(FakeDatabase())
    await repository.ensure_indexes()
    await repository.insert_entry(decision("u1", "d1"))
    service = StockResearchService(repository, clock=lambda: NOW)
    reference = Reference.real_trade("$id", label="$100")

    linked = await service.set_decision_trade_links("u1", "d1", [reference])

    assert linked.references == (reference,)
    assert linked.trade_link_keys == (reference.trade_link_key(),)

    unlinked = await service.delete_decision_trade_link(
        "u1", "d1", reference
    )

    assert unlinked.references == ()
    assert unlinked.trade_link_keys == ()


@pytest.mark.asyncio
async def test_generic_decision_creation_reserves_trade_reference_keys() -> None:
    repository = StockResearchRepository(FakeDatabase())
    await repository.ensure_indexes()
    identifiers = iter(("d1", "d2"))
    service = StockResearchService(
        repository, clock=lambda: NOW, id_factory=lambda: next(identifiers)
    )
    request = replace(
        NewEntry.decision("A:600519", "buy", date(2026, 9, 8)),
        references=(Reference.real_trade("t1"),),
    )

    first = await service.create_entry("u1", request)

    assert first.trade_link_keys
    with pytest.raises(ResearchError, match="already linked"):
        await service.create_entry("u1", request)


@pytest.mark.asyncio
async def test_generic_reference_edit_rejects_duplicates_and_releases_stale_keys(
) -> None:
    repository = StockResearchRepository(FakeDatabase())
    await repository.ensure_indexes()
    identifiers = iter(("d1", "d2"))
    service = StockResearchService(
        repository, clock=lambda: NOW, id_factory=lambda: next(identifiers)
    )
    first = await service.create_entry(
        "u1",
        replace(
            NewEntry.decision("A:600519", "buy", date(2026, 9, 8)),
            references=(Reference.real_trade("t1"),),
        ),
    )
    second = await service.create_entry(
        "u1",
        replace(
            NewEntry.decision("A:600519", "buy", date(2026, 9, 9)),
            references=(Reference.real_trade("t2"),),
        ),
    )

    with pytest.raises(ResearchError, match="already linked"):
        await service.update_entry_draft(
            "u1", second.id, EntryPatch(references=(Reference.real_trade("t1"),))
        )

    edited = await service.update_entry_draft(
        "u1", first.id, EntryPatch(references=())
    )
    reclaimed = await service.update_entry_draft(
        "u1", second.id, EntryPatch(references=(Reference.real_trade("t1"),))
    )

    assert edited.trade_link_keys == ()
    assert [reference.source_id for reference in reclaimed.references] == ["t1"]


@pytest.mark.asyncio
async def test_link_replacement_preserves_a_concurrent_unrelated_entry_edit() -> None:
    class InterleavingRepository(StockResearchRepository):
        async def _concurrent_edit(self, user_id: str, decision_id: str) -> None:
            document = await self._collection("entries").find_one(
                {"user_id": user_id, "id": decision_id}
            )
            assert document is not None
            await self._collection("entries").update_one(
                {"user_id": user_id, "id": decision_id},
                {
                    "$set": {
                        "body": "concurrent body",
                        "references": [
                            *document["references"],
                            Reference.analysis_report(
                                "concurrent-report"
                            ).to_document(),
                        ],
                    }
                },
            )

        async def replace_entry(self, entry: Entry) -> Entry:
            await self._concurrent_edit(entry.user_id, entry.id)
            return await super().replace_entry(entry)

        async def replace_decision_trade_links(
            self,
            user_id: str,
            decision_id: str,
            references: tuple[Reference, ...],
            now: datetime,
        ) -> Entry:
            await self._concurrent_edit(user_id, decision_id)
            return await super().replace_decision_trade_links(
                user_id, decision_id, references, now
            )

    repository = InterleavingRepository(FakeDatabase())
    await repository.ensure_indexes()
    await repository.insert_entry(decision("u1", "d1"))
    service = StockResearchService(repository, clock=lambda: NOW)

    linked = await service.set_decision_trade_links(
        "u1", "d1", [Reference.real_trade("t1")]
    )

    assert linked.body == "concurrent body"
    assert [(item.kind, item.source_id) for item in linked.references] == [
        ("analysis_report", "concurrent-report"),
        ("real_trade", "t1"),
    ]
    assert (await service.get_entry("u1", "d1")).body == "concurrent body"


@pytest.mark.asyncio
async def test_link_replacement_does_not_resurrect_concurrently_deleted_entry(
) -> None:
    class ConcurrentDeleteRepository(StockResearchRepository):
        async def _concurrent_delete(self, user_id: str, decision_id: str) -> None:
            await self._collection("entries").update_one(
                {"user_id": user_id, "id": decision_id},
                {"$set": {"deleted_at": NOW.isoformat()}},
            )

        async def replace_entry(self, entry: Entry) -> Entry:
            await self._concurrent_delete(entry.user_id, entry.id)
            return await super().replace_entry(entry)

        async def replace_decision_trade_links(
            self,
            user_id: str,
            decision_id: str,
            references: tuple[Reference, ...],
            now: datetime,
        ) -> Entry:
            await self._concurrent_delete(user_id, decision_id)
            return await super().replace_decision_trade_links(
                user_id, decision_id, references, now
            )

    repository = ConcurrentDeleteRepository(FakeDatabase())
    await repository.ensure_indexes()
    await repository.insert_entry(decision("u1", "d1"))
    service = StockResearchService(repository, clock=lambda: NOW)

    with pytest.raises(ResearchError, match="not found"):
        await service.set_decision_trade_links(
            "u1", "d1", [Reference.real_trade("t1")]
        )

    deleted = await service.get_entry("u1", "d1", include_deleted=True)
    assert deleted.deleted_at == NOW


@pytest.mark.asyncio
async def test_targeted_unlink_does_not_resurrect_a_concurrently_removed_link() -> None:
    class InterleavingUnlinkRepository(StockResearchRepository):
        async def remove_decision_trade_link(
            self,
            user_id: str,
            decision_id: str,
            reference: Reference,
            now: datetime,
        ) -> Entry:
            document = await self._collection("entries").find_one(
                {"user_id": user_id, "id": decision_id}
            )
            assert document is not None
            other_key = Reference.paper_trade("t2").trade_link_key()
            await self._collection("entries").update_one(
                {"user_id": user_id, "id": decision_id},
                {
                    "$set": {
                        "references": [
                            item
                            for item in document["references"]
                            if item["source_id"] != "t2"
                        ],
                        "trade_link_keys": [
                            key
                            for key in document["trade_link_keys"]
                            if key != other_key
                        ],
                        "body": "concurrent body",
                    }
                },
            )
            return await super().remove_decision_trade_link(
                user_id, decision_id, reference, now
            )

    repository = InterleavingUnlinkRepository(FakeDatabase())
    await repository.ensure_indexes()
    await repository.insert_entry(
        replace(
            decision("u1", "d1"),
            references=(
                Reference.real_trade("t1"),
                Reference.paper_trade("t2"),
            ),
        )
    )
    service = StockResearchService(repository, clock=lambda: NOW)

    unlinked = await service.delete_decision_trade_link(
        "u1", "d1", Reference.real_trade("t1")
    )

    assert unlinked.body == "concurrent body"
    assert await service.get_decision_trade_links("u1", "d1") == []
