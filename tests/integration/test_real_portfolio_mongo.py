"""Exercise publication against real standalone MongoDB, never user databases."""

import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import TradeFilters
from app.services.real_portfolio.service import RealPortfolioService
from app.services.real_portfolio.storage import (
    GenerationManifest,
    RealPortfolioRepository,
    ensure_real_portfolio_indexes,
)
from tests.unit.real_portfolio.fixtures import delivery_bytes, snapshot_bytes


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_portfolio_round_trip_on_standalone_mongo(tmp_path):
    """Catch broken BSON writes, idempotency, publication isolation and paper writes."""
    uri = os.getenv("REAL_PORTFOLIO_TEST_MONGO_URI")
    if not uri:
        pytest.skip("REAL_PORTFOLIO_TEST_MONGO_URI is not configured")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database_name = f"real_portfolio_test_{uuid4().hex}"
    db = client[database_name]
    scope = {"user_id": "user-1", "account_alias": "main"}
    try:
        topology = await client.admin.command("ismaster")
        assert not topology.get("setName"), "Use a standalone MongoDB for this proof"
        assert topology.get("msg") != "isdbgrid", "Use standalone, not mongos"
        paper_before = {}
        for name in (
            "paper_accounts", "paper_positions", "paper_orders", "paper_trades"
        ):
            await db[name].insert_one(
                {
                    "_id": "synthetic-paper-sentinel",
                    "user_id": "user-1",
                    "value": "123.45",
                }
            )
            paper_before[name] = await db[name].find({}).to_list(length=None)

        await ensure_real_portfolio_indexes(db)
        # Literal acceptance contracts, independent of the index builder.
        required_indexes = (
            ("accounts", ("user_id", "account_alias"), True),
            ("imports", ("user_id", "account_alias", "file_sha256"), True),
            ("imports", ("user_id", "account_alias", "completed_at"), False),
            ("source_rows", ("user_id", "account_alias", "fact_key"), False),
            ("source_rows", ("import_id", "line_number"), True),
            (
                "snapshots",
                ("user_id", "account_alias", "derived_generation", "observed_on", "import_id"),
                False,
            ),
            ("snapshot_positions", ("snapshot_id", "security_id"), True),
            (
                "events",
                ("user_id", "account_alias", "derived_generation", "event_id"),
                True,
            ),
            (
                "postings",
                (
                    "user_id", "account_alias", "derived_generation",
                    "event_id", "effective_date",
                ),
                False,
            ),
            (
                "event_evidence",
                (
                    "user_id", "account_alias", "derived_generation", "event_id",
                    "import_id", "line_number", "evidence_role",
                ),
                True,
            ),
        )
        for collection, fields, unique in required_indexes:
            indexes = await db[f"real_portfolio_{collection}"].index_information()
            expected_keys = [
                (field, -1 if field == "completed_at" else 1) for field in fields
            ]
            matching = [
                index for index in indexes.values() if index["key"] == expected_keys
            ]
            assert len(matching) == 1, (collection, fields)
            assert matching[0].get("unique", False) is unique
            assert "partialFilterExpression" not in matching[0]
            assert not matching[0].get("sparse", False)

        repository = RealPortfolioRepository(db)
        service = RealPortfolioService(repository, tmp_path, quote_service=None)
        snapshot = await service.import_file(
            user_id="user-1", filename="position.xls", content=snapshot_bytes(),
            as_of=date(2026, 9, 6),
        )
        first = await service.import_file(
            user_id="user-1", filename="delivery.xls",
            content=delivery_bytes(), as_of=None,
        )
        active_before = (await db.real_portfolio_accounts.find_one(scope))[
            "active_derived_generation"
        ]
        duplicate = await service.import_file(
            user_id="user-1", filename="delivery.xls",
            content=delivery_bytes(), as_of=None,
        )
        assert snapshot.status == first.status == "imported"
        assert duplicate.status == "duplicate"
        assert await db.real_portfolio_imports.count_documents(scope) == 2
        assert await db.real_portfolio_source_rows.count_documents(scope) == 2
        assert (await db.real_portfolio_accounts.find_one(scope))[
            "active_derived_generation"
        ] == active_before
        history = await service.list_imports(user_id="user-1", page=1, page_size=50)
        assert history.total == 2
        assert {item.source_filename for item in history.items} == {
            "position.xls", "delivery.xls"
        }
        assert all(item.status == "imported" for item in history.items)
        positions = await service.get_positions(user_id="user-1", as_of=None)
        assert positions.as_of == positions.anchor_date == date(2026, 9, 6)
        assert positions.completeness == "authoritative"
        assert len(positions.holdings) == 1
        assert positions.holdings[0].quantity == Decimal("100")
        trades = await service.list_trades(
            user_id="user-1", filters=TradeFilters(), page=1, page_size=50,
        )
        assert trades.total == len(trades.items) == 1
        assert trades.items[0].trade_date == date(2026, 9, 1)
        assert trades.items[0].security_quantity == Decimal("100")
        assert trades.items[0].cash_movements[0].amount == Decimal("-1001")
        assert len(list(tmp_path.rglob("*.xls"))) == 2

        # The database must reject the same file even under another import status.
        imported = await db.real_portfolio_imports.find_one(scope)
        conflicting = {
            key: value for key, value in imported.items() if key != "_id"
        }
        conflicting.update(import_id=uuid4().hex, status="publishing")
        with pytest.raises(DuplicateKeyError):
            await db.real_portfolio_imports.insert_one(conflicting)

        incomplete_generation = uuid4().hex
        await db.real_portfolio_events.insert_one(
            {
                **scope,
                "derived_generation": incomplete_generation,
                "event_id": "incomplete-event",
            }
        )
        bad_manifest = GenerationManifest(
            generation=incomplete_generation,
            import_ids=tuple(
                item["import_id"]
                for item in await db.real_portfolio_imports.find(scope).to_list(
                    length=None
                )
            ),
            snapshot_count=1, position_count=1, event_count=1,
            posting_count=1, evidence_count=1, warning_count=0,
        )
        with pytest.raises(PortfolioError) as caught:
            await repository.activate_generation(
                user_id="user-1", account_alias="main", manifest=bad_manifest,
            )
        assert caught.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
        assert (await db.real_portfolio_accounts.find_one(scope))[
            "active_derived_generation"
        ] == active_before
        assert await service.get_positions(
            user_id="user-1", as_of=date(2026, 9, 6)
        ) == positions
        assert await service.list_trades(
            user_id="user-1", filters=TradeFilters(), page=1, page_size=50,
        ) == trades
        assert {
            name for name in await db.list_collection_names()
            if name.startswith("paper_")
        } == set(paper_before)
        for name, documents in paper_before.items():
            assert await db[name].find({}).to_list(length=None) == documents
    finally:
        try:
            await client.drop_database(database_name)
        finally:
            client.close()
