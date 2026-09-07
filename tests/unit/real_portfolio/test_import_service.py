import asyncio
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.services.real_portfolio.archive import archive_portfolio_bytes
from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.service import (
    RealPortfolioService,
    account_import_lock,
)
from app.services.real_portfolio.storage import (
    DERIVED_VERSION,
    RealPortfolioRepository,
    ensure_real_portfolio_indexes,
)
from tests.unit.real_portfolio.fixtures import (
    delivery_bytes,
    delivery_row,
    hk_pair,
    snapshot_bytes,
)
from tests.unit.real_portfolio.test_storage import MemoryCollection


@pytest.fixture
async def system(tmp_path):
    database = defaultdict(MemoryCollection)
    await ensure_real_portfolio_indexes(database)
    repository = RealPortfolioRepository(database)
    return RealPortfolioService(repository, tmp_path), repository, database


async def upload(service, content=None, **kwargs):
    return await service.import_file(
        user_id="user-1",
        filename="delivery.xls",
        content=content or delivery_bytes(),
        as_of=None,
        **kwargs,
    )


async def active(repository):
    return await repository.load_active_portfolio(
        user_id="user-1", account_alias="main"
    )


async def test_preview_retains_warnings_and_never_archives_or_writes(system, tmp_path):
    service, _, database = system
    before = {key: deepcopy(value.documents) for key, value in database.items()}
    result = await service.preview_file(
        user_id="user-1",
        filename="preview.xls",
        content=delivery_bytes(delivery_row(), delivery_row(成交数量="bad")),
        as_of=None,
    )
    assert result.status == "preview"
    assert (result.source_rows, result.usable_rows, result.new_facts) == (2, 1, 1)
    assert result.events == 1 and result.warnings
    assert all(warning.import_id is None for warning in result.warnings)
    assert {key: value.documents for key, value in database.items()} == before
    assert list(tmp_path.rglob("*.xls")) == []


async def test_duplicate_uses_exact_persisted_summary_without_rebuilding(
    system, monkeypatch
):
    service, repo, database = system
    first = await upload(service)
    generation = database["real_portfolio_accounts"].documents[0][
        "active_derived_generation"
    ]
    monkeypatch.setattr(
        repo, "load_rebuild_imports", AsyncMock(side_effect=AssertionError)
    )
    second = await upload(service)
    assert second == replace(first, status="duplicate")
    assert second.new_facts == 1 and second.duplicate_facts == 0
    assert len(database["real_portfolio_imports"].documents) == 1
    assert len(database["real_portfolio_source_rows"].documents) == 1
    assert (
        database["real_portfolio_accounts"].documents[0]["active_derived_generation"]
        == generation
    )


async def test_snapshot_date_conflict_preserves_original_anchor(system):
    service, repo, database = system
    arguments = {
        "user_id": "user-1",
        "filename": "snapshot.xls",
        "content": snapshot_bytes(),
    }
    await service.import_file(**arguments, as_of=date(2026, 9, 6))
    before = await active(repo)
    with pytest.raises(PortfolioError) as caught:
        await service.import_file(**arguments, as_of=date(2026, 9, 7))
    assert caught.value.code == "SNAPSHOT_DATE_CONFLICT"
    assert caught.value.context == {"observed_on": "2026-09-06"}
    assert await active(repo) == before
    assert len(database["real_portfolio_imports"].documents) == 1
    assert database["real_portfolio_imports"].documents[0]["status"] == "imported"


async def test_two_awaited_same_account_imports_share_one_identity(system):
    service, _, database = system
    imports = database["real_portfolio_imports"]
    reserving, resume = asyncio.Event(), asyncio.Event()

    async def suspended_insert(document):
        reserving.set()
        await resume.wait()
        await imports._insert_one(document)

    imports.insert_one.side_effect = suspended_insert
    first_task = asyncio.create_task(upload(service))
    await asyncio.wait_for(reserving.wait(), timeout=1)
    second_task = asyncio.create_task(upload(service))
    await asyncio.sleep(0)
    resume.set()
    first, second = await asyncio.gather(first_task, second_task)
    assert sorted([first.status, second.status]) == ["duplicate", "imported"]
    assert len(database["real_portfolio_imports"].documents) == 1
    assert len(database["real_portfolio_source_rows"].documents) == 1
    assert database["real_portfolio_accounts"].documents[0]["import_sequence"] == 1


async def test_parse_errors_arrive_before_waiting_for_account_lock(system):
    service, _, database = system
    async with account_import_lock("user-1", "main"):
        with pytest.raises(PortfolioError) as caught:
            await asyncio.wait_for(upload(service, b"invalid file"), timeout=0.5)
    assert caught.value.code == "UNSUPPORTED_FORMAT"
    assert database["real_portfolio_imports"].documents == []


@pytest.mark.parametrize("preexisting", [False, True])
async def test_reservation_failure_leaves_no_new_archive(system, tmp_path, preexisting):
    service, _, database = system
    if preexisting:
        archive_portfolio_bytes(tmp_path, "user-1", delivery_bytes())
    database["real_portfolio_imports"].insert_one.side_effect = OSError(
        "private detail"
    )
    with pytest.raises(PortfolioError) as caught:
        await upload(service)
    assert caught.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
    assert "private detail" not in str(caught.value)
    assert database["real_portfolio_imports"].documents == []
    assert len(list(tmp_path.rglob("*.xls"))) == int(preexisting)


@pytest.mark.parametrize(
    "stage",
    [
        "archive",
        "partial_source",
        "after_source",
        "generation",
        "summary",
        "activation",
    ],
)
async def test_failure_keeps_previous_generation_and_retry_is_idempotent(
    system, tmp_path, monkeypatch, stage
):
    service, repo, database = system
    await upload(service)
    before = await active(repo)
    content = delivery_bytes(delivery_row(成交编号="second", 成交数量="200"))
    with monkeypatch.context() as patch:
        if stage == "archive":
            patch.setattr(
                "app.services.real_portfolio.service.archive_portfolio_bytes",
                lambda *args: (_ for _ in ()).throw(OSError("private path")),
            )
        elif stage == "partial_source":
            collection = database["real_portfolio_source_row_revisions"]

            async def fail_after_row(documents):
                await collection._insert_one(documents[0])
                raise OSError("source interrupted")

            patch.setattr(collection, "insert_many", fail_after_row)
        elif stage == "generation":
            database["real_portfolio_postings"].insert_many.side_effect = OSError(
                "generation interrupted"
            )
        else:
            method = {
                "after_source": "load_rebuild_imports",
                "summary": "stage_import_summary",
                "activation": "activate_generation",
            }[stage]
            patch.setattr(repo, method, AsyncMock(side_effect=OSError("interrupted")))
        with pytest.raises(PortfolioError) as caught:
            await upload(service, content)
        assert caught.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
        assert await active(repo) == before
        current = database["real_portfolio_imports"].documents[1]
        assert current["status"] == "failed" and current["completed_at"] is None
        assert len(list(tmp_path.rglob("*.xls"))) == (1 if stage == "archive" else 2)
    database["real_portfolio_postings"].insert_many.side_effect = database[
        "real_portfolio_postings"
    ]._insert_many
    result = await upload(service, content)
    assert result.status == "imported" and result.new_facts == 1
    assert len((await active(repo)).events) == 2
    assert len(database["real_portfolio_imports"].documents) == 2
    assert len(database["real_portfolio_source_rows"].documents) == 2
    assert (
        database["real_portfolio_imports"].documents[1]["import_id"]
        == current["import_id"]
    )


@pytest.mark.parametrize("stage", ["after_activation", "mark_imported"])
async def test_post_activation_retry_finalizes_staged_summary_without_rebuild(
    system, tmp_path, monkeypatch, stage
):
    service, repo, database = system
    content = delivery_bytes(delivery_row(), delivery_row(成交数量="bad"))
    with monkeypatch.context() as patch:
        if stage == "after_activation":
            activate = repo.activate_generation

            async def fail_after_activation(**kwargs):
                await activate(**kwargs)
                raise OSError("activation acknowledgement lost")

            patch.setattr(repo, "activate_generation", fail_after_activation)
        else:
            patch.setattr(
                repo,
                "mark_imported",
                AsyncMock(side_effect=OSError("completion interrupted")),
            )
        with pytest.raises(PortfolioError):
            await upload(service, content)
    visible = await active(repo)
    stored = deepcopy(database["real_portfolio_imports"].documents[0])
    generation = database["real_portfolio_accounts"].documents[0][
        "active_derived_generation"
    ]
    assert len(visible.events) == 1
    assert stored["status"] == "failed" and stored["completed_at"] is None
    assert stored["summary"]["source_rows"] == 2 and stored["summary"]["new_facts"] == 1
    assert stored["summary"]["warnings"]
    assert len(list(tmp_path.rglob("*.xls"))) == 1
    monkeypatch.setattr(
        repo, "replace_import_facts", AsyncMock(side_effect=AssertionError)
    )
    monkeypatch.setattr(
        repo, "load_rebuild_imports", AsyncMock(side_effect=AssertionError)
    )
    result = await upload(service, content)
    assert result.status == "imported" and result.new_facts == 1
    assert result.source_rows == 2 and result.warnings == visible.warnings
    assert await active(repo) == visible
    assert (
        database["real_portfolio_accounts"].documents[0]["active_derived_generation"]
        == generation
    )
    final = database["real_portfolio_imports"].documents[0]
    assert final["summary"] == stored["summary"]
    assert final["status"] == "imported" and final["completed_at"] is not None
    assert len(database["real_portfolio_source_rows"].documents) == 2


@pytest.mark.parametrize("version_field", ["parser_version", "derived_version"])
async def test_stale_versions_rebuild_instead_of_recovering_old_generation(
    system, version_field
):
    service, repo, database = system
    await upload(service)
    before = await active(repo)
    account = database["real_portfolio_accounts"].documents[0]
    generation = account["active_derived_generation"]
    document = database["real_portfolio_imports"].documents[0]
    await database["real_portfolio_imports"].update_one(
        {"import_id": document["import_id"]}, {"$set": {version_field: "obsolete"}}
    )
    result = await upload(service)
    assert result.status == "imported"
    assert await active(repo) == before
    assert (
        database["real_portfolio_accounts"].documents[0]["active_derived_generation"]
        != generation
    )
    assert (
        database["real_portfolio_imports"].documents[0]["derived_version"]
        == DERIVED_VERSION
    )
    assert len(database["real_portfolio_source_rows"].documents) == 1


async def test_preview_and_import_count_overlap_conflicts_and_new_facts(system):
    service, repo, _ = system
    await upload(
        service,
        delivery_bytes(
            delivery_row(成交编号="same"), delivery_row(成交编号="conflict")
        ),
    )
    content = delivery_bytes(
        delivery_row(成交编号="same"),
        delivery_row(成交编号="conflict", 成交数量="200"),
        delivery_row(成交编号="new"),
        delivery_row(成交编号="new"),
    )
    preview = await service.preview_file(
        user_id="user-1", filename="x.xls", content=content, as_of=None
    )
    imported = await upload(service, content)
    for result in (preview, imported):
        assert (result.source_rows, result.usable_rows) == (4, 4)
        assert (result.new_facts, result.duplicate_facts, result.conflicting_facts) == (
            1,
            2,
            1,
        )
        assert result.events == 3
        assert any(w.warning_type == "source_fact_replaced" for w in result.warnings)
    assert len((await active(repo)).events) == 3


async def test_partial_event_summary_and_filename_sanitization(system):
    service, _, database = system
    result = await service.import_file(
        user_id="user-1",
        filename="../private\\folder\\trade\x00.xls",
        content=delivery_bytes(hk_pair()[0]),
        as_of=None,
    )
    assert (result.events, result.partial_events, result.unclassified_events) == (
        1,
        1,
        0,
    )
    assert (
        database["real_portfolio_imports"].documents[0]["source_filename"]
        == "trade.xls"
    )


async def test_failure_recording_error_still_returns_sanitized_storage_error(
    system, monkeypatch
):
    service, repo, database = system
    monkeypatch.setattr(
        repo, "replace_import_facts", AsyncMock(side_effect=OSError("private account"))
    )
    monkeypatch.setattr(
        repo, "mark_failed", AsyncMock(side_effect=OSError("private db"))
    )
    with pytest.raises(PortfolioError) as caught:
        await upload(service)
    assert caught.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
    assert "private" not in str(caught.value)
    assert database["real_portfolio_imports"].documents[0]["status"] == "publishing"
