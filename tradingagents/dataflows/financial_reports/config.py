"""Configuration for FinancialReportClient integration."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _remap_extractor_path_for_docker(raw: str) -> str:
    """Map host extractor paths from .env to the Docker bind mount path."""
    if not raw or not _env_bool("DOCKER_CONTAINER", False):
        return raw

    host_root = os.getenv("FINANCIAL_REPORT_EXTRACTOR_HOST_ROOT", "").rstrip("/")
    if not host_root:
        return raw

    if raw != host_root and not raw.startswith(f"{host_root}/"):
        return raw

    container_root = os.getenv(
        "FINANCIAL_REPORT_EXTRACTOR_CONTAINER_ROOT",
        "/app/external/financial-report-llm-extractor",
    ).rstrip("/")
    suffix = raw[len(host_root):].lstrip("/")
    return f"{container_root}/{suffix}" if suffix else container_root


@dataclass(frozen=True)
class FinancialReportClientConfig:
    enabled: bool
    cache_only: bool
    force_refresh: bool
    include_llm_supplement: bool
    allow_llm_models: tuple[str, ...]
    extractor_cache_root: str
    llm_config_path: str
    pdf_root: str


def get_financial_report_client_config() -> FinancialReportClientConfig:
    return FinancialReportClientConfig(
        enabled=_env_bool("FINANCIAL_REPORT_CLIENT_ENABLED", False),
        cache_only=_env_bool("FINANCIAL_REPORT_CACHE_ONLY", True),
        force_refresh=_env_bool("FINANCIAL_REPORT_FORCE_REFRESH", False),
        include_llm_supplement=_env_bool("FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT", False),
        allow_llm_models=_split_csv(os.getenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "gpt-5.5,codex")),
        extractor_cache_root=_remap_extractor_path_for_docker(
            os.getenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", "")
        ),
        llm_config_path=_remap_extractor_path_for_docker(
            os.getenv("FINANCIAL_REPORT_LLM_CONFIG_PATH", "")
        ),
        pdf_root=_remap_extractor_path_for_docker(
            os.getenv("FINANCIAL_REPORT_PDF_ROOT", "")
        ),
    )
