from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.stock_research.directory import ResearchDirectorySourceAdapter
from tests.unit.stock_research.fakes import FakeDatabase


NOW = datetime(2026, 9, 8, 9, 30, tzinfo=UTC)
OLDER = datetime(2026, 9, 7, 9, 30, tzinfo=UTC)


class RealPortfolioStub:
    async def get_positions(self, *, user_id: str, as_of):
        assert user_id == "u1"
        assert as_of is None
        return SimpleNamespace(
            holdings=(
                SimpleNamespace(
                    security=SimpleNamespace(market="A", code="600519"), quantity=100
                ),
            )
        )


class EmptyRealPortfolioStub:
    async def get_positions(self, *, user_id: str, as_of):
        raise PortfolioError("NO_FULL_SNAPSHOT", "no full portfolio snapshot")


@pytest.mark.asyncio
async def test_directory_adapter_reads_owned_latest_entries_and_separate_sources():
    db = FakeDatabase()
    entries = db["stock_research_entries"]
    await entries.insert_one(
        {
            "id": "older",
            "user_id": "u1",
            "entry_type": "note",
            "security_ids": ["A:600519"],
            "deleted_at": None,
            "updated_at": OLDER.isoformat(),
        }
    )
    await entries.insert_one(
        {
            "id": "latest",
            "user_id": "u1",
            "entry_type": "review",
            "security_ids": ["A:600519"],
            "deleted_at": None,
            "updated_at": NOW.isoformat(),
        }
    )
    await entries.insert_one(
        {
            "id": "other-user",
            "user_id": "u2",
            "entry_type": "decision",
            "security_ids": ["A:600519"],
            "deleted_at": None,
            "updated_at": datetime(2026, 9, 9, tzinfo=UTC).isoformat(),
        }
    )
    await db["paper_positions"].insert_one(
        {"user_id": "u1", "market": "HK", "code": "00700", "quantity": 10}
    )
    await db["paper_positions"].insert_one(
        {"user_id": "u2", "market": "CN", "code": "600519", "quantity": 10}
    )
    await db["user_favorites"].insert_one(
        {
            "user_id": "u1",
            "favorites": [{"market": "A股", "stock_code": "600519"}],
        }
    )

    adapter = ResearchDirectorySourceAdapter(db, RealPortfolioStub())
    facts = await adapter.get_workspace_facts(
        "u1", ("A:600519", "HK:00700", "US:AAPL")
    )

    assert facts["A:600519"].latest_entry_type == "review"
    assert facts["A:600519"].latest_entry_at == NOW
    assert facts["A:600519"].has_real_holding is True
    assert facts["A:600519"].has_paper_holding is False
    assert facts["A:600519"].watchlisted is True
    assert facts["HK:00700"].has_real_holding is False
    assert facts["HK:00700"].has_paper_holding is True
    assert facts["HK:00700"].watchlisted is False
    assert facts["US:AAPL"].has_real_holding is False
    assert facts["US:AAPL"].has_paper_holding is False


@pytest.mark.asyncio
async def test_directory_adapter_treats_missing_real_snapshot_as_no_holdings():
    adapter = ResearchDirectorySourceAdapter(FakeDatabase(), EmptyRealPortfolioStub())

    facts = await adapter.get_workspace_facts("u1", ("A:600519",))

    assert facts["A:600519"].has_real_holding is False
