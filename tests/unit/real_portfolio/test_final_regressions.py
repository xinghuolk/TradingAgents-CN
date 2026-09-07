from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal, getcontext
from unittest.mock import AsyncMock

import pytest

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.formats import parse_portfolio_file
from app.services.real_portfolio.holdings import build_portfolio_view
from app.services.real_portfolio.models import (
    ImportedFacts,
    ReconciledPortfolio,
    TradeFilters,
)
from app.services.real_portfolio.reconciliation import reconcile_imports
from tests.unit.real_portfolio.fixtures import (
    delivery_bytes,
    delivery_row,
    hk_pair,
    snapshot_bytes,
    snapshot_row,
)
from tests.unit.real_portfolio.test_import_service import active, system, upload


@pytest.mark.parametrize(
    "revisions", [[("import", "revision")], (["import", "revision"],)]
)
def test_source_revision_metadata_rejects_mutable_containers(revisions):
    with pytest.raises(TypeError):
        ReconciledPortfolio((), (), (), (), (), source_revisions=revisions)


@pytest.mark.parametrize("source", ["snapshot", "delivery"])
async def test_published_facts_survive_lost_completion_then_another_import(
    system, monkeypatch, source
):
    service, repo, _ = system
    with monkeypatch.context() as patch:
        patch.setattr(repo, "mark_imported", AsyncMock(side_effect=OSError("lost ack")))
        with pytest.raises(PortfolioError):
            await service.import_file(
                user_id="user-1",
                filename="source.xls",
                content=snapshot_bytes() if source == "snapshot" else delivery_bytes(),
                as_of=date(2026, 9, 1) if source == "snapshot" else None,
            )
    published = await active(repo)
    await upload(service, delivery_bytes(delivery_row(成交编号="later")))
    rebuilt = await active(repo)
    assert set(published.import_ids) < set(rebuilt.import_ids)
    assert all(event in rebuilt.events for event in published.events)
    assert rebuilt.snapshots == published.snapshots
    if source == "snapshot":
        assert (await service.get_positions(user_id="user-1", as_of=None)).holdings[
            0
        ].quantity == Decimal("100")
    else:
        assert len(rebuilt.events) == 2


@pytest.mark.parametrize("finish_reparse", [False, True])
async def test_active_revision_survives_unpublished_reparse_then_another_import(
    system, monkeypatch, finish_reparse
):
    service, repo, db = system
    original = delivery_bytes(delivery_row(), delivery_row(成交编号="second"))
    await upload(service, original)
    before = await active(repo)
    document = deepcopy(db["real_portfolio_imports"].documents[0])
    parsed = parse_portfolio_file(original)
    changed = replace(
        parsed,
        delivery_observations=tuple(
            replace(obs, quantity=Decimal("999"))
            for obs in parsed.delivery_observations
        ),
    )
    with monkeypatch.context() as patch:
        if not finish_reparse:
            collection = db["real_portfolio_source_row_revisions"]
            old_collection = db["real_portfolio_source_rows"]

            async def fail_new(documents):
                await collection._insert_one(documents[0])
                raise OSError("partial revision")

            async def fail_old(*args, **kwargs):
                await old_collection._replace_one(*args, **kwargs)
                raise OSError("partial replacement")

            patch.setattr(collection, "insert_many", fail_new)
            patch.setattr(old_collection, "replace_one", fail_old)
            with pytest.raises(OSError):
                await repo.replace_import_facts(
                    user_id="user-1",
                    account_alias="main",
                    import_doc=document,
                    parsed=changed,
                )
        else:
            await repo.replace_import_facts(
                user_id="user-1",
                account_alias="main",
                import_doc=document,
                parsed=changed,
            )
    await repo.mark_failed(import_id=document["import_id"], error_class="OSError")
    await upload(service, delivery_bytes(delivery_row(成交编号="third")))
    rebuilt = await active(repo)
    assert len(rebuilt.events) == 3
    assert all(event in rebuilt.events for event in before.events)
    assert {
        p.amount
        for event in rebuilt.events
        for p in event.postings
        if p.role == "security"
    } == {Decimal("100")}


async def test_parser_upgrade_keeps_overlapping_facts_at_two_events(
    system, monkeypatch
):
    service, repo, _ = system
    first = delivery_bytes(delivery_row(成交编号="one"))
    second = delivery_bytes(delivery_row(成交编号="one"), delivery_row(成交编号="two"))
    await upload(service, first)
    await upload(service, second)
    monkeypatch.setattr(
        "app.services.real_portfolio.formats.PARSER_VERSION", "portfolio-v2"
    )
    monkeypatch.setattr(
        "app.services.real_portfolio.service.PARSER_VERSION", "portfolio-v2"
    )
    await upload(service, first)
    events = (await active(repo)).events
    assert len(events) == 2
    assert sum(
        p.amount for event in events for p in event.postings if p.role == "security"
    ) == Decimal("200")
    assert sorted(len(event.evidence) for event in events) == [1, 2]


@pytest.mark.parametrize("cash", ["-1000", "-1001"])
async def test_reverse_repo_phase_keeps_one_event_and_all_evidence(system, cash):
    service, repo, _ = system
    rows = [
        delivery_row(
            证券代码="204001",
            证券名称="GC001",
            操作="证券卖出",
            合同编号="repo",
            成交编号=transaction,
            发生金额=amount,
        )
        for transaction, amount in (("repo-a", "-1000"), ("repo-b", cash))
    ]
    result = await upload(service, delivery_bytes(*rows))
    events = (await active(repo)).events
    assert result.events == len(events) == 1
    assert events[0].event_type == "repo_open"
    assert len(events[0].evidence) == 2
    assert len(events[0].postings) == 1
    assert events[0].postings[0].amount in (Decimal("-1000"), Decimal(cash))
    assert bool(events[0].warnings) is (cash != "-1000")
    parsed = parse_portfolio_file(delivery_bytes(*rows))
    reverse = reconcile_imports(
        user_id="user-1",
        account_alias="main",
        imports=(
            ImportedFacts(
                "i",
                1,
                replace(
                    parsed,
                    delivery_observations=tuple(reversed(parsed.delivery_observations)),
                ),
            ),
        ),
    )
    forward = reconcile_imports(
        user_id="user-1", account_alias="main", imports=(ImportedFacts("i", 1, parsed),)
    )
    assert reverse == forward


async def test_large_decimal_query_and_forward_reverse_reconstruction_are_exact(system):
    service, repo, _ = system
    value = "123456789012345678901234567890.123456789"
    context = getcontext().copy()
    await upload(service, delivery_bytes(delivery_row(成交数量=value, 成交日期="2026-09-02")))
    page = await service.list_trades(
        user_id="user-1", filters=TradeFilters(), page=1, page_size=50
    )
    assert str(page.items[0].security_quantity) == value
    snapshot = parse_portfolio_file(
        snapshot_bytes(snapshot_row(股票余额="0")), as_of=date(2026, 9, 1)
    )
    trade = parse_portfolio_file(
        delivery_bytes(delivery_row(成交数量=value, 成交日期="2026-09-02"))
    )
    portfolio = reconcile_imports(
        user_id="user-1",
        account_alias="main",
        imports=(ImportedFacts("s", 1, snapshot), ImportedFacts("t", 2, trade)),
    )
    assert (
        str(build_portfolio_view(portfolio, date(2026, 9, 2)).holdings[0].quantity)
        == value
    )
    later = replace(
        portfolio,
        snapshots=(replace(portfolio.snapshots[0], observed_on=date(2026, 9, 2)),),
    )
    assert (
        str(build_portfolio_view(later, date(2026, 9, 1)).holdings[0].quantity)
        == "-" + value
    )
    assert getcontext().prec == context.prec
    assert getcontext().flags == context.flags


async def test_settlement_only_rows_filter_and_page_by_displayed_operation_date(system):
    service, _, _ = system
    await upload(
        service,
        delivery_bytes(
            hk_pair()[1],
            delivery_row(成交编号="older-cn"),
            delivery_row(成交编号="same-date", 成交日期="2026-09-03"),
        ),
    )
    filtered = await service.list_trades(
        user_id="user-1",
        filters=TradeFilters(date_from=date(2026, 9, 3), date_through=date(2026, 9, 3)),
        page=1,
        page_size=50,
    )
    assert filtered.total == 2
    assert any(
        item.trade_date is None and item.settlement_date == date(2026, 9, 3)
        for item in filtered.items
    )
    all_rows = await service.list_trades(
        user_id="user-1", filters=TradeFilters(), page=1, page_size=50
    )
    pages = [
        await service.list_trades(
            user_id="user-1", filters=TradeFilters(), page=n, page_size=1
        )
        for n in range(1, 4)
    ]
    assert [p.items[0].id for p in pages] == [item.id for item in all_rows.items]
    assert [item.trade_date or item.settlement_date for item in all_rows.items] == [
        date(2026, 9, 3),
        date(2026, 9, 3),
        date(2026, 9, 1),
    ]


@pytest.mark.parametrize("status", ["failed", "publishing"])
@pytest.mark.parametrize(
    "field", ["summary_parser_version", "summary_derived_version", "summary_generation"]
)
async def test_recovery_rebuilds_mismatched_staged_summary(system, status, field):
    service, repo, db = system
    await upload(service)
    before = await active(repo)
    generation = db["real_portfolio_accounts"].documents[0]["active_derived_generation"]
    document = db["real_portfolio_imports"].documents[0]
    await db["real_portfolio_imports"].update_one(
        {"import_id": document["import_id"]},
        {"$set": {"status": status, field: "obsolete"}},
    )
    result = await upload(service)
    assert result.status == "imported"
    assert await active(repo) == before
    assert (
        db["real_portfolio_accounts"].documents[0]["active_derived_generation"]
        != generation
    )


async def test_incomplete_source_revision_cannot_be_used_for_rebuild(system):
    service, repo, db = system
    await upload(service)
    before = await active(repo)
    original = deepcopy(db["real_portfolio_source_row_revisions"].documents[0])
    await db["real_portfolio_imports"].update_one(
        {"import_id": original["import_id"]},
        {"$set": {"source_revision": "incomplete", "facts_complete": True}},
    )
    await db["real_portfolio_source_row_revisions"].insert_one(
        dict(original, source_revision="incomplete")
    )
    with pytest.raises(PortfolioError):
        await repo.load_rebuild_imports(
            user_id="user-1",
            account_alias="main",
            current_import_id=original["import_id"],
        )
    assert await active(repo) == before
