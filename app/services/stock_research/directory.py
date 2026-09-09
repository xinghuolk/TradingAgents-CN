from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

from app.services.real_portfolio.errors import PortfolioError
from app.services.stock_research.models import (
    ENTRY_TYPES,
    ResearchSecurityId,
    WorkspaceDirectoryFacts,
)


class RealPortfolioReader(Protocol):
    async def get_positions(self, *, user_id: str, as_of): ...


class WorkspaceDirectorySource(Protocol):
    async def get_workspace_facts(
        self, user_id: str, security_ids: tuple[str, ...]
    ) -> Mapping[str, WorkspaceDirectoryFacts]: ...


class EmptyWorkspaceDirectorySource:
    async def get_workspace_facts(
        self, user_id: str, security_ids: tuple[str, ...]
    ) -> Mapping[str, WorkspaceDirectoryFacts]:
        return {security_id: WorkspaceDirectoryFacts() for security_id in security_ids}


def _source_security_id(market: object, code: object) -> str | None:
    market_alias = str(market or "CN").strip().upper()
    market_alias = {
        "A股": "CN",
        "A-SHARE": "CN",
        "港股": "HK",
        "美股": "US",
    }.get(market_alias, market_alias)
    try:
        return str(ResearchSecurityId.parse(market_alias, str(code or "")))
    except Exception:
        return None


def _has_quantity(value: object) -> bool:
    try:
        return Decimal(str(value)) > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class ResearchDirectorySourceAdapter:
    def __init__(self, db, real_portfolio: RealPortfolioReader) -> None:
        self.db = db
        self.real_portfolio = real_portfolio

    async def get_workspace_facts(
        self, user_id: str, security_ids: tuple[str, ...]
    ) -> Mapping[str, WorkspaceDirectoryFacts]:
        requested = set(security_ids)
        mutable = {
            security_id: {
                "latest_entry_type": None,
                "latest_entry_at": None,
                "has_real_holding": False,
                "has_paper_holding": False,
                "watchlisted": False,
            }
            for security_id in security_ids
        }
        if not requested:
            return {}

        entry_documents = (
            await self.db["stock_research_entries"]
            .find(
                {
                    "user_id": user_id,
                    "security_ids": {"$in": list(requested)},
                    "deleted_at": None,
                }
            )
            .sort([("updated_at", -1), ("id", 1)])
            .to_list(length=None)
        )
        for document in entry_documents:
            entry_type = document.get("entry_type")
            entry_at = _datetime(document.get("updated_at"))
            if entry_type not in ENTRY_TYPES or entry_at is None:
                continue
            for security_id in document.get("security_ids", []):
                if (
                    security_id in mutable
                    and mutable[security_id]["latest_entry_at"] is None
                ):
                    mutable[security_id]["latest_entry_type"] = entry_type
                    mutable[security_id]["latest_entry_at"] = entry_at

        try:
            real_positions = await self.real_portfolio.get_positions(
                user_id=user_id, as_of=None
            )
            real_holdings = real_positions.holdings
        except PortfolioError as error:
            if error.code != "NO_FULL_SNAPSHOT":
                raise
            real_holdings = ()
        for holding in real_holdings:
            security_id = _source_security_id(
                holding.security.market, holding.security.code
            )
            if security_id in mutable and _has_quantity(holding.quantity):
                mutable[security_id]["has_real_holding"] = True

        paper_positions = await self.db["paper_positions"].find(
            {"user_id": user_id}
        ).to_list(length=None)
        for position in paper_positions:
            security_id = _source_security_id(
                position.get("market"), position.get("code")
            )
            if security_id in mutable and _has_quantity(position.get("quantity")):
                mutable[security_id]["has_paper_holding"] = True

        favorites_document = await self.db["user_favorites"].find_one(
            {"user_id": user_id}
        )
        for favorite in (favorites_document or {}).get("favorites", []):
            security_id = _source_security_id(
                favorite.get("market"), favorite.get("stock_code")
            )
            if security_id in mutable:
                mutable[security_id]["watchlisted"] = True

        return {
            security_id: WorkspaceDirectoryFacts(
                latest_entry_type=facts["latest_entry_type"],  # type: ignore[arg-type]
                latest_entry_at=facts["latest_entry_at"],  # type: ignore[arg-type]
                has_real_holding=bool(facts["has_real_holding"]),
                has_paper_holding=bool(facts["has_paper_holding"]),
                watchlisted=bool(facts["watchlisted"]),
            )
            for security_id, facts in mutable.items()
        }
