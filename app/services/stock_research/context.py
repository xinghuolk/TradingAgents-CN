"""Adjustable generation context composed only from existing, owned sources."""

from dataclasses import replace
from datetime import date, timedelta

from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    Entry, EntryQuery, Reference, ResearchSecurityId, utc_now,
)
from app.services.stock_research.references import ReferenceService
from app.services.stock_research.storage import StockResearchRepository


def _date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ResearchError("INVALID_QUERY", "context date must use YYYY-MM-DD") from None


class ResearchContextBuilder:
    def __init__(
        self, repository: StockResearchRepository, references: ReferenceService
    ) -> None:
        self.repository = repository
        self.references = references

    async def build(
        self,
        user_id: str,
        entry: Entry,
        selected_references: list[Reference] | None = None,
        overrides: dict | None = None,
    ) -> tuple[dict, list[Reference]]:
        options = {
            "include_market": entry.entry_type == "review",
            "include_real_holdings": entry.entry_type == "review",
            "include_paper_holdings": False,
            "include_trades": True,
            "include_reports": True,
            "include_thesis": True,
            "include_recent_entries": True,
            **entry.scope_metadata,
            **(overrides or {}),
        }
        date_from = _date(options.get("date_from") or options.get("date"))
        date_through = _date(options.get("date_through") or options.get("date"))
        if date_from and date_through and date_from > date_through:
            raise ResearchError("INVALID_QUERY", "date_from must not follow date_through")
        security_ids = list(dict.fromkeys((
            *entry.security_ids, *((entry.security_id,) if entry.security_id else ())
        )))
        decision = (
            await self.repository.get_entry(user_id, entry.decision_id)
            if entry.decision_id else None
        )
        if decision is not None and decision.entry_type != "decision":
            decision = None
        if decision and entry.review_kind == "decision":
            if date_from is None and decision.decision_date:
                date_from = min(
                    decision.decision_date, utc_now().date()
                ) - timedelta(days=7)
                options["date_from"] = date_from.isoformat()
            if date_through is None:
                date_through = utc_now().date()
                options["date_through"] = date_through.isoformat()
        if decision and decision.security_id not in security_ids:
            security_ids.append(decision.security_id)
        if (
            decision and "include_paper_holdings" not in entry.scope_metadata
            and "include_paper_holdings" not in (overrides or {})
        ):
            options["include_paper_holdings"] = any(
                ref.account_type == "paper" for ref in decision.references
            )
        sources = []
        defaults = list(entry.references)
        if (
            entry.entry_type == "review" and entry.scope == "portfolio"
            and (options["include_real_holdings"] or options["include_paper_holdings"])
        ):
            try:
                holdings = await self.references.list_holdings(user_id)
                for holding in holdings:
                    if options.get(f"include_{holding.account_type}_holdings"):
                        if holding.security_id not in security_ids:
                            security_ids.append(holding.security_id)
            except Exception:
                sources.append({
                    "kind": "holdings", "available": False,
                    "reason": "source_unavailable",
                })
        if entry.entry_type == "review":
            if entry.decision_id:
                defaults.append(Reference(kind="decision", source_id=entry.decision_id))
            if decision:
                defaults.extend(decision.references)
            for security_id in security_ids:
                # Each source is independent: a missing portfolio must not hide reports.
                security = ResearchSecurityId.from_string(security_id)
                readers = [
                    ("analysis_report", self.references._report_candidates,
                     options["include_reports"]),
                    ("real_trade", self.references._real_trade_candidates,
                     options["include_trades"] and options["include_real_holdings"]),
                    ("paper_trade", self.references._paper_trade_candidates,
                     options["include_trades"] and options["include_paper_holdings"]),
                    ("real_holding", self.references._holding_candidates,
                     options["include_real_holdings"]),
                ]
                for kind, reader, enabled in readers:
                    if not enabled:
                        continue
                    try:
                        candidates = await reader(
                            user_id, security, date_from, date_through
                        )
                        defaults.extend(item.to_reference() for item in candidates)
                        sources.append({
                            "kind": kind, "security_id": security_id,
                            "available": bool(candidates),
                            "reason": None if candidates else "no_source_in_period",
                        })
                    except Exception:
                        sources.append({
                            "kind": kind, "security_id": security_id,
                            "available": False, "reason": "source_unavailable",
                        })
                if options["include_paper_holdings"]:
                    try:
                        holdings = await self.references._paper_holdings(user_id)
                    except Exception:
                        holdings = []
                    candidates = [
                        item for item in holdings
                        if item.security_id == security_id
                        and (not date_from or item.source_date >= date_from)
                        and (not date_through or item.source_date <= date_through)
                    ]
                    defaults.extend(item.to_reference() for item in candidates)
                    sources.append({
                        "kind": "paper_holding", "security_id": security_id,
                        "available": bool(candidates),
                        "reason": None if candidates else "historical_snapshot_unavailable",
                    })
        selected = selected_references if selected_references is not None else defaults
        unique = {
            (ref.kind, ref.source_id, ref.account_type, ref.source_date): ref
            for ref in selected
        }
        resolved = []
        for reference in unique.values():
            try:
                document = (
                    await self.references.resolve(user_id, reference)
                ).to_document()
            except Exception:
                document = {
                    **reference.to_document(), "available": False, "snapshot": {}
                }
            document.pop("user_id", None)
            resolved.append(document)
        for kind in (
            "market", "funds", "global_markets", "limit_up_concepts", "financial_metrics"
        ):
            if options.get(f"include_{kind}"):
                sources.append({
                    "kind": kind, "available": False, "reason": "source_unavailable",
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_through": date_through.isoformat() if date_through else None,
                })
        theses = []
        recent = []
        for security_id in security_ids:
            if options["include_thesis"]:
                workspace = await self.repository.get_workspace(user_id, security_id)
                document = workspace.to_document() if workspace else None
                theses.append({key: document[key] for key in (
                    "security_id", "body", "assumptions", "risks",
                    "invalidation_conditions", "open_questions", "current_revision",
                )} if document else {"security_id": security_id, "available": False})
            if options["include_recent_entries"]:
                query = EntryQuery(security_id=security_id, page_size=200)
                page_number = 1
                while True:
                    page = await self.repository.list_entries(
                        user_id, replace(query, page=page_number)
                    )
                    for item in page.items:
                        if item.entry_type not in {"note", "research"} or item.id == entry.id:
                            continue
                        if (
                            item.updated_at.date() < (
                                date_from or utc_now().date() - timedelta(days=30)
                            )
                            or (date_through and item.updated_at.date() > date_through)
                        ):
                            continue
                        document = item.to_document()
                        recent.append({key: document[key] for key in (
                            "id", "entry_type", "security_id", "title", "body",
                            "conclusion", "updated_at",
                        )})
                    if page_number * page.page_size >= page.total:
                        break
                    page_number += 1
        target = entry.to_document()
        return {
            "target": {key: target[key] for key in (
                "entry_type", "scope", "security_id", "security_ids", "title", "body",
                "topic", "conclusion", "decision_action", "decision_date",
                "planned_price", "target_allocation", "horizon", "review_kind",
                "decision_id", "scope_metadata",
            )},
            "options": options, "theses": theses, "recent_entries": recent,
            "references": resolved, "sources": sources,
        }, list(unique.values())
