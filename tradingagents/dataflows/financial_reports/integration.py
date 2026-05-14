"""Lightweight integration helpers for TradingAgents tools."""

from __future__ import annotations

from typing import Any

from .adapter import create_financial_report_adapter
from .config import get_financial_report_client_config
from .mapper import merge_financial_report_data
from .policy import FinancialReportPolicy


def _metadata_only(
    financial_data: dict[str, Any],
    *,
    company: str,
    market: str,
    period_end: str,
    catalog_version: str | None,
    caveats: list[str],
) -> dict[str, Any]:
    updated = dict(financial_data)
    updated["_financial_report_client"] = {
        "company": company,
        "market": market,
        "period_end": period_end,
        "catalog_version": catalog_version,
        "caveats": caveats,
    }
    return updated


def apply_financial_report_client_data(
    *,
    financial_data: dict[str, Any],
    ticker: str,
    market: str,
    period_end: str | None = None,
) -> dict[str, Any]:
    """Merge authoritative annual-report fields into financial_data when enabled."""
    frc_config = get_financial_report_client_config()
    if not frc_config.enabled:
        return financial_data

    adapter = create_financial_report_adapter(frc_config)
    normalized_market = "CN" if market == "A" else market
    result = adapter.get_annual_report_data(
        ticker=ticker,
        market=normalized_market,
        period_end=period_end,
    )
    if result.extraction is None:
        return _metadata_only(
            financial_data,
            company=result.company,
            market=result.market,
            period_end=result.period_end,
            catalog_version=None,
            caveats=result.warnings + result.errors,
        )

    staleness = getattr(result.extraction, "staleness", None)
    if not result.available or bool(getattr(staleness, "is_missing", False)):
        return _metadata_only(
            financial_data,
            company=result.company,
            market=result.market,
            period_end=result.period_end,
            catalog_version=getattr(result.extraction, "catalog_version", None),
            caveats=result.warnings + result.errors + ["missing extraction is display-only by policy"],
        )

    if bool(getattr(staleness, "is_stale", False)):
        return _metadata_only(
            financial_data,
            company=result.company,
            market=result.market,
            period_end=result.period_end,
            catalog_version=getattr(result.extraction, "catalog_version", None),
            caveats=result.warnings + result.errors + ["stale extraction is display-only by policy"],
        )

    policy = FinancialReportPolicy(allow_llm_models=frc_config.allow_llm_models)
    merge_result = merge_financial_report_data(
        financial_data=financial_data,
        extraction=result.extraction,
        policy=policy,
    )
    if result.warnings or result.errors:
        meta = merge_result.financial_data.setdefault("_financial_report_client", {})
        caveats = list(meta.get("caveats") or [])
        caveats.extend(result.warnings)
        caveats.extend(result.errors)
        meta["caveats"] = caveats
    return merge_result.financial_data
