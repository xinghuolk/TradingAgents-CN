import ast
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import BSON
from pymongo.errors import DuplicateKeyError

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.formats import parse_portfolio_file
from app.services.real_portfolio.models import ImportSummary, TradeFilters
from app.services.real_portfolio.reconciliation import reconcile_imports
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


def matches(doc, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(doc, branch) for branch in value):
                return False
            continue
        actual = doc.get(key)
        if isinstance(value, dict):
            for operator, operand in value.items():
                if operator == "$in" and actual not in operand:
                    return False
                if operator == "$nin" and actual in operand:
                    return False
                if operator == "$ne" and actual == operand:
                    return False
                if operator == "$gte" and (actual is None or actual < operand):
                    return False
                if operator == "$lte" and (actual is None or actual > operand):
                    return False
        elif actual != value:
            return False
    return True


class Cursor:
    def __init__(self, documents):
        self.documents = deepcopy(documents)

    def sort(self, keys):
        for key, direction in reversed(keys):
            self.documents.sort(
                key=lambda doc: (doc.get(key) is not None, doc.get(key)),
                reverse=direction < 0,
            )
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    async def to_list(self, length=None):
        return self.documents if length is None else self.documents[:length]


class MemoryCollection:
    """Motor boundary with BSON serialization and the indexes this task uses."""

    def __init__(self):
        self.documents = []
        self.indexes = {}
        for name in (
            "create_index",
            "find_one",
            "insert_one",
            "insert_many",
            "update_one",
            "find_one_and_update",
            "delete_many",
            "count_documents",
            "replace_one",
        ):
            setattr(self, name, AsyncMock(side_effect=getattr(self, "_" + name)))

    async def _create_index(self, keys, *, name, unique=False, **kwargs):
        self.indexes[name] = (tuple(keys), unique)

    def find(self, query):
        return Cursor([d for d in self.documents if matches(d, query)])

    async def _find_one(self, query):
        return next((deepcopy(d) for d in self.documents if matches(d, query)), None)

    def persist(self, doc, replacing=None):
        doc = BSON.encode(doc).decode()
        for keys, unique in self.indexes.values():
            if unique:
                identity = tuple(doc.get(k) for k, _ in keys)
                if any(
                    d is not replacing and tuple(d.get(k) for k, _ in keys) == identity
                    for d in self.documents
                ):
                    raise DuplicateKeyError("duplicate")
        return doc

    async def _insert_one(self, doc):
        self.documents.append(self.persist(doc))

    async def _insert_many(self, docs):
        for doc in docs:
            await self._insert_one(doc)

    async def _update_one(self, query, update, upsert=False):
        old = next((d for d in self.documents if matches(d, query)), None)
        if old is None and not upsert:
            return SimpleNamespace(matched_count=0)
        updated = deepcopy(old) if old is not None else deepcopy(query)
        if old is None:
            updated.update(update.get("$setOnInsert", {}))
        updated.update(update.get("$set", {}))
        for key, value in update.get("$inc", {}).items():
            updated[key] = updated.get(key, 0) + value
        for key in update.get("$unset", {}):
            updated.pop(key, None)
        updated = self.persist(updated, old)
        if old is None:
            self.documents.append(updated)
        else:
            self.documents[self.documents.index(old)] = updated
        return SimpleNamespace(matched_count=int(old is not None))

    async def _find_one_and_update(self, query, update, **kwargs):
        await self._update_one(query, update, kwargs.get("upsert", False))
        return await self._find_one(query)

    async def _replace_one(self, query, replacement, upsert=False):
        old = next((d for d in self.documents if matches(d, query)), None)
        if old is not None:
            self.documents[self.documents.index(old)] = self.persist(replacement, old)
        elif upsert:
            self.documents.append(self.persist(replacement))

    async def _delete_many(self, query):
        self.documents[:] = [d for d in self.documents if not matches(d, query)]

    async def _count_documents(self, query):
        return sum(matches(d, query) for d in self.documents)


@pytest.fixture
async def database():
    db = defaultdict(MemoryCollection)
    await ensure_real_portfolio_indexes(db)
    return db


async def reserve(repo, parsed=None, user="user-1"):
    parsed = parsed or parse_portfolio_file(delivery_bytes())
    result = await repo.reserve_import(
        user_id=user,
        account_alias="main",
        source_filename="../delivery.xls",
        parsed=parsed,
    )
    return result, parsed


async def facts(repo, parsed=None, user="user-1"):
    reservation, parsed = await reserve(repo, parsed, user)
    await repo.replace_import_facts(
        user_id=user,
        account_alias="main",
        import_doc=reservation.document,
        parsed=parsed,
    )
    return reservation.document


async def rebuild(repo, doc, user="user-1"):
    imports = await repo.load_rebuild_imports(
        user_id=user, account_alias="main", current_import_id=doc["import_id"]
    )
    return reconcile_imports(user_id=user, account_alias="main", imports=imports)


def summary(parsed):
    return ImportSummary(
        "imported",
        parsed.source_type,
        parsed.format_id,
        parsed.file_sha256[:12],
        len(parsed.rows),
        1,
        1,
        0,
        0,
        1,
        0,
        0,
        parsed.warnings,
    )


async def publish(repo, doc, generation):
    portfolio = await rebuild(repo, doc)
    manifest = await repo.write_generation(
        user_id="user-1",
        account_alias="main",
        generation=generation,
        portfolio=portfolio,
    )
    await repo.activate_generation(
        user_id="user-1", account_alias="main", manifest=manifest
    )
    return portfolio, manifest


async def test_required_indexes_are_named_and_protect_identity(database):
    expected = {
        "accounts": [(("user_id", "account_alias"), True)],
        "imports": [
            (("user_id", "account_alias", "file_sha256"), True),
            (("user_id", "account_alias", "completed_at"), False),
        ],
        "source_rows": [
            (("user_id", "account_alias", "fact_key"), False),
            (("import_id", "line_number"), True),
        ],
        "source_revisions": [(("import_id", "source_revision"), True)],
        "source_row_revisions": [
            (("import_id", "source_revision", "line_number"), True),
            (("user_id", "account_alias", "fact_key"), False),
        ],
        "snapshots": [
            (
                (
                    "user_id",
                    "account_alias",
                    "derived_generation",
                    "observed_on",
                    "import_id",
                ),
                False,
            )
        ],
        "snapshot_positions": [(("snapshot_id", "security_id"), True)],
        "events": [
            (("user_id", "account_alias", "derived_generation", "event_id"), True)
        ],
        "postings": [
            (
                (
                    "user_id",
                    "account_alias",
                    "derived_generation",
                    "event_id",
                    "effective_date",
                ),
                False,
            )
        ],
        "event_evidence": [
            (
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
            )
        ],
        "warnings": [
            (("user_id", "account_alias", "import_id"), False),
            (("user_id", "account_alias", "derived_generation"), False),
        ],
    }
    for collection, indexes in expected.items():
        actual = database["real_portfolio_" + collection].indexes
        assert all(actual)
        assert {
            (tuple(k for k, _ in keys), unique) for keys, unique in actual.values()
        } >= set(indexes)


async def test_reservation_reuses_sequence_and_date_across_retry(database):
    repo = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(snapshot_bytes(), as_of=date(2026, 9, 1))
    first, _ = await reserve(repo, parsed)
    await repo.mark_failed(import_id=first.document["import_id"], error_class="OSError")
    second, _ = await reserve(repo, replace(parsed, observed_on=date(2026, 9, 2)))
    third, _ = await reserve(repo)
    assert first.created and not second.created and third.created
    assert first.document["import_id"] == second.document["import_id"]
    assert first.document["import_sequence"] == second.document["import_sequence"] == 1
    assert third.document["import_sequence"] == 2
    assert second.document["observed_on"] == "2026-09-01"
    assert second.document["status"] == "failed"
    assert first.document["source_filename"] == "delivery.xls"


async def test_duplicate_key_race_reloads_winner(database):
    repo = RealPortfolioRepository(database)
    collection = database["real_portfolio_imports"]

    async def racing_insert(doc):
        winner = dict(doc, import_id="winner", import_sequence=90)
        await collection._insert_one(winner)
        raise DuplicateKeyError("race")

    collection.insert_one.side_effect = racing_insert
    result, _ = await reserve(repo)
    assert not result.created
    assert result.document["import_id"] == "winner"
    assert result.document["import_sequence"] == 90
    assert len(collection.documents) == 1


@pytest.mark.parametrize(
    "parsed",
    [
        parse_portfolio_file(snapshot_bytes(), as_of=date(2026, 9, 1)),
        parse_portfolio_file(delivery_bytes()),
        parse_portfolio_file(delivery_bytes(*hk_pair())),
        parse_portfolio_file(
            delivery_bytes(delivery_row(), delivery_row(成交数量="bad"))
        ),
    ],
)
async def test_source_records_round_trip_and_replace_idempotently(database, parsed):
    repo = RealPortfolioRepository(database)
    doc = await facts(repo, parsed)
    await facts(repo, parsed)
    loaded = await repo.load_rebuild_imports(
        user_id="user-1", account_alias="main", current_import_id=doc["import_id"]
    )
    assert len(loaded) == 1
    assert loaded[0].parsed == replace(
        parsed,
        warnings=tuple(replace(w, import_id=doc["import_id"]) for w in parsed.warnings),
    )
    rows = database["real_portfolio_source_rows"].documents
    assert len(rows) == len(parsed.rows)
    assert "成交编号" not in repr(rows) and "合同编号" not in repr(rows)
    revisions = database["real_portfolio_source_row_revisions"].documents
    assert len(revisions) == 2 * len(parsed.rows)
    assert len({row["source_revision"] for row in revisions}) == 2
    assert "成交编号" not in repr(revisions) and "合同编号" not in repr(revisions)
    assert all(
        d["warning_scope"] == "parse"
        for d in database["real_portfolio_warnings"].documents
    )


async def test_rebuild_excludes_incomplete_current_and_other_failed_imports(database):
    repo = RealPortfolioRepository(database)
    completed = await facts(repo)
    await publish(repo, completed, "old")
    await repo.mark_imported(
        import_id=completed["import_id"],
        generation="old",
        summary=summary(parse_portfolio_file(delivery_bytes())),
    )
    current, _ = await reserve(
        repo, parse_portfolio_file(snapshot_bytes(), as_of=date(2026, 9, 1))
    )
    failed = await facts(
        repo, parse_portfolio_file(delivery_bytes(delivery_row(成交数量="200")))
    )
    await repo.mark_failed(import_id=failed["import_id"], error_class="RuntimeError")
    await facts(repo, user="someone-else")
    loaded = await repo.load_rebuild_imports(
        user_id="user-1",
        account_alias="main",
        current_import_id=current.document["import_id"],
    )
    assert [i.import_id for i in loaded] == [completed["import_id"]]


async def test_parse_warning_order_survives_mongo_query_order(database):
    repo = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(
        delivery_bytes(
            delivery_row(),
            delivery_row(成交数量="bad"),
            delivery_row(成交均价="bad"),
        )
    )
    doc = await facts(repo, parsed)
    collection = database["real_portfolio_warnings"]
    original_find = collection.find

    def reversed_find(query):
        cursor = original_find(query)
        cursor.documents.reverse()
        return cursor

    collection.find = reversed_find
    loaded = await repo.load_rebuild_imports(
        user_id="user-1", account_alias="main", current_import_id=doc["import_id"]
    )
    assert loaded[0].parsed.warnings == tuple(
        replace(warning, import_id=doc["import_id"]) for warning in parsed.warnings
    )


async def test_active_warning_revision_survives_failed_and_completed_replacement(
    database,
):
    repo = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(
        delivery_bytes(delivery_row(), delivery_row(成交数量="bad"))
    )
    doc = await facts(repo, parsed)
    old, _ = await publish(repo, doc, "old-warning-revision")
    await repo.mark_imported(
        import_id=doc["import_id"],
        generation="old-warning-revision",
        summary=summary(parsed),
    )
    replacement = replace(
        parsed,
        warnings=(
            replace(
                parsed.warnings[0], message="reparsed warning", affects_quantity=False
            ),
        ),
    )
    warnings = database["real_portfolio_warnings"]

    async def fail_after_first_warning(documents):
        await warnings._insert_one(documents[0])
        raise OSError("injected partial warning replacement")

    warnings.insert_many.side_effect = fail_after_first_warning
    with pytest.raises(OSError):
        await repo.replace_import_facts(
            user_id="user-1", account_alias="main", import_doc=doc, parsed=replacement
        )
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == old
    )
    assert (
        await repo.find_active_generation_for_import(
            user_id="user-1", account_alias="main", import_id=doc["import_id"]
        )
        is None
    )
    warnings.insert_many.side_effect = warnings._insert_many
    await repo.mark_failed(import_id=doc["import_id"], error_class="OSError")
    await facts(repo, replacement)
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == old
    )
    assert (
        await repo.find_active_generation_for_import(
            user_id="user-1", account_alias="main", import_id=doc["import_id"]
        )
        is None
    )
    new = await rebuild(repo, doc)
    assert new.warnings[0].message == "reparsed warning"
    manifest = await repo.write_generation(
        user_id="user-1",
        account_alias="main",
        generation="new-warning-revision",
        portfolio=new,
    )
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == old
    )
    await repo.activate_generation(
        user_id="user-1", account_alias="main", manifest=manifest
    )
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == new
    )
    assert (
        await repo.find_active_generation_for_import(
            user_id="user-1", account_alias="main", import_id=doc["import_id"]
        )
        == "new-warning-revision"
    )


async def test_copy_then_switch_round_trips_generations_and_warning_scopes(database):
    repo = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(snapshot_bytes(), as_of=date(2026, 9, 1))
    snapshot = await facts(repo, parsed)
    await publish(repo, snapshot, "initial")
    await repo.mark_imported(
        import_id=snapshot["import_id"], generation="initial", summary=summary(parsed)
    )
    delivery = await facts(repo, parse_portfolio_file(delivery_bytes(hk_pair()[0])))
    old, first = await publish(repo, delivery, "generation-1")
    assert first.snapshot_count == first.position_count == first.event_count == 1
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == old
    )
    newer = await facts(
        repo,
        parse_portfolio_file(
            delivery_bytes(delivery_row(), delivery_row(成交数量="bad"))
        ),
    )
    new = await rebuild(repo, newer)
    manifest = await repo.write_generation(
        user_id="user-1", account_alias="main", generation="generation-2", portfolio=new
    )
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == old
    )
    with pytest.raises(PortfolioError):
        await repo.activate_generation(
            user_id="user-1",
            account_alias="main",
            manifest=replace(manifest, event_count=99),
        )
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == old
    )
    await repo.activate_generation(
        user_id="user-1", account_alias="main", manifest=manifest
    )
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == new
    )
    assert await repo.load_active_portfolio(
        user_id="stranger", account_alias="main"
    ) == replace(
        new, snapshots=(), events=(), warnings=(), import_ids=(), reported_coverage=()
    )
    assert (
        await repo.find_active_generation_for_import(
            user_id="user-1", account_alias="main", import_id=newer["import_id"]
        )
        == "generation-2"
    )
    assert (
        await repo.find_active_generation_for_import(
            user_id="user-1", account_alias="main", import_id=delivery["import_id"]
        )
        == "generation-2"
    )


async def test_failed_generation_write_and_incomplete_recovery_keep_active_data(
    database,
):
    repo = RealPortfolioRepository(database)
    doc = await facts(repo)
    old, _ = await publish(repo, doc, "old")
    database["real_portfolio_postings"].insert_many.side_effect = OSError("injected")
    with pytest.raises(OSError):
        await repo.write_generation(
            user_id="user-1", account_alias="main", generation="broken", portfolio=old
        )
    assert (
        await repo.load_active_portfolio(user_id="user-1", account_alias="main") == old
    )
    database["real_portfolio_postings"].documents.clear()
    with pytest.raises(PortfolioError):
        await repo.find_active_generation_for_import(
            user_id="user-1", account_alias="main", import_id=doc["import_id"]
        )


async def test_trade_filters_pagination_and_sanitized_results(database):
    repo = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(
        delivery_bytes(
            delivery_row(成交编号="older", 成交日期="2026-08-31"),
            delivery_row(成交编号="newer"),
        )
    )
    doc = await facts(repo, parsed)
    await publish(repo, doc, "trades")
    page = await repo.list_active_trades(
        user_id="user-1",
        account_alias="main",
        filters=TradeFilters(market="A"),
        page=1,
        page_size=1,
    )
    assert page.total == 2 and page.page_size == 1
    assert page.items[0].trade_date == date(2026, 9, 1)
    assert str(page.items[0].security) == "A:000001"
    assert str(page.items[0].security_quantity) == "100"
    assert str(page.items[0].cash_movements[0].amount) == "-1001"
    assert "older" not in repr(page) and "newer" not in repr(page)
    filtered = await repo.list_active_trades(
        user_id="user-1",
        account_alias="main",
        filters=TradeFilters(
            date_through=date(2026, 8, 31),
            security_id=page.items[0].security,
            event_type=page.items[0].event_type,
            completeness="complete",
        ),
        page=1,
        page_size=5,
    )
    assert filtered.total == 1 and filtered.items[0].trade_date == date(2026, 8, 31)
    empty = await repo.list_active_trades(
        user_id="other",
        account_alias="main",
        filters=TradeFilters(),
        page=1,
        page_size=5,
    )
    assert empty.total == 0 and empty.items == ()


async def test_completion_history_and_failure_metadata_are_safe(database):
    repo = RealPortfolioRepository(database)
    first = await facts(repo)
    second = await facts(
        repo, parse_portfolio_file(snapshot_bytes(), as_of=date(2026, 9, 1))
    )
    parsed = parse_portfolio_file(delivery_bytes())
    await repo.mark_imported(
        import_id=second["import_id"], generation="g", summary=summary(parsed)
    )
    await repo.mark_imported(
        import_id=first["import_id"], generation="g", summary=summary(parsed)
    )
    await database["real_portfolio_imports"].update_one(
        {"import_id": second["import_id"]},
        {"$set": {"completed_at": "2026-01-01T00:00:00+00:00"}},
    )
    result = await repo.list_imports(
        user_id="user-1", account_alias="main", page=1, page_size=1
    )
    assert result.total == 2 and result.items[0].source_type == "delivery_statement"
    persisted = await database["real_portfolio_imports"].find_one(
        {"import_id": first["import_id"]}
    )
    assert persisted["derived_version"] == "real-portfolio-v1"
    assert persisted["summary"]["source_rows"] == 1
    assert persisted["status"] == "imported"
    await repo.mark_failed(
        import_id=first["import_id"], error_class="OSError: /private/raw/account"
    )
    persisted = await database["real_portfolio_imports"].find_one(
        {"import_id": first["import_id"]}
    )
    assert persisted["status"] == "failed"
    assert "private" not in persisted["last_error_class"]
    assert persisted["import_sequence"] == first["import_sequence"]


async def test_staged_summary_is_durable_without_publishing_success(database):
    repo = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(delivery_bytes())
    doc = await facts(repo, parsed)
    await repo.stage_import_summary(
        import_id=doc["import_id"],
        generation="pending",
        parser_version=parsed.parser_version,
        summary=summary(parsed),
    )
    stored = await database["real_portfolio_imports"].find_one(
        {"import_id": doc["import_id"]}
    )
    assert stored["summary"]["new_facts"] == 1
    assert stored["summary"]["warnings"] == []
    assert stored["summary_generation"] == "pending"
    assert stored["summary_parser_version"] == parsed.parser_version
    assert stored["summary_derived_version"] == "real-portfolio-v1"
    assert stored["status"] == "publishing"
    assert stored["completed_at"] is None
    assert stored["derived_version"] is None
    assert (
        not database["real_portfolio_accounts"]
        .documents[0]
        .get("active_derived_generation")
    )


async def test_staging_summary_requires_an_existing_import(database):
    repo = RealPortfolioRepository(database)
    parsed = parse_portfolio_file(delivery_bytes())
    with pytest.raises(PortfolioError) as caught:
        await repo.stage_import_summary(
            import_id="missing",
            generation="pending",
            parser_version=parsed.parser_version,
            summary=summary(parsed),
        )
    assert caught.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"


@pytest.mark.parametrize("fails", [False, True])
async def test_startup_index_readiness_is_nonfatal(fails):
    # Execute the real lifespan prefix, stopping before unrelated service startup.
    module = ast.parse(Path("app/main.py").read_text())
    lifespan = next(
        n
        for n in module.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"
    )
    start = next(
        i
        for i, n in enumerate(lifespan.body)
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Await)
        and isinstance(n.value.value, ast.Call)
        and getattr(n.value.value.func, "id", None) == "init_db"
    )
    end = next(
        i
        for i in range(start + 1, len(lifespan.body))
        if isinstance(lifespan.body[i], ast.ImportFrom)
        and lifespan.body[i].module == "app.core.redis_client"
    )
    function = ast.AsyncFunctionDef(
        name="startup",
        args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=lifespan.body[start:end],
        decorator_list=[],
    )
    tree = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    application = SimpleNamespace(state=SimpleNamespace())
    namespace = {
        "app": application,
        "init_db": AsyncMock(),
        "get_mongo_db": dict,
        "ensure_real_portfolio_indexes": AsyncMock(
            side_effect=RuntimeError("index failure") if fails else None
        ),
        "logger": MagicMock(),
    }
    exec(compile(tree, "app/main.py", "exec"), namespace)  # noqa: S102 - repository-owned startup code
    await namespace["startup"]()
    assert application.state.real_portfolio_import_ready is (not fails)
