"""Exercise publication against real standalone MongoDB, never user databases."""

import os
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.formats import parse_portfolio_file
from app.services.real_portfolio.models import TradeFilters
from app.services.real_portfolio.service import RealPortfolioService
from app.services.real_portfolio.storage import (
    GenerationManifest,
    RealPortfolioRepository,
    ensure_real_portfolio_indexes,
)
from tests.unit.real_portfolio.fixtures import (
    delivery_bytes,
    delivery_row,
    hk_pair,
    snapshot_bytes,
)


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
            "paper_accounts",
            "paper_positions",
            "paper_orders",
            "paper_trades",
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
            ("source_revisions", ("import_id", "source_revision"), True),
            (
                "source_row_revisions",
                ("import_id", "source_revision", "line_number"),
                True,
            ),
            ("source_row_revisions", ("user_id", "account_alias", "fact_key"), False),
            (
                "events",
                (
                    "user_id",
                    "account_alias",
                    "derived_generation",
                    "operation_date",
                    "event_id",
                ),
                False,
            ),
            (
                "snapshots",
                (
                    "user_id",
                    "account_alias",
                    "derived_generation",
                    "observed_on",
                    "import_id",
                ),
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
                    "user_id",
                    "account_alias",
                    "derived_generation",
                    "event_id",
                    "effective_date",
                ),
                False,
            ),
            (
                "event_evidence",
                (
                    "user_id",
                    "account_alias",
                    "derived_generation",
                    "event_id",
                    "import_id",
                    "line_number",
                    "evidence_role",
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
            user_id="user-1",
            filename="position.xls",
            content=snapshot_bytes(),
            as_of=date(2026, 9, 6),
        )
        first = await service.import_file(
            user_id="user-1",
            filename="delivery.xls",
            content=delivery_bytes(),
            as_of=None,
        )
        active_before = (await db.real_portfolio_accounts.find_one(scope))[
            "active_derived_generation"
        ]
        duplicate = await service.import_file(
            user_id="user-1",
            filename="delivery.xls",
            content=delivery_bytes(),
            as_of=None,
        )
        assert snapshot.status == first.status == "imported"
        assert duplicate.status == "duplicate"
        assert await db.real_portfolio_imports.count_documents(scope) == 2
        assert await db.real_portfolio_source_rows.count_documents(scope) == 2
        assert await db.real_portfolio_source_revisions.count_documents(scope) == 2
        assert await db.real_portfolio_source_row_revisions.count_documents(scope) == 2
        assert (await db.real_portfolio_accounts.find_one(scope))[
            "active_derived_generation"
        ] == active_before
        history = await service.list_imports(user_id="user-1", page=1, page_size=50)
        assert history.total == 2
        assert {item.source_filename for item in history.items} == {
            "position.xls",
            "delivery.xls",
        }
        assert all(item.status == "imported" for item in history.items)
        positions = await service.get_positions(user_id="user-1", as_of=None)
        assert positions.as_of == positions.anchor_date == date(2026, 9, 6)
        assert positions.completeness == "authoritative"
        assert len(positions.holdings) == 1
        assert positions.holdings[0].quantity == Decimal("100")
        trades = await service.list_trades(
            user_id="user-1",
            filters=TradeFilters(),
            page=1,
            page_size=50,
        )
        assert trades.total == len(trades.items) == 1
        assert trades.items[0].trade_date == date(2026, 9, 1)
        assert trades.items[0].security_quantity == Decimal("100")
        assert trades.items[0].cash_movements[0].amount == Decimal("-1001")
        assert len(list(tmp_path.rglob("*.xls"))) == 2

        # The database must reject the same file even under another import status.
        imported = await db.real_portfolio_imports.find_one(scope)
        conflicting = {key: value for key, value in imported.items() if key != "_id"}
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
            snapshot_count=1,
            position_count=1,
            event_count=1,
            posting_count=1,
            evidence_count=1,
            warning_count=0,
        )
        account = await db.real_portfolio_accounts.find_one(scope)
        await db.real_portfolio_accounts.update_one(
            scope,
            {
                "$set": {
                    "pending_generation_manifest": asdict(bad_manifest),
                    "pending_reported_coverage": account["active_reported_coverage"],
                    "pending_source_revisions": account["active_source_revisions"],
                    "pending_warning_revisions": account["active_warning_revisions"],
                }
            },
        )
        assert (
            await db.real_portfolio_snapshots.count_documents(
                {**scope, "derived_generation": incomplete_generation}
            )
            == 0
        )
        with pytest.raises(PortfolioError) as caught:
            await repository.activate_generation(
                user_id="user-1",
                account_alias="main",
                manifest=bad_manifest,
            )
        assert caught.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
        assert (await db.real_portfolio_accounts.find_one(scope))[
            "active_derived_generation"
        ] == active_before
        assert (
            await service.get_positions(user_id="user-1", as_of=date(2026, 9, 6))
            == positions
        )
        assert (
            await service.list_trades(
                user_id="user-1",
                filters=TradeFilters(),
                page=1,
                page_size=50,
            )
            == trades
        )
        assert {
            name
            for name in await db.list_collection_names()
            if name.startswith("paper_")
        } == set(paper_before)
        for name, documents in paper_before.items():
            assert await db[name].find({}).to_list(length=None) == documents
    finally:
        try:
            await client.drop_database(database_name)
        finally:
            client.close()


@pytest.mark.integration
@pytest.mark.parametrize("published_source", ["snapshot", "delivery"])
async def test_revision_recovery_and_operation_dates_on_standalone_mongo(
    tmp_path, monkeypatch, published_source
):
    uri = os.getenv("REAL_PORTFOLIO_TEST_MONGO_URI")
    if not uri:
        pytest.skip("REAL_PORTFOLIO_TEST_MONGO_URI is not configured")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database_name = f"real_portfolio_test_{uuid4().hex}"
    db = client[database_name]
    scope = {"user_id": "user-1", "account_alias": "main"}
    try:
        assert not (await client.admin.command("ismaster")).get("setName")
        await ensure_real_portfolio_indexes(db)
        repo = RealPortfolioRepository(db)
        service = RealPortfolioService(repo, tmp_path)
        content = (
            snapshot_bytes()
            if published_source == "snapshot"
            else delivery_bytes(delivery_row(成交编号="original-cn"))
        )
        as_of = date(2026, 9, 1) if published_source == "snapshot" else None
        with monkeypatch.context() as patch:
            patch.setattr(
                repo,
                "mark_imported",
                AsyncMock(side_effect=OSError("lost acknowledgement")),
            )
            with pytest.raises(PortfolioError):
                await service.import_file(
                    user_id="user-1",
                    filename="source.xls",
                    content=content,
                    as_of=as_of,
                )
        old = await repo.load_active_portfolio(**scope)
        original = deepcopy(
            await db.real_portfolio_source_row_revisions.find_one(scope)
        )
        imported = await db.real_portfolio_imports.find_one(scope)
        parsed = parse_portfolio_file(content, as_of=as_of)
        revised = replace(
            parsed,
            snapshot_positions=tuple(
                replace(p, quantity=Decimal("999")) for p in parsed.snapshot_positions
            ),
            delivery_observations=tuple(
                replace(p, quantity=Decimal("999"))
                for p in parsed.delivery_observations
            ),
        )
        await repo.replace_import_facts(**scope, import_doc=imported, parsed=revised)
        await repo.mark_failed(import_id=imported["import_id"], error_class="OSError")
        # A complete but unpublished reparse must not replace published facts.
        await service.import_file(
            user_id="user-1",
            filename="later.xls",
            content=delivery_bytes(delivery_row(成交编号="later-cn"), hk_pair()[1]),
            as_of=None,
        )
        current = await repo.load_active_portfolio(**scope)
        assert set(old.import_ids) < set(current.import_ids)
        assert all(event in current.events for event in old.events)
        assert current.snapshots == old.snapshots
        assert (
            await db.real_portfolio_source_row_revisions.find_one(
                {"_id": original["_id"]}
            )
            == original
        )
        account = await db.real_portfolio_accounts.find_one(scope)
        assert (
            account["active_source_revisions"][imported["import_id"]]
            == original["source_revision"]
        )
        assert (
            set(account["active_source_revisions"])
            == set(account["active_warning_revisions"])
            == set(current.import_ids)
        )
        selected = await service.list_trades(
            user_id="user-1",
            filters=TradeFilters(
                date_from=date(2026, 9, 3), date_through=date(2026, 9, 3)
            ),
            page=1,
            page_size=1,
        )
        assert selected.total == 1
        assert selected.items[0].trade_date is None
        assert selected.items[0].settlement_date == date(2026, 9, 3)
        if published_source == "snapshot":
            assert (await service.get_positions(user_id="user-1", as_of=None)).holdings[
                0
            ].quantity == Decimal("100")
        rows = [
            delivery_row(
                证券代码="204001",
                证券名称="GC001",
                操作="证券卖出",
                合同编号="repo",
                成交编号=transaction,
                发生金额="-1000",
            )
            for transaction in ("a", "b")
        ]
        await service.import_file(
            user_id="user-1",
            filename="repo.xls",
            content=delivery_bytes(*rows),
            as_of=None,
        )
        result = await service.list_trades(
            user_id="user-1",
            filters=TradeFilters(event_type="repo_open"),
            page=1,
            page_size=50,
        )
        assert result.total == 1
        portfolio = await repo.load_active_portfolio(**scope)
        event = next(
            event for event in portfolio.events if event.event_type == "repo_open"
        )
        assert len(event.evidence) == 2
        full = await service.list_trades(
            user_id="user-1", filters=TradeFilters(), page=1, page_size=50
        )
        pages = [
            await service.list_trades(
                user_id="user-1", filters=TradeFilters(), page=n, page_size=1
            )
            for n in range(1, full.total + 1)
        ]
        assert [page.items[0].id for page in pages] == [item.id for item in full.items]
        assert full.items[0].settlement_date == date(2026, 9, 3)
        with pytest.raises(DuplicateKeyError):
            await db.real_portfolio_source_row_revisions.insert_one(
                {key: value for key, value in original.items() if key != "_id"}
            )
    finally:
        try:
            await client.drop_database(database_name)
        finally:
            client.close()
