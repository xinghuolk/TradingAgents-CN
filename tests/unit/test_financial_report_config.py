from tradingagents.dataflows.financial_reports.config import (
    FinancialReportClientConfig,
    get_financial_report_client_config,
)
from tradingagents.default_config import DEFAULT_CONFIG


def test_default_config_disables_financial_report_client():
    assert DEFAULT_CONFIG["financial_report_client_enabled"] is False
    assert DEFAULT_CONFIG["financial_report_cache_only"] is True
    assert DEFAULT_CONFIG["financial_report_force_refresh"] is False
    assert DEFAULT_CONFIG["financial_report_include_llm_supplement"] is False
    assert DEFAULT_CONFIG["financial_report_allow_llm_models"] == "gpt-5.5,codex"
    assert DEFAULT_CONFIG["financial_report_extractor_cache_root"] == ""
    assert DEFAULT_CONFIG["financial_report_llm_config_path"] == ""
    assert DEFAULT_CONFIG["financial_report_pdf_root"] == ""


def test_env_config_parses_booleans_and_paths(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "true")
    monkeypatch.setenv("FINANCIAL_REPORT_CACHE_ONLY", "false")
    monkeypatch.setenv("FINANCIAL_REPORT_FORCE_REFRESH", "true")
    monkeypatch.setenv("FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT", "true")
    monkeypatch.setenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "gpt-5.5,codex,gpt-4.1")
    monkeypatch.setenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", "/tmp/fr-cache")
    monkeypatch.setenv("FINANCIAL_REPORT_LLM_CONFIG_PATH", "/tmp/extractor-llm.json")
    monkeypatch.setenv("FINANCIAL_REPORT_PDF_ROOT", "/tmp/reports")

    config = get_financial_report_client_config()

    assert config == FinancialReportClientConfig(
        enabled=True,
        cache_only=False,
        force_refresh=True,
        include_llm_supplement=True,
        allow_llm_models=("gpt-5.5", "codex", "gpt-4.1"),
        extractor_cache_root="/tmp/fr-cache",
        llm_config_path="/tmp/extractor-llm.json",
        pdf_root="/tmp/reports",
    )


def test_env_config_ignores_empty_llm_model_entries(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", " codex, , gpt-5.5 ,,")

    config = get_financial_report_client_config()

    assert config.allow_llm_models == ("codex", "gpt-5.5")


def test_env_config_remaps_extractor_host_paths_inside_docker(monkeypatch):
    monkeypatch.setenv("DOCKER_CONTAINER", "true")
    monkeypatch.setenv(
        "FINANCIAL_REPORT_EXTRACTOR_HOST_ROOT",
        "/home/like/git/financial-report-llm-extractor",
    )
    monkeypatch.setenv(
        "FINANCIAL_REPORT_EXTRACTOR_CONTAINER_ROOT",
        "/app/external/financial-report-llm-extractor",
    )
    monkeypatch.setenv(
        "FINANCIAL_REPORT_PDF_ROOT",
        "/home/like/git/financial-report-llm-extractor/downloads",
    )
    monkeypatch.setenv(
        "FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT",
        "/home/like/git/financial-report-llm-extractor/tmp/.cache",
    )
    monkeypatch.setenv(
        "FINANCIAL_REPORT_LLM_CONFIG_PATH",
        "/home/like/git/financial-report-llm-extractor/tmp/runs/quick_validation/00001_2025_en/llm_config_deepseek.json",
    )

    config = get_financial_report_client_config()

    assert config.pdf_root == "/app/external/financial-report-llm-extractor/downloads"
    assert config.extractor_cache_root == "/app/external/financial-report-llm-extractor/tmp/.cache"
    assert config.llm_config_path == (
        "/app/external/financial-report-llm-extractor/tmp/runs/quick_validation/"
        "00001_2025_en/llm_config_deepseek.json"
    )
