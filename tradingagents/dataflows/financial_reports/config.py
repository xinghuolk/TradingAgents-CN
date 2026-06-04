"""Configuration for FinancialReportClient integration."""

from __future__ import annotations

from dataclasses import dataclass
import os

_DEFAULT_EXTRACTOR_CONTAINER_ROOT = "/app/external/financial-report-llm-extractor"
_DEFAULT_PDF_CONTAINER_ROOT = "/app/external/financial-report-pdfs"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _container_root(env_name: str, default: str) -> str:
    return os.getenv(env_name, default).rstrip("/")


def _remap_host_path_for_docker(
    raw: str,
    *,
    host_root_env: str,
    container_root_env: str,
    default_container_root: str,
) -> str:
    """Map host paths from .env to their Docker bind mount path."""
    if not raw or not _env_bool("DOCKER_CONTAINER", False):
        return raw

    host_root = os.getenv(host_root_env, "").rstrip("/")
    if not host_root:
        return raw

    if raw != host_root and not raw.startswith(f"{host_root}/"):
        return raw

    container_root = _container_root(container_root_env, default_container_root)
    suffix = raw[len(host_root):].lstrip("/")
    return f"{container_root}/{suffix}" if suffix else container_root


def _remap_extractor_path_for_docker(raw: str) -> str:
    return _remap_host_path_for_docker(
        raw,
        host_root_env="FINANCIAL_REPORT_EXTRACTOR_HOST_ROOT",
        container_root_env="FINANCIAL_REPORT_EXTRACTOR_CONTAINER_ROOT",
        default_container_root=_DEFAULT_EXTRACTOR_CONTAINER_ROOT,
    )


def remap_pdf_path_for_docker(raw: str) -> str:
    """Public: map a host PDF path to its Docker container path.

    Imported by ``adapter.py`` to remap report-collector ``file_path`` values
    before existence checks, so it is part of this module's public surface.
    """
    remapped = _remap_host_path_for_docker(
        raw,
        host_root_env="FINANCIAL_REPORT_PDF_HOST_ROOT",
        container_root_env="FINANCIAL_REPORT_PDF_CONTAINER_ROOT",
        default_container_root=_DEFAULT_PDF_CONTAINER_ROOT,
    )
    if remapped != raw:
        return remapped
    return _remap_extractor_path_for_docker(raw)


def _default_pdf_root() -> str:
    raw = os.getenv("FINANCIAL_REPORT_PDF_ROOT", "")
    if raw:
        return remap_pdf_path_for_docker(raw)
    if _env_bool("DOCKER_CONTAINER", False):
        return _container_root("FINANCIAL_REPORT_PDF_CONTAINER_ROOT", _DEFAULT_PDF_CONTAINER_ROOT)
    return os.getenv("FINANCIAL_REPORT_PDF_HOST_ROOT", "")


def _default_extractor_cache_root() -> str:
    raw = os.getenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", "")
    if raw:
        return _remap_extractor_path_for_docker(raw)
    if _env_bool("DOCKER_CONTAINER", False):
        root = _container_root(
            "FINANCIAL_REPORT_EXTRACTOR_CONTAINER_ROOT",
            _DEFAULT_EXTRACTOR_CONTAINER_ROOT,
        )
        return f"{root}/tmp/.cache"
    host_root = os.getenv("FINANCIAL_REPORT_EXTRACTOR_HOST_ROOT", "").rstrip("/")
    return f"{host_root}/tmp/.cache" if host_root else ""


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


def get_financial_report_container_paths() -> tuple[str, str]:
    """返回容器内部固定的 (extractor_cache_root, pdf_root)。

    清理操作只针对容器内部的挂载点，与 host 端 env 路径配置（FINANCIAL_REPORT_*_ROOT /
    *_HOST_ROOT）无关，避免误删宿主机上任意配置目录或相对路径。可被
    FINANCIAL_REPORT_EXTRACTOR_CONTAINER_ROOT / FINANCIAL_REPORT_PDF_CONTAINER_ROOT 覆盖。
    """
    extractor_root = _container_root(
        "FINANCIAL_REPORT_EXTRACTOR_CONTAINER_ROOT",
        _DEFAULT_EXTRACTOR_CONTAINER_ROOT,
    )
    extractor_cache_root = f"{extractor_root}/tmp/.cache"
    pdf_root = _container_root(
        "FINANCIAL_REPORT_PDF_CONTAINER_ROOT",
        _DEFAULT_PDF_CONTAINER_ROOT,
    )
    return extractor_cache_root, pdf_root


def get_financial_report_client_config() -> FinancialReportClientConfig:
    return FinancialReportClientConfig(
        enabled=_env_bool("FINANCIAL_REPORT_CLIENT_ENABLED", False),
        cache_only=_env_bool("FINANCIAL_REPORT_CACHE_ONLY", True),
        force_refresh=_env_bool("FINANCIAL_REPORT_FORCE_REFRESH", False),
        include_llm_supplement=_env_bool("FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT", False),
        allow_llm_models=_split_csv(os.getenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "gpt-5.5,codex")),
        extractor_cache_root=_default_extractor_cache_root(),
        llm_config_path=_remap_extractor_path_for_docker(
            os.getenv("FINANCIAL_REPORT_LLM_CONFIG_PATH", "")
        ),
        pdf_root=_default_pdf_root(),
    )
