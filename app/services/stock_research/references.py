from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Mapping, Protocol

from bson import ObjectId

from app.services.real_portfolio.errors import PortfolioError
from app.services.real_portfolio.models import (
    Page,
    PortfolioView,
    SecurityId,
    TradeFilters,
    TradeItem,
)
from app.services.stock_research.errors import ResearchError
from app.services.stock_research.models import (
    AccountType,
    EntryQuery,
    Reference,
    ReferenceKind,
    ResearchSecurityId,
)
from app.services.stock_research.storage import StockResearchRepository


RECOMMENDATION_WINDOW_DAYS = 7
SOURCE_PAGE_SIZE = 200


def _source_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if type(value) is date:
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None


def _in_range(
    value: date | None, date_from: date | None, date_through: date | None
) -> bool:
    return value is not None and not (
        (date_from is not None and value < date_from)
        or (date_through is not None and value > date_through)
    )


def _public_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _report_market(document: Mapping[str, object], code: str) -> str:
    raw = str(document.get("market_type") or "")
    if raw in {"HK", "港股"}:
        return "HK"
    if raw in {"US", "美股"}:
        return "US"
    return "A" if code.isdigit() else "US"


@dataclass(frozen=True, slots=True)
class ReferenceCandidate:
    user_id: str
    security_id: str
    kind: ReferenceKind
    source_id: str
    account_type: AccountType | None
    source_date: date | None
    label: str
    available: bool = True
    snapshot: dict[str, object] = field(default_factory=dict)

    def to_reference(self) -> Reference:
        return Reference(
            kind=self.kind,
            source_id=self.source_id,
            account_type=self.account_type,
            source_date=self.source_date,
            label=self.label,
        )

    def to_document(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "security_id": self.security_id,
            "kind": self.kind,
            "source_id": self.source_id,
            "account_type": self.account_type,
            "source_date": self.source_date.isoformat() if self.source_date else None,
            "label": self.label,
            "available": self.available,
            "snapshot": {
                key: _public_value(value) for key, value in self.snapshot.items()
            },
        }


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    user_id: str
    kind: ReferenceKind
    source_id: str
    account_type: AccountType | None
    source_date: date | None
    label: str | None
    available: bool
    security_id: str | None = None
    snapshot: dict[str, object] = field(default_factory=dict)

    def to_document(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "kind": self.kind,
            "source_id": self.source_id,
            "account_type": self.account_type,
            "source_date": self.source_date.isoformat() if self.source_date else None,
            "label": self.label,
            "available": self.available,
            "security_id": self.security_id,
            "snapshot": {
                key: _public_value(value) for key, value in self.snapshot.items()
            },
        }


class RealPortfolioReader(Protocol):
    async def get_positions(
        self, *, user_id: str, as_of: date | None
    ) -> PortfolioView: ...

    async def list_trades(
        self,
        *,
        user_id: str,
        filters: TradeFilters,
        page: int,
        page_size: int,
    ) -> Page[TradeItem]: ...


class AnalysisReportAdapter:
    def __init__(self, db) -> None:
        self.collection = db["analysis_reports"]

    async def list_reports(
        self,
        user_id: str,
        security: ResearchSecurityId,
        date_from: date | None,
        date_through: date | None,
    ) -> list[Mapping[str, object]]:
        documents = await self.collection.find(
            {"user_id": user_id, "stock_symbol": security.code}
        ).to_list(length=None)
        return [
            document
            for document in documents
            if ResearchSecurityId.parse(
                _report_market(document, security.code), security.code
            )
            == security
            and _in_range(
                _source_date(
                    document.get("analysis_date") or document.get("created_at")
                ),
                date_from,
                date_through,
            )
        ]

    async def get_report(
        self, user_id: str, source_id: str
    ) -> Mapping[str, object] | None:
        identities: list[dict[str, object]] = [
            {"analysis_id": source_id},
            {"_id": source_id},
        ]
        if ObjectId.is_valid(source_id):
            identities.append({"_id": ObjectId(source_id)})
        return await self.collection.find_one(
            {"user_id": user_id, "$or": identities}
        )


class PaperTradeAdapter:
    def __init__(self, db) -> None:
        self.collection = db["paper_trades"]

    async def list_trades(
        self,
        user_id: str,
        security: ResearchSecurityId,
        date_from: date | None,
        date_through: date | None,
    ) -> list[Mapping[str, object]]:
        market = "CN" if security.market == "A" else security.market
        documents = await self.collection.find(
            {"user_id": user_id, "market": market, "code": security.code}
        ).to_list(length=None)
        return [
            document
            for document in documents
            if _in_range(
                _source_date(document.get("timestamp")), date_from, date_through
            )
        ]

    async def get_trade(
        self, user_id: str, source_id: str
    ) -> Mapping[str, object] | None:
        identities: list[dict[str, object]] = [{"_id": source_id}]
        if ObjectId.is_valid(source_id):
            identities.append({"_id": ObjectId(source_id)})
        return await self.collection.find_one(
            {"user_id": user_id, "$or": identities}
        )


class ReferenceService:
    def __init__(
        self,
        repository: StockResearchRepository,
        reports: AnalysisReportAdapter,
        real_portfolio: RealPortfolioReader,
        paper_trades: PaperTradeAdapter,
    ) -> None:
        self.repository = repository
        self.reports = reports
        self.real_portfolio = real_portfolio
        self.paper_trades = paper_trades

    async def list_candidates(
        self,
        user_id: str,
        security_id: str,
        date_from: date | None,
        date_through: date | None,
    ) -> list[ReferenceCandidate]:
        if (
            date_from is not None
            and date_through is not None
            and date_from > date_through
        ):
            raise ResearchError(
                "INVALID_QUERY", "date_from must not follow date_through"
            )
        security = ResearchSecurityId.from_string(security_id)
        candidates = [
            *await self._report_candidates(user_id, security, date_from, date_through),
            *await self._real_trade_candidates(
                user_id, security, date_from, date_through
            ),
            *await self._paper_trade_candidates(
                user_id, security, date_from, date_through
            ),
            *await self._holding_candidates(
                user_id, security, date_from, date_through
            ),
            *await self._decision_candidates(
                user_id, security, date_from, date_through
            ),
        ]
        return sorted(
            candidates,
            key=lambda item: (item.source_date or date.min, item.kind, item.source_id),
            reverse=True,
        )

    async def resolve(
        self, user_id: str, reference: Reference
    ) -> ResolvedReference:
        candidate = await self._resolve_candidate(user_id, reference)
        if candidate is None:
            return ResolvedReference(
                user_id=user_id,
                kind=reference.kind,
                source_id=reference.source_id,
                account_type=reference.account_type,
                source_date=reference.source_date,
                label=reference.label,
                available=False,
            )
        return ResolvedReference(
            user_id=user_id,
            kind=candidate.kind,
            source_id=candidate.source_id,
            account_type=candidate.account_type,
            source_date=candidate.source_date,
            label=candidate.label,
            available=True,
            security_id=candidate.security_id,
            snapshot=candidate.snapshot,
        )

    async def recommend_trade_links(
        self, user_id: str, decision_id: str
    ) -> list[ReferenceCandidate]:
        decision = await self.repository.get_entry(user_id, decision_id)
        if (
            decision is None
            or decision.entry_type != "decision"
            or decision.security_id is None
            or decision.decision_date is None
        ):
            raise ResearchError("RESEARCH_NOT_FOUND", "research decision not found")
        date_from = decision.decision_date - timedelta(days=RECOMMENDATION_WINDOW_DAYS)
        date_through = decision.decision_date + timedelta(
            days=RECOMMENDATION_WINDOW_DAYS
        )
        candidates = await self.list_candidates(
            user_id, decision.security_id, date_from, date_through
        )
        trades = [
            item
            for item in candidates
            if item.kind in {"real_trade", "paper_trade"}
        ]
        return sorted(
            trades,
            key=lambda item: (
                abs((item.source_date - decision.decision_date).days)
                if item.source_date is not None
                else RECOMMENDATION_WINDOW_DAYS + 1,
                item.source_date or date.max,
                item.kind,
                item.source_id,
            ),
        )

    async def _report_candidates(
        self,
        user_id: str,
        security: ResearchSecurityId,
        date_from: date | None,
        date_through: date | None,
    ) -> list[ReferenceCandidate]:
        documents = await self.reports.list_reports(
            user_id, security, date_from, date_through
        )
        return [
            self._report_candidate(user_id, security, document)
            for document in documents
        ]

    def _report_candidate(
        self,
        user_id: str,
        security: ResearchSecurityId,
        document: Mapping[str, object],
    ) -> ReferenceCandidate:
        source_id = str(document.get("analysis_id") or document.get("_id"))
        source_date = _source_date(
            document.get("analysis_date") or document.get("created_at")
        )
        name = str(document.get("stock_name") or security.code)
        return ReferenceCandidate(
            user_id=user_id,
            security_id=str(security),
            kind="analysis_report",
            source_id=source_id,
            account_type=None,
            source_date=source_date,
            label=f"{name} analysis report",
            snapshot={"summary": str(document.get("summary") or "")},
        )

    async def _real_trade_candidates(
        self,
        user_id: str,
        security: ResearchSecurityId,
        date_from: date | None,
        date_through: date | None,
    ) -> list[ReferenceCandidate]:
        real_security = self._real_security(security)
        if real_security is None:
            return []
        items = await self._list_real_trades(
            user_id,
            TradeFilters(
                date_from=date_from,
                date_through=date_through,
                security_id=real_security,
            ),
        )
        return [self._real_trade_candidate(user_id, security, item) for item in items]

    async def _list_real_trades(
        self, user_id: str, filters: TradeFilters
    ) -> list[TradeItem]:
        first = await self.real_portfolio.list_trades(
            user_id=user_id, filters=filters, page=1, page_size=SOURCE_PAGE_SIZE
        )
        items = list(first.items)
        pages = (first.total + SOURCE_PAGE_SIZE - 1) // SOURCE_PAGE_SIZE
        for page in range(2, pages + 1):
            result = await self.real_portfolio.list_trades(
                user_id=user_id,
                filters=filters,
                page=page,
                page_size=SOURCE_PAGE_SIZE,
            )
            items.extend(result.items)
        return items

    def _real_trade_candidate(
        self, user_id: str, security: ResearchSecurityId, item: TradeItem
    ) -> ReferenceCandidate:
        return ReferenceCandidate(
            user_id=user_id,
            security_id=str(security),
            kind="real_trade",
            source_id=item.id,
            account_type="real",
            source_date=item.trade_date,
            label=f"Real {item.operation_label}",
            snapshot={
                "operation": item.operation_label,
                "quantity": item.security_quantity,
                "currency": item.trade_currency,
            },
        )

    async def _paper_trade_candidates(
        self,
        user_id: str,
        security: ResearchSecurityId,
        date_from: date | None,
        date_through: date | None,
    ) -> list[ReferenceCandidate]:
        documents = await self.paper_trades.list_trades(
            user_id, security, date_from, date_through
        )
        return [
            self._paper_trade_candidate(user_id, security, item)
            for item in documents
        ]

    def _paper_trade_candidate(
        self,
        user_id: str,
        security: ResearchSecurityId,
        document: Mapping[str, object],
    ) -> ReferenceCandidate:
        side = str(document.get("side") or "trade")
        return ReferenceCandidate(
            user_id=user_id,
            security_id=str(security),
            kind="paper_trade",
            source_id=str(document["_id"]),
            account_type="paper",
            source_date=_source_date(document.get("timestamp")),
            label=f"Paper {side}",
            snapshot={
                "side": side,
                "quantity": document.get("quantity"),
                "price": document.get("price"),
            },
        )

    async def _holding_candidates(
        self,
        user_id: str,
        security: ResearchSecurityId,
        date_from: date | None,
        date_through: date | None,
    ) -> list[ReferenceCandidate]:
        real_security = self._real_security(security)
        if real_security is None:
            return []
        requested_dates = tuple(
            dict.fromkeys(
                value
                for value in (date_from, date_through)
                if value is not None
            )
        )
        try:
            if not requested_dates:
                views = (
                    await self.real_portfolio.get_positions(
                        user_id=user_id, as_of=None
                    ),
                )
            else:
                views = tuple(
                    [
                        await self.real_portfolio.get_positions(
                            user_id=user_id, as_of=value
                        )
                        for value in requested_dates
                    ]
                )
        except PortfolioError as error:
            if error.code == "NO_FULL_SNAPSHOT":
                return []
            raise
        candidates = []
        for view in views:
            holding = next(
                (item for item in view.holdings if item.security == real_security), None
            )
            if holding is None:
                continue
            candidates.append(
                ReferenceCandidate(
                    user_id=user_id,
                    security_id=str(security),
                    kind="holding_date",
                    source_id=f"real:{security}@{view.as_of.isoformat()}",
                    account_type="real",
                    source_date=view.as_of,
                    label=f"Real holding on {view.as_of.isoformat()}",
                    snapshot={
                        "quantity": holding.quantity,
                        "market_value": holding.market_value,
                        "completeness": view.completeness,
                    },
                )
            )
        return candidates

    async def _decision_candidates(
        self,
        user_id: str,
        security: ResearchSecurityId,
        date_from: date | None,
        date_through: date | None,
    ) -> list[ReferenceCandidate]:
        query = EntryQuery(
            security_id=str(security),
            entry_type="decision",
            page=1,
            page_size=SOURCE_PAGE_SIZE,
        )
        page = await self.repository.list_entries(user_id, query)
        entries = list(page.items)
        page_number = 2
        while len(entries) < page.total:
            next_page = await self.repository.list_entries(
                user_id, replace(query, page=page_number)
            )
            if not next_page.items:
                break
            entries.extend(next_page.items)
            page_number += 1
        return [
            ReferenceCandidate(
                user_id=user_id,
                security_id=str(security),
                kind="decision",
                source_id=entry.id,
                account_type=None,
                source_date=entry.decision_date,
                label=entry.title or f"Decision {entry.decision_action or ''}".strip(),
                snapshot={
                    "action": entry.decision_action,
                    "status": entry.status,
                },
            )
            for entry in entries
            if _in_range(entry.decision_date, date_from, date_through)
        ]

    async def _resolve_candidate(
        self, user_id: str, reference: Reference
    ) -> ReferenceCandidate | None:
        expected_account = {
            "real_trade": "real",
            "paper_trade": "paper",
            "holding_date": "real",
        }.get(reference.kind)
        if expected_account is not None and reference.account_type != expected_account:
            return None
        if reference.kind == "analysis_report":
            document = await self.reports.get_report(user_id, reference.source_id)
            if document is None:
                return None
            code = str(document.get("stock_symbol") or "")
            security = ResearchSecurityId.parse(
                _report_market(document, code), code
            )
            return self._report_candidate(user_id, security, document)
        if reference.kind == "paper_trade":
            document = await self.paper_trades.get_trade(user_id, reference.source_id)
            if document is None:
                return None
            security = ResearchSecurityId.parse(
                str(document.get("market") or "CN"), str(document.get("code") or "")
            )
            return self._paper_trade_candidate(user_id, security, document)
        if reference.kind == "real_trade":
            filters = TradeFilters(
                date_from=reference.source_date,
                date_through=reference.source_date,
            )
            items = await self._list_real_trades(user_id, filters)
            item = next(
                (item for item in items if item.id == reference.source_id), None
            )
            if item is None or item.security is None:
                return None
            security = ResearchSecurityId.parse(
                item.security.market, item.security.code
            )
            return self._real_trade_candidate(user_id, security, item)
        if reference.kind == "decision":
            entry = await self.repository.get_entry(user_id, reference.source_id)
            if (
                entry is None
                or entry.entry_type != "decision"
                or entry.security_id is None
            ):
                return None
            return ReferenceCandidate(
                user_id=user_id,
                security_id=entry.security_id,
                kind="decision",
                source_id=entry.id,
                account_type=None,
                source_date=entry.decision_date,
                label=entry.title or f"Decision {entry.decision_action or ''}".strip(),
                snapshot={"action": entry.decision_action, "status": entry.status},
            )
        if reference.kind == "holding_date":
            return await self._resolve_holding(user_id, reference)
        return None

    async def _resolve_holding(
        self, user_id: str, reference: Reference
    ) -> ReferenceCandidate | None:
        prefix = "real:"
        if not reference.source_id.startswith(prefix) or reference.source_date is None:
            return None
        security_id = reference.source_id[len(prefix) :].rsplit("@", maxsplit=1)[0]
        security = ResearchSecurityId.from_string(security_id)
        items = await self._holding_candidates(
            user_id, security, reference.source_date, reference.source_date
        )
        return next(
            (item for item in items if item.source_id == reference.source_id), None
        )

    @staticmethod
    def _real_security(security: ResearchSecurityId) -> SecurityId | None:
        if security.market not in {"A", "HK"}:
            return None
        return SecurityId(security.market, security.code)
