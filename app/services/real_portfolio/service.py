"""Single-worker publication of broker imports with resumable generations."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from app.services.real_portfolio.archive import archive_portfolio_bytes
from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.formats import PARSER_VERSION, parse_portfolio_file
from app.services.real_portfolio.holdings import build_portfolio_view
from app.services.real_portfolio.models import (
    ImportedFacts,
    ImportHistoryItem,
    ImportSummary,
    Page,
    ParsedPortfolioFile,
    ParseWarning,
    PortfolioView,
    ReconciledPortfolio,
    SnapshotAnchor,
    TradeFilters,
    TradeItem,
)
from app.services.real_portfolio.quotes import attach_latest_quotes, load_latest_quotes
from app.services.real_portfolio.reconciliation import reconcile_imports
from app.services.real_portfolio.storage import (
    DERIVED_VERSION,
    MAX_PAGE_SIZE,
    RealPortfolioRepository,
)

if TYPE_CHECKING:
    from app.services.unified_stock_service import UnifiedStockService

_import_locks: dict[tuple[str, str], asyncio.Lock] = {}
logger = logging.getLogger(__name__)


def latest_full_snapshot_date(snapshots: Sequence[SnapshotAnchor]) -> date:
    full_dates = [
        snapshot.observed_on for snapshot in snapshots if snapshot.full_snapshot
    ]
    if not full_dates:
        raise PortfolioError("NO_FULL_SNAPSHOT", "no full portfolio snapshot")
    return max(full_dates)


def _validate_pagination(page: int, page_size: int) -> None:
    if page < 1 or page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(
            f"pagination requires page >= 1 and page_size <= {MAX_PAGE_SIZE}"
        )


def account_import_lock(user_id: str, account_alias: str) -> asyncio.Lock:
    if account_alias != "main":
        raise ValueError("real portfolio account must be main")
    return _import_locks.setdefault((user_id, "main"), asyncio.Lock())


def build_import_summary(
    import_doc: Mapping[str, object],
    portfolio: ReconciledPortfolio,
    status: Literal["preview", "imported", "duplicate"],
) -> ImportSummary:
    return ImportSummary(
        status=status,
        source_type=import_doc["source_type"],
        format_id=import_doc["format_id"],
        file_sha256_short=str(import_doc["file_sha256"])[:12],
        source_rows=import_doc["row_count"],
        usable_rows=import_doc["usable_rows"],
        new_facts=import_doc["new_facts"],
        duplicate_facts=import_doc["duplicate_facts"],
        conflicting_facts=import_doc["conflicting_facts"],
        events=len(portfolio.events),
        partial_events=sum(e.completeness == "partial" for e in portfolio.events),
        unclassified_events=sum(
            e.completeness == "unclassified" for e in portfolio.events
        ),
        warnings=portfolio.warnings,
    )


def summary_from_import(
    import_doc: Mapping[str, object], status: Literal["imported", "duplicate"]
) -> ImportSummary:
    data = dict(import_doc["summary"])
    data["status"] = status
    data["warnings"] = tuple(
        ParseWarning(
            **{
                **warning,
                "impact_from": date.fromisoformat(warning["impact_from"])
                if warning["impact_from"]
                else None,
                "impact_through": date.fromisoformat(warning["impact_through"])
                if warning["impact_through"]
                else None,
            }
        )
        for warning in data["warnings"]
    )
    return ImportSummary(**data)


def _summary_document(
    import_doc: Mapping[str, object],
    parsed: ParsedPortfolioFile,
    inputs: Sequence[ImportedFacts],
) -> dict[str, object]:
    current = {}
    for imported in sorted(inputs, key=lambda item: item.import_sequence):
        if imported.import_id == import_doc["import_id"]:
            continue
        for row in sorted(imported.parsed.rows, key=lambda item: item.line_number):
            if row.usable:
                current[row.fact_key] = row.row_sha256
    new = duplicate = conflicting = 0
    for row in sorted(parsed.rows, key=lambda item: item.line_number):
        if not row.usable:
            continue
        previous = current.get(row.fact_key)
        if previous is None:
            new += 1
        elif previous == row.row_sha256:
            duplicate += 1
        else:
            conflicting += 1
        current[row.fact_key] = row.row_sha256
    return {
        **import_doc,
        "source_type": parsed.source_type,
        "format_id": parsed.format_id,
        "file_sha256": parsed.file_sha256,
        "row_count": len(parsed.rows),
        "usable_rows": sum(row.usable for row in parsed.rows),
        "new_facts": new,
        "duplicate_facts": duplicate,
        "conflicting_facts": conflicting,
    }


class RealPortfolioService:
    def __init__(
        self,
        repository: RealPortfolioRepository,
        archive_root: Path,
        quote_service: "UnifiedStockService | None" = None,
    ):
        self.repository = repository
        self.archive_root = Path(archive_root)
        self.quote_service = quote_service

    async def get_positions(self, *, user_id: str, as_of: date | None) -> PortfolioView:
        portfolio = await self.repository.load_active_portfolio(
            user_id=user_id, account_alias="main"
        )
        requested = as_of or latest_full_snapshot_date(portfolio.snapshots)
        view = build_portfolio_view(portfolio, requested)
        quotes = await load_latest_quotes(
            self.quote_service, tuple(holding.security for holding in view.holdings)
        )
        return attach_latest_quotes(view, quotes)

    async def list_trades(
        self,
        *,
        user_id: str,
        filters: TradeFilters,
        page: int,
        page_size: int,
    ) -> Page[TradeItem]:
        _validate_pagination(page, page_size)
        return await self.repository.list_active_trades(
            user_id=user_id,
            account_alias="main",
            filters=filters,
            page=page,
            page_size=page_size,
        )

    async def list_imports(
        self, *, user_id: str, page: int, page_size: int
    ) -> Page[ImportHistoryItem]:
        _validate_pagination(page, page_size)
        return await self.repository.list_imports(
            user_id=user_id,
            account_alias="main",
            page=page,
            page_size=page_size,
        )

    async def preview_file(
        self, *, user_id: str, filename: str, content: bytes, as_of: date | None
    ) -> ImportSummary:
        parsed = parse_portfolio_file(content, as_of=as_of)
        try:
            inputs = await self.repository.load_rebuild_imports(
                user_id=user_id, account_alias="main", current_import_id=""
            )
            preview_id = uuid4().hex
            current = ImportedFacts(
                preview_id,
                max((item.import_sequence for item in inputs), default=0) + 1,
                parsed,
            )
            portfolio = reconcile_imports(
                user_id=user_id, account_alias="main", imports=(*inputs, current)
            )
            portfolio = replace(
                portfolio,
                warnings=tuple(
                    replace(warning, import_id=None)
                    if warning.import_id == preview_id
                    else warning
                    for warning in portfolio.warnings
                ),
            )
            return build_import_summary(
                _summary_document({"import_id": preview_id}, parsed, inputs),
                portfolio,
                status="preview",
            )
        except Exception as error:  # noqa: BLE001 - sanitize storage failures at the service boundary
            logger.warning("Portfolio preview failed: %s", type(error).__name__)
            raise PortfolioError(
                "PORTFOLIO_STORAGE_UNAVAILABLE", "portfolio preview is unavailable"
            ) from None

    async def import_file(
        self, *, user_id: str, filename: str, content: bytes, as_of: date | None
    ) -> ImportSummary:
        parsed = parse_portfolio_file(content, as_of=as_of)
        async with account_import_lock(user_id, "main"):
            import_doc = None
            date_conflict = False
            try:
                filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
                filename = "".join(c for c in filename if c.isprintable())[:255]
                reservation = await self.repository.reserve_import(
                    user_id=user_id,
                    account_alias="main",
                    source_filename=filename or "portfolio.xls",
                    parsed=parsed,
                )
                import_doc = reservation.document
                if (
                    not reservation.created
                    and parsed.source_type == "snapshot"
                    and import_doc.get("observed_on") != parsed.observed_on.isoformat()
                ):
                    date_conflict = True
                    raise PortfolioError(
                        "SNAPSHOT_DATE_CONFLICT",
                        "snapshot bytes were already imported with a different date",
                        {"observed_on": str(import_doc["observed_on"])},
                    )
                if (
                    import_doc.get("status") == "imported"
                    and import_doc.get("parser_version") == PARSER_VERSION
                    and import_doc.get("derived_version") == DERIVED_VERSION
                ):
                    return summary_from_import(import_doc, status="duplicate")
                import_id = str(import_doc["import_id"])
                if (
                    import_doc.get("status") != "imported"
                    and import_doc.get("summary_parser_version") == PARSER_VERSION
                    and import_doc.get("summary_derived_version") == DERIVED_VERSION
                ):
                    recovered = await self.repository.find_active_generation_for_import(
                        user_id=user_id, account_alias="main", import_id=import_id
                    )
                    if recovered is not None and recovered == import_doc.get(
                        "summary_generation"
                    ):
                        summary = summary_from_import(import_doc, status="imported")
                        await self.repository.mark_imported(
                            import_id=import_id, generation=recovered, summary=summary
                        )
                        return summary
                # Reserve first: every archive created here already has a durable owner.
                archive_portfolio_bytes(self.archive_root, user_id, content)
                await self.repository.replace_import_facts(
                    user_id=user_id,
                    account_alias="main",
                    import_doc=import_doc,
                    parsed=parsed,
                )
                inputs = await self.repository.load_rebuild_imports(
                    user_id=user_id, account_alias="main", current_import_id=import_id
                )
                portfolio = reconcile_imports(
                    user_id=user_id, account_alias="main", imports=inputs
                )
                generation = uuid4().hex
                manifest = await self.repository.write_generation(
                    user_id=user_id,
                    account_alias="main",
                    generation=generation,
                    portfolio=portfolio,
                )
                summary = build_import_summary(
                    _summary_document(import_doc, parsed, inputs),
                    portfolio,
                    status="imported",
                )
                await self.repository.stage_import_summary(
                    import_id=import_id,
                    generation=generation,
                    parser_version=PARSER_VERSION,
                    summary=summary,
                )
                await self.repository.activate_generation(
                    user_id=user_id, account_alias="main", manifest=manifest
                )
                await self.repository.mark_imported(
                    import_id=import_id, generation=generation, summary=summary
                )
                return summary
            except Exception as error:
                if date_conflict:
                    raise
                if import_doc is not None:
                    try:
                        await self.repository.mark_failed(
                            import_id=str(import_doc["import_id"]),
                            error_class=type(error).__name__,
                        )
                    except Exception as failure:  # noqa: BLE001 - retain the original sanitized failure
                        # A publishing record remains resumable if failure recording fails.
                        logger.warning(
                            "Portfolio failure status could not be saved: %s",
                            type(failure).__name__,
                        )
                raise PortfolioError(
                    "PORTFOLIO_STORAGE_UNAVAILABLE",
                    "portfolio import could not be saved",
                ) from None
