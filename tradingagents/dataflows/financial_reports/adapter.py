"""Adapter around financial-report-llm-extractor public client API."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import FinancialReportClientConfig, remap_pdf_path_for_docker
from .llm_config_export import materialize_extractor_llm_config


@dataclass(frozen=True)
class FinancialReportAdapterResult:
    available: bool
    company: str
    market: str
    period_end: str
    extraction: Any | None
    warnings: list[str]
    errors: list[str]


def infer_annual_period_end(reference_date: str | None) -> str:
    if reference_date:
        ref = datetime.strptime(reference_date[:10], "%Y-%m-%d").date()
    else:
        ref = date.today()
    report_year = ref.year - 1 if ref.month >= 5 else ref.year - 2
    return f"{report_year}-12-31"


def _load_extractor_client() -> tuple[Any, Any, Any, Any] | None:
    try:
        from financial_report_llm_extractor.client import (  # type: ignore[import-not-found]
            ExtractorConfig,
            ExtractorError,
            FinancialReportClient,
            RefreshPolicy,
        )
    except ImportError:
        return None
    return ExtractorConfig, ExtractorError, FinancialReportClient, RefreshPolicy


def _path_from_pdf_info(pdf_info: dict[str, Any] | None) -> Path | None:
    if not isinstance(pdf_info, dict):
        return None
    for key in ("file_path", "path", "pdf_path", "local_path"):
        raw = pdf_info.get(key)
        if raw:
            raw_path = str(raw)
            candidates = (remap_pdf_path_for_docker(raw_path), raw_path)
            for candidate in dict.fromkeys(candidates):
                path = Path(candidate)
                if path.exists():
                    return path
    return None


def _optional_path(raw: str) -> Path | None:
    return Path(raw) if raw else None


def _extractor_market_dir(market: str) -> str:
    normalized = str(market or "").strip().upper()
    if normalized in {"HK", "HKG"}:
        return "hk_stocks"
    if normalized in {"CN", "A", "CHINA"}:
        return "cn_stocks"
    return f"{normalized.lower()}_stocks"


def _annual_pdf_names(market: str, period_end: str) -> tuple[str, ...]:
    year = str(period_end)[:4]
    normalized = str(market or "").strip().upper()
    if normalized in {"HK", "HKG"}:
        return (f"{year}_annual_en.pdf", f"{year}_annual_zh.pdf")
    if normalized in {"CN", "A", "CHINA"}:
        return (
            f"{year}_年度报告.pdf",
            f"{year}_annual_zh.pdf",
            f"{year}_annual_en.pdf",
        )
    return (f"{year}_annual_en.pdf", f"{year}_annual_zh.pdf", f"{year}.pdf")


def _pdf_company_candidates(market: str, company: str) -> tuple[str, ...]:
    raw = str(company or "").strip()
    candidates = [raw] if raw else []

    normalized_market = str(market or "").strip().upper()
    if normalized_market in {"HK", "HKG"} and raw:
        without_suffix = raw[:-3] if raw.upper().endswith(".HK") else raw
        if without_suffix and without_suffix != raw:
            candidates.append(without_suffix)

        digits = "".join(ch for ch in without_suffix if ch.isdigit())
        if digits and len(digits) <= 5:
            candidates.append(digits.zfill(5))
            candidates.append(digits)

    return tuple(dict.fromkeys(candidates))


def _pdf_candidates(root: Path, query: Any) -> tuple[Path, ...]:
    market = str(query.market)
    company_candidates = _pdf_company_candidates(market, str(query.company))
    period_end = str(query.period_end)
    candidates: list[Path] = []
    for company in company_candidates:
        candidates.extend(
            (
                root / market.lower() / company / f"{period_end}.pdf",
                root / market.upper() / company / f"{period_end}.pdf",
                root / company / f"{period_end}.pdf",
            )
        )

    annual_dirs: list[Path] = []
    for company in company_candidates:
        annual_dirs.extend(
            (
                root / _extractor_market_dir(market) / company / "annual",
                root / company / "annual",
            )
        )
    annual_dirs.extend((root / "annual", root))

    for annual_dir in dict.fromkeys(annual_dirs):
        for filename in _annual_pdf_names(market, period_end):
            candidates.append(annual_dir / filename)
    return tuple(dict.fromkeys(candidates))


def resolve_injected_codex_token(deep_config: dict | None = None) -> str | None:
    """Per-request codex OAuth token to hand the extractor.

    analysis_service injects the user's OAuth token into the analysis config as
    ``deep_api_key`` for subscription providers. Return it ONLY when the bridged
    deep provider is codex, so non-codex runs return None. When ``deep_config`` is
    not supplied, read the process-global analysis config (``Toolkit._config``) so
    every adapter call site (fundamentals / value-investment / turtle) is covered
    uniformly. The token travels as a call argument — never written to disk or env.
    """
    if os.getenv("TRADINGAGENTS_DEEP_PROVIDER", "").strip().lower() != "codex":
        return None
    if deep_config is None:
        try:
            from tradingagents.agents.utils.agent_utils import Toolkit

            deep_config = Toolkit._config
        except Exception:
            return None
    token = (deep_config or {}).get("deep_api_key")
    return token or None


class FinancialReportAdapter:
    def __init__(
        self,
        config: FinancialReportClientConfig,
        report_collector: Any | None = None,
        subscription_token: str | None = None,
    ) -> None:
        self.config = config
        self.report_collector = report_collector
        self.subscription_token = subscription_token

    def resolve_pdf(self, query: Any) -> Path | None:
        if self.config.pdf_root:
            root = Path(self.config.pdf_root)
            for candidate in _pdf_candidates(root, query):
                if candidate.exists():
                    return candidate

        if self.report_collector is not None:
            pdf_info = self.report_collector.fetch_latest_pdf_info(
                stock_code=str(query.company),
                market=str(query.market),
                report_types=("annual",),
            )
            return _path_from_pdf_info(pdf_info)
        return None

    def _refresh_policy(self, refresh_policy_module: Any) -> Any:
        if self.config.force_refresh:
            return refresh_policy_module.FORCE_REFRESH
        if self.config.cache_only:
            return refresh_policy_module.CACHE_ONLY
        return refresh_policy_module.CACHE_FIRST

    def _result_from_extraction(
        self,
        *,
        extraction: Any,
        ticker: str,
        market: str,
        period_end: str,
        warnings: list[str] | None = None,
    ) -> FinancialReportAdapterResult:
        result_warnings = list(warnings or [])
        staleness = getattr(extraction, "staleness", None)
        if bool(getattr(staleness, "is_missing", False)):
            result_warnings.append("annual-report extraction missing")
        if bool(getattr(staleness, "is_stale", False)):
            result_warnings.append("annual-report extraction stale")
        return FinancialReportAdapterResult(
            available=not bool(getattr(staleness, "is_missing", False)),
            company=ticker,
            market=market,
            period_end=period_end,
            extraction=extraction,
            warnings=result_warnings,
            errors=[],
        )

    def get_annual_report_data(
        self,
        *,
        ticker: str,
        market: str,
        period_end: str | None,
        reference_date: str | None = None,
    ) -> FinancialReportAdapterResult:
        resolved_period_end = period_end or infer_annual_period_end(reference_date)
        if not self.config.enabled:
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=["FinancialReportClient disabled"],
                errors=[],
            )

        loaded = _load_extractor_client()
        if loaded is None:
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=["financial-report-llm-extractor is not installed"],
                errors=[],
            )

        ExtractorConfig, ExtractorError, FinancialReportClient, RefreshPolicy = loaded
        try:
            include_llm = bool(self.config.include_llm_supplement and self.config.llm_config_path)
            extractor_config = ExtractorConfig(
                llm_config_path=_optional_path(self.config.llm_config_path),
                cache_root=_optional_path(self.config.extractor_cache_root),
                pdf_resolver=self.resolve_pdf if include_llm else None,
                subscription_token=self.subscription_token,
            )
            client = FinancialReportClient(config=extractor_config)
            extraction = client.get_extraction(
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                include_llm_supplement=include_llm,
                refresh_policy=self._refresh_policy(RefreshPolicy),
            )
            return self._result_from_extraction(
                extraction=extraction,
                ticker=ticker,
                market=market,
                period_end=resolved_period_end,
            )
        except ExtractorError as exc:
            reason = getattr(exc, "reason", "extractor_error")
            if include_llm:
                try:
                    extraction = client.get_extraction(
                        company=ticker,
                        market=market,
                        period_end=resolved_period_end,
                        include_llm_supplement=False,
                        refresh_policy=self._refresh_policy(RefreshPolicy),
                    )
                    return self._result_from_extraction(
                        extraction=extraction,
                        ticker=ticker,
                        market=market,
                        period_end=resolved_period_end,
                        warnings=[
                            "llm_supplement_failed; retried provider-only: "
                            f"{reason}: {exc}"
                        ],
                    )
                except Exception:
                    pass
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=[],
                errors=[f"{reason}: {exc}"],
            )
        except Exception as exc:
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=[],
                errors=[f"unexpected_error: {exc}"],
            )


def create_financial_report_adapter(
    config: FinancialReportClientConfig,
    subscription_token: str | None = None,
) -> FinancialReportAdapter:
    """Create adapter with report-collector wired only as a PDF provider.

    When the LLM supplement is requested but no explicit FINANCIAL_REPORT_LLM_CONFIG_PATH
    was provided, materialize a transport-config JSON from TradingAgents-CN's bridged
    deep-role LLM env vars (provider/model/backend_url). The extractor is unchanged.

    subscription_token: per-request codex OAuth token (caller-resolved); forwarded to
    the extractor so codex subscriptions work without a local ~/.codex login.
    """
    if subscription_token is None:
        subscription_token = resolve_injected_codex_token()

    if config.enabled and config.include_llm_supplement and not config.llm_config_path:
        generated = materialize_extractor_llm_config(cache_root=config.extractor_cache_root)
        if generated:
            config = replace(config, llm_config_path=generated)

    if not config.enabled or not (config.include_llm_supplement and config.llm_config_path):
        return FinancialReportAdapter(config=config, subscription_token=subscription_token)

    report_collector = None
    try:
        from tradingagents.services.report_collector_client import ReportCollectorClient
        from tradingagents.services.report_collector_config import get_report_collector_config

        rc_config = get_report_collector_config()
        if rc_config.get("enabled"):
            client = ReportCollectorClient(
                base_url=rc_config["url"],
                port=rc_config["port"],
                timeout=rc_config["timeout"],
            )
            report_collector = client if client.is_available() else None
    except Exception:
        report_collector = None
    return FinancialReportAdapter(
        config=config,
        report_collector=report_collector,
        subscription_token=subscription_token,
    )
