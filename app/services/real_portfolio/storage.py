"""Standalone-Mongo persistence for immutable facts and copy-then-switch views."""

import re
import types
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal
from functools import cache
from typing import Union, get_args, get_origin, get_type_hints
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import (
    CurrencyMovement,
    ImportedFacts,
    ImportHistoryItem,
    ImportSummary,
    Page,
    ParsedPortfolioFile,
    ParseWarning,
    PortfolioEvent,
    ReconciledPortfolio,
    SnapshotAnchor,
    TradeFilters,
    TradeItem,
    canonical_decimal_string,
)

DERIVED_VERSION = "real-portfolio-v1"


@dataclass(frozen=True, slots=True)
class ImportReservation:
    document: Mapping[str, object]
    created: bool


@dataclass(frozen=True, slots=True)
class GenerationManifest:
    generation: str
    import_ids: tuple[str, ...]
    snapshot_count: int
    position_count: int
    event_count: int
    posting_count: int
    evidence_count: int
    warning_count: int


def _encode(value):
    if is_dataclass(value):
        return {
            field.name: _encode(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Decimal):
        return canonical_decimal_string(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _encode(item) for key, item in value.items()}
    return value


@cache
def _record_types(record):
    return get_type_hints(record)


def _decode(record, value):
    if value is None:
        return None
    origin, arguments = get_origin(record), get_args(record)
    if origin in (Union, types.UnionType):
        return _decode(next(t for t in arguments if t is not type(None)), value)
    if origin is tuple:
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(_decode(arguments[0], item) for item in value)
        return tuple(_decode(t, item) for t, item in zip(arguments, value, strict=True))
    if is_dataclass(record):
        return record(
            **{
                name: _decode(t, value[name])
                for name, t in _record_types(record).items()
            }
        )
    if record is Decimal:
        return Decimal(value)
    if record in (datetime, date, time):
        return record.fromisoformat(value)
    return value


def _scope(user_id, account_alias):
    if account_alias != "main":
        raise ValueError("real portfolio account must be main")
    return {"user_id": user_id, "account_alias": account_alias}


def _unavailable():
    return PortfolioError(
        "PORTFOLIO_STORAGE_UNAVAILABLE", "portfolio generation is incomplete"
    )


def _group_documents(documents, key):
    grouped = defaultdict(list)
    for document in documents:
        grouped[document[key]].append(document)
    return grouped


async def ensure_real_portfolio_indexes(db) -> None:
    account = ("user_id", "account_alias")
    generation = (*account, "derived_generation")
    indexes = (
        ("accounts", "account_identity", account, True),
        ("imports", "import_identity", (*account, "file_sha256"), True),
        ("imports", "import_history", (*account, "completed_at"), False),
        ("source_rows", "source_fact", (*account, "fact_key"), False),
        ("source_rows", "source_row_identity", ("import_id", "line_number"), True),
        (
            "snapshots",
            "snapshot_generation",
            (*generation, "observed_on", "import_id"),
            False,
        ),
        (
            "snapshot_positions",
            "snapshot_position_identity",
            ("snapshot_id", "security_id"),
            True,
        ),
        ("events", "event_identity", (*generation, "event_id"), True),
        (
            "postings",
            "event_postings",
            (*generation, "event_id", "effective_date"),
            False,
        ),
        (
            "event_evidence",
            "event_evidence_identity",
            (*generation, "event_id", "import_id", "line_number", "evidence_role"),
            True,
        ),
        ("warnings", "import_warnings", (*account, "import_id"), False),
        ("warnings", "generation_warnings", generation, False),
    )
    for collection, name, keys, unique in indexes:
        await db[f"real_portfolio_{collection}"].create_index(
            [(key, -1 if key == "completed_at" else 1) for key in keys],
            name=name,
            unique=unique,
        )


class RealPortfolioRepository:
    def __init__(self, db):
        self.db = db

    def _collection(self, name):
        return self.db[f"real_portfolio_{name}"]

    async def reserve_import(
        self,
        *,
        user_id: str,
        account_alias: str,
        source_filename: str,
        parsed: ParsedPortfolioFile,
    ) -> ImportReservation:
        scope = _scope(user_id, account_alias)
        identity = {**scope, "file_sha256": parsed.file_sha256}
        imports = self._collection("imports")
        existing = await imports.find_one(identity)
        if existing is not None:
            return ImportReservation(existing, False)
        account = await self._collection("accounts").find_one_and_update(
            scope,
            {"$inc": {"import_sequence": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        filename = source_filename.replace("\\", "/").rsplit("/", 1)[-1]
        filename = "".join(char for char in filename if char.isprintable())[:255]
        doc = {
            **identity,
            "import_id": uuid4().hex,
            "import_sequence": account["import_sequence"],
            "source_filename": filename or "portfolio.xls",
            "status": "publishing",
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": None,
            "attempt": 0,
            "derived_version": None,
            "parser_version": parsed.parser_version,
            "source_type": parsed.source_type,
            "format_id": parsed.format_id,
            "observed_on": _encode(parsed.observed_on),
            "coverage_from": _encode(parsed.coverage_from),
            "coverage_through": _encode(parsed.coverage_through),
            "row_count": len(parsed.rows),
            "warning_count": len(parsed.warnings),
            "facts_complete": False,
        }
        try:
            await imports.insert_one(doc)
        except DuplicateKeyError:
            existing = await imports.find_one(identity)
            if existing is None:
                raise
            return ImportReservation(existing, False)
        return ImportReservation(doc, True)

    async def replace_import_facts(
        self,
        *,
        user_id: str,
        account_alias: str,
        import_doc: Mapping[str, object],
        parsed: ParsedPortfolioFile,
    ) -> None:
        scope = _scope(user_id, account_alias)
        query = {**scope, "import_id": import_doc["import_id"]}
        imports = self._collection("imports")
        stored = await imports.find_one(query)
        if stored is None or stored["file_sha256"] != parsed.file_sha256:
            raise _unavailable()
        if stored["observed_on"] != _encode(parsed.observed_on):
            raise PortfolioError(
                "SNAPSHOT_DATE_CONFLICT",
                "snapshot date cannot change",
                {"observed_on": str(stored["observed_on"])},
            )
        await imports.update_one(
            query,
            {
                "$set": {"facts_complete": False, "status": "publishing"},
                "$inc": {"attempt": 1},
            },
        )
        observations = {
            obs.evidence.line_number: obs for obs in parsed.delivery_observations
        }
        for index, row in enumerate(parsed.rows):
            document = {**query, **_encode(row), "row_index": index}
            if row.line_number in observations:
                document["observation"] = _encode(observations[row.line_number])
            # Position slots preserve parser order without embedding a whole file in an import.
            if index < len(parsed.snapshot_positions):
                document["snapshot_position"] = _encode(
                    parsed.snapshot_positions[index]
                )
            await self._collection("source_rows").replace_one(
                {**query, "line_number": row.line_number},
                document,
                upsert=True,
            )
        await self._collection("source_rows").delete_many(
            {**query, "line_number": {"$nin": [row.line_number for row in parsed.rows]}}
        )
        warning_query = {**query, "warning_scope": "parse"}
        await self._collection("warnings").delete_many(warning_query)
        warnings = [
            replace(warning, import_id=str(import_doc["import_id"]))
            for warning in parsed.warnings
        ]
        if warnings:
            await self._collection("warnings").insert_many(
                [
                    {**_encode(w), **warning_query, "warning_index": index}
                    for index, w in enumerate(warnings)
                ]
            )
        metadata = {
            key: value
            for key, value in _encode(parsed).items()
            if key
            not in {"rows", "snapshot_positions", "delivery_observations", "warnings"}
        }
        await imports.update_one(
            query,
            {
                "$set": {
                    "parsed_metadata": metadata,
                    "facts_complete": True,
                    "parser_version": parsed.parser_version,
                    "row_count": len(parsed.rows),
                    "usable_rows": sum(row.usable for row in parsed.rows),
                    "warning_count": len(warnings),
                },
                "$unset": {"last_error_class": ""},
            },
        )

    async def load_rebuild_imports(
        self, *, user_id: str, account_alias: str, current_import_id: str
    ) -> tuple[ImportedFacts, ...]:
        scope = _scope(user_id, account_alias)
        documents = (
            await self._collection("imports")
            .find(
                {
                    **scope,
                    "facts_complete": True,
                    "$or": [{"status": "imported"}, {"import_id": current_import_id}],
                }
            )
            .sort([("import_sequence", 1)])
            .to_list(length=None)
        )
        result = []
        for doc in documents:
            query = {**scope, "import_id": doc["import_id"]}
            rows = (
                await self._collection("source_rows")
                .find(query)
                .sort([("row_index", 1)])
                .to_list(length=None)
            )
            warnings = (
                await self._collection("warnings")
                .find({**query, "warning_scope": "parse"})
                .sort([("warning_index", 1)])
                .to_list(length=None)
            )
            data = {
                **doc["parsed_metadata"],
                "rows": rows,
                "warnings": warnings,
                "snapshot_positions": [
                    r["snapshot_position"] for r in rows if "snapshot_position" in r
                ],
                "delivery_observations": [
                    r["observation"] for r in rows if "observation" in r
                ],
            }
            result.append(
                ImportedFacts(
                    doc["import_id"],
                    doc["import_sequence"],
                    _decode(ParsedPortfolioFile, data),
                )
            )
        return tuple(result)

    async def _parse_warnings(self, scope, import_ids):
        documents = (
            await self._collection("warnings")
            .find(
                {
                    **scope,
                    "warning_scope": "parse",
                    "import_id": {"$in": list(import_ids)},
                }
            )
            .to_list(length=None)
        )
        return tuple(_decode(ParseWarning, doc) for doc in documents)

    async def write_generation(
        self,
        *,
        user_id: str,
        account_alias: str,
        generation: str,
        portfolio: ReconciledPortfolio,
    ) -> GenerationManifest:
        scope = _scope(user_id, account_alias)
        account = await self._collection("accounts").find_one(scope)
        if account and account.get("active_derived_generation") == generation:
            raise _unavailable()
        query = {**scope, "derived_generation": generation}
        documents = {
            key: []
            for key in (
                "snapshots",
                "snapshot_positions",
                "events",
                "postings",
                "event_evidence",
                "warnings",
            )
        }
        for anchor in portfolio.snapshots:
            snapshot_id = uuid4().hex
            data = _encode(anchor)
            data.pop("positions")
            documents["snapshots"].append(
                {
                    **data,
                    **query,
                    "snapshot_id": snapshot_id,
                    "anchor_id": anchor.snapshot_id,
                }
            )
            documents["snapshot_positions"].extend(
                {
                    **_encode(position),
                    **query,
                    "snapshot_id": snapshot_id,
                    "security_id": str(position.security),
                    "position_index": index,
                }
                for index, position in enumerate(anchor.positions)
            )
        for event in portfolio.events:
            data = _encode(event)
            data.pop("postings")
            data.pop("evidence")
            event_scope = {**query, "event_id": event.event_id}
            documents["events"].append(
                {
                    **data,
                    **event_scope,
                    "security_id": str(event.security) if event.security else None,
                    "market": event.security.market if event.security else None,
                }
            )
            documents["postings"].extend(
                {**_encode(posting), **event_scope, "posting_index": index}
                for index, posting in enumerate(event.postings)
            )
            documents["event_evidence"].extend(
                {
                    **_encode(ref),
                    **event_scope,
                    "evidence_role": ref.role,
                    "evidence_index": index,
                }
                for index, ref in enumerate(event.evidence)
            )
        parse_warnings = set(await self._parse_warnings(scope, portfolio.import_ids))
        documents["warnings"] = [
            {**_encode(w), **query, "warning_scope": "derived"}
            for w in portfolio.warnings
            if w not in parse_warnings
        ]
        for name, rows in documents.items():
            cleanup = (
                {**query, "warning_scope": "derived"} if name == "warnings" else query
            )
            await self._collection(name).delete_many(cleanup)
            if rows:
                await self._collection(name).insert_many(rows)
        manifest = GenerationManifest(
            generation,
            portfolio.import_ids,
            len(documents["snapshots"]),
            len(documents["snapshot_positions"]),
            len(documents["events"]),
            len(documents["postings"]),
            len(documents["event_evidence"]),
            len(documents["warnings"]),
        )
        await self._collection("accounts").update_one(
            scope,
            {
                "$set": {
                    "pending_generation_manifest": _encode(manifest),
                    "pending_reported_coverage": _encode(portfolio.reported_coverage),
                }
            },
            upsert=True,
        )
        return manifest

    async def _validate_generation(self, scope, manifest):
        query = {**scope, "derived_generation": manifest.generation}
        for name, expected in (
            ("snapshots", manifest.snapshot_count),
            ("snapshot_positions", manifest.position_count),
            ("events", manifest.event_count),
            ("postings", manifest.posting_count),
            ("event_evidence", manifest.evidence_count),
            ("warnings", manifest.warning_count),
        ):
            counted = (
                {**query, "warning_scope": "derived"} if name == "warnings" else query
            )
            if await self._collection(name).count_documents(counted) != expected:
                raise _unavailable()

    async def activate_generation(
        self, *, user_id: str, account_alias: str, manifest: GenerationManifest
    ) -> None:
        scope = _scope(user_id, account_alias)
        account = await self._collection("accounts").find_one(scope)
        if not account or account.get("pending_generation_manifest") != _encode(
            manifest
        ):
            raise _unavailable()
        await self._validate_generation(scope, manifest)
        await self._collection("accounts").update_one(
            scope,
            {
                "$set": {
                    "active_derived_generation": manifest.generation,
                    "active_generation_manifest": _encode(manifest),
                    "active_reported_coverage": account["pending_reported_coverage"],
                }
            },
        )

    async def find_active_generation_for_import(
        self, *, user_id: str, account_alias: str, import_id: str
    ) -> str | None:
        scope = _scope(user_id, account_alias)
        account = await self._collection("accounts").find_one(scope)
        if not account or not account.get("active_generation_manifest"):
            return None
        manifest = _decode(GenerationManifest, account["active_generation_manifest"])
        if import_id not in manifest.import_ids:
            return None
        await self._validate_generation(scope, manifest)
        return manifest.generation

    async def mark_imported(
        self, *, import_id: str, generation: str, summary: ImportSummary
    ) -> None:
        # Import IDs are globally unique and obtained from an account-scoped reservation.
        await self._collection("imports").update_one(
            {"import_id": import_id},
            {
                "$set": {
                    "status": "imported",
                    "derived_generation": generation,
                    "derived_version": DERIVED_VERSION,
                    "summary": _encode(summary),
                    "completed_at": datetime.now(UTC).isoformat(),
                    "warning_count": len(summary.warnings),
                },
                "$unset": {"last_error_class": ""},
            },
        )

    async def mark_failed(self, *, import_id: str, error_class: str) -> None:
        safe_class = (
            error_class
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", error_class)
            else "StorageError"
        )
        await self._collection("imports").update_one(
            {"import_id": import_id},
            {
                "$set": {
                    "status": "failed",
                    "last_error_class": safe_class,
                }
            },
        )

    async def _load_events(self, query, documents):
        ids = [document["event_id"] for document in documents]
        related = {**query, "event_id": {"$in": ids}}
        postings = (
            await self._collection("postings")
            .find(related)
            .sort([("posting_index", 1)])
            .to_list(length=None)
        )
        evidence = (
            await self._collection("event_evidence")
            .find(related)
            .sort([("evidence_index", 1)])
            .to_list(length=None)
        )
        postings_by_event = _group_documents(postings, "event_id")
        evidence_by_event = _group_documents(evidence, "event_id")
        return tuple(
            _decode(
                PortfolioEvent,
                {
                    **doc,
                    "postings": postings_by_event[doc["event_id"]],
                    "evidence": evidence_by_event[doc["event_id"]],
                },
            )
            for doc in documents
        )

    async def load_active_portfolio(
        self, *, user_id: str, account_alias: str
    ) -> ReconciledPortfolio:
        scope = _scope(user_id, account_alias)
        account = await self._collection("accounts").find_one(scope)
        if not account or not account.get("active_derived_generation"):
            return ReconciledPortfolio((), (), (), (), ())
        query = {**scope, "derived_generation": account["active_derived_generation"]}
        manifest = _decode(GenerationManifest, account["active_generation_manifest"])
        anchors = (
            await self._collection("snapshots")
            .find(query)
            .sort([("observed_on", 1), ("import_sequence", 1), ("anchor_id", 1)])
            .to_list(length=None)
        )
        positions = (
            await self._collection("snapshot_positions")
            .find(query)
            .sort([("position_index", 1)])
            .to_list(length=None)
        )
        positions_by_snapshot = _group_documents(positions, "snapshot_id")
        snapshots = tuple(
            _decode(
                SnapshotAnchor,
                {
                    **anchor,
                    "snapshot_id": anchor["anchor_id"],
                    "positions": positions_by_snapshot[anchor["snapshot_id"]],
                },
            )
            for anchor in anchors
        )
        events = (
            await self._collection("events")
            .find(query)
            .sort([("event_id", 1)])
            .to_list(length=None)
        )
        warnings = (
            await self._collection("warnings")
            .find({**query, "warning_scope": "derived"})
            .to_list(length=None)
        )
        all_warnings = set(await self._parse_warnings(scope, manifest.import_ids)) | {
            _decode(ParseWarning, w) for w in warnings
        }
        return ReconciledPortfolio(
            snapshots,
            await self._load_events(query, events),
            tuple(
                sorted(
                    all_warnings,
                    key=lambda w: (
                        w.warning_type,
                        w.import_id or "",
                        w.line_number if w.line_number is not None else -1,
                        w.impact_from or date.min,
                        w.impact_through or date.max,
                        w.message,
                        w.affects_quantity,
                    ),
                )
            ),
            _decode(tuple[tuple[date, date], ...], account["active_reported_coverage"]),
            manifest.import_ids,
        )

    async def list_active_trades(
        self,
        *,
        user_id: str,
        account_alias: str,
        filters: TradeFilters,
        page: int,
        page_size: int,
    ) -> Page[TradeItem]:
        if page < 1 or page_size < 1:
            raise ValueError("pagination must be positive")
        scope = _scope(user_id, account_alias)
        account = await self._collection("accounts").find_one(scope)
        if not account or not account.get("active_derived_generation"):
            return Page((), page, page_size, 0)
        generation_query = {
            **scope,
            "derived_generation": account["active_derived_generation"],
        }
        query = dict(generation_query)
        for key in ("market", "event_type", "completeness"):
            if getattr(filters, key) is not None:
                query[key] = getattr(filters, key)
        if filters.security_id is not None:
            query["security_id"] = str(filters.security_id)
        dates = {}
        if filters.date_from is not None:
            dates["$gte"] = filters.date_from.isoformat()
        if filters.date_through is not None:
            dates["$lte"] = filters.date_through.isoformat()
        if dates:
            query["trade_date"] = dates
        collection = self._collection("events")
        total = await collection.count_documents(query)
        documents = (
            await collection.find(query)
            .sort([("trade_date", -1), ("event_id", -1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
            .to_list(length=page_size)
        )
        events = await self._load_events(generation_query, documents)
        items = []
        for event in events:
            details = dict(event.details)
            quantities = [p.amount for p in event.postings if p.role == "security"]
            items.append(
                TradeItem(
                    event.event_id,
                    event.trade_date,
                    event.settlement_date,
                    event.security,
                    details.get("broker_name") or None,
                    event.event_type,
                    details.get("operation", event.event_type),
                    sum(quantities, Decimal(0)) if quantities else None,
                    details.get("trade_currency") or None,
                    tuple(
                        CurrencyMovement(p.currency, p.amount)
                        for p in event.postings
                        if p.role == "cash" and p.currency
                    ),
                    event.completeness,
                    event.warnings,
                )
            )
        return Page(tuple(items), page, page_size, total)

    async def list_imports(
        self, *, user_id: str, account_alias: str, page: int, page_size: int
    ) -> Page[ImportHistoryItem]:
        if page < 1 or page_size < 1:
            raise ValueError("pagination must be positive")
        query = _scope(user_id, account_alias)
        collection = self._collection("imports")
        total = await collection.count_documents(query)
        documents = (
            await collection.find(query)
            .sort([("completed_at", -1), ("import_sequence", -1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
            .to_list(length=page_size)
        )
        items = tuple(
            _decode(
                ImportHistoryItem, {**doc, "file_sha256_short": doc["file_sha256"][:12]}
            )
            for doc in documents
        )
        return Page(items, page, page_size, total)
