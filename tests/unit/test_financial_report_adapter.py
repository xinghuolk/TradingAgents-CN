from dataclasses import dataclass
from pathlib import Path
import sys
import types

from tradingagents.dataflows.financial_reports.adapter import (
    FinancialReportAdapter,
    FinancialReportAdapterResult,
    infer_annual_period_end,
)
from tradingagents.dataflows.financial_reports.config import FinancialReportClientConfig


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False
    value: str = "fresh"


@dataclass(frozen=True)
class FakeExtraction:
    company: str
    market: str
    period_end: str
    staleness: FakeStaleness
    fields: dict
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None


class FakeRefreshPolicy:
    CACHE_ONLY = "cache_only"
    CACHE_FIRST = "cache_first"
    FORCE_REFRESH = "force_refresh"


class FakeExtractorConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakePdfQuery:
    def __init__(self, company, market, period_end):
        self.company = company
        self.market = market
        self.period_end = period_end


class FakeClient:
    calls = []
    last_config = None

    def __init__(self, config):
        self.config = config
        FakeClient.last_config = config

    def get_extraction(self, **kwargs):
        self.calls.append(kwargs)
        return FakeExtraction(
            company=kwargs["company"],
            market=kwargs["market"],
            period_end=kwargs["period_end"],
            staleness=FakeStaleness(),
            fields={},
        )


class FakeExtractorError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def install_fake_extractor(monkeypatch):
    module = types.ModuleType("financial_report_llm_extractor.client")
    module.ExtractorConfig = FakeExtractorConfig
    module.FinancialReportClient = FakeClient
    module.PdfQuery = FakePdfQuery
    module.RefreshPolicy = FakeRefreshPolicy
    module.ExtractorError = FakeExtractorError
    package = types.ModuleType("financial_report_llm_extractor")
    monkeypatch.setitem(sys.modules, "financial_report_llm_extractor", package)
    monkeypatch.setitem(sys.modules, "financial_report_llm_extractor.client", module)


def test_infer_annual_period_end_uses_conservative_report_calendar():
    assert infer_annual_period_end("2026-05-14") == "2025-12-31"
    assert infer_annual_period_end("2026-03-01") == "2024-12-31"
    assert infer_annual_period_end(None).endswith("-12-31")


def test_adapter_returns_disabled_when_config_disabled():
    adapter = FinancialReportAdapter(FinancialReportClientConfig(
        enabled=False,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=False,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ))

    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert result == FinancialReportAdapterResult(
        available=False,
        company="600519",
        market="CN",
        period_end="2024-12-31",
        extraction=None,
        warnings=["FinancialReportClient disabled"],
        errors=[],
    )


def test_create_adapter_does_not_probe_report_collector_when_disabled(monkeypatch):
    import tradingagents.dataflows.financial_reports.adapter as adapter_module

    def fail_get_config():
        raise AssertionError("report collector should not be configured")

    monkeypatch.setattr(adapter_module, "get_report_collector_config", fail_get_config, raising=False)

    adapter = adapter_module.create_financial_report_adapter(FinancialReportClientConfig(
        enabled=False,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=False,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ))

    assert isinstance(adapter, FinancialReportAdapter)
    assert adapter.report_collector is None


def test_create_adapter_does_not_probe_report_collector_for_provider_only(monkeypatch):
    import tradingagents.dataflows.financial_reports.adapter as adapter_module

    def fail_get_config():
        raise AssertionError("report collector should not be configured")

    monkeypatch.setattr(adapter_module, "get_report_collector_config", fail_get_config, raising=False)

    adapter = adapter_module.create_financial_report_adapter(FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=False,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ))

    assert isinstance(adapter, FinancialReportAdapter)
    assert adapter.report_collector is None


def test_adapter_degrades_when_extractor_not_installed(monkeypatch):
    import tradingagents.dataflows.financial_reports.adapter as adapter_module

    monkeypatch.setattr(adapter_module, "_load_extractor_client", lambda: None)
    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=False,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ))

    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert result.available is False
    assert "financial-report-llm-extractor is not installed" in result.warnings[0]


def test_adapter_calls_extractor_with_cache_only(monkeypatch):
    install_fake_extractor(monkeypatch)
    FakeClient.calls.clear()
    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=True,
        allow_llm_models=("codex",),
        extractor_cache_root="/tmp/cache",
        llm_config_path="/tmp/llm.json",
        pdf_root="",
    ))

    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert result.available is True
    assert result.extraction.company == "600519"
    assert FakeClient.calls[0]["refresh_policy"] == "cache_only"
    assert FakeClient.calls[0]["include_llm_supplement"] is True
    assert isinstance(FakeClient.last_config.kwargs["cache_root"], Path)
    assert isinstance(FakeClient.last_config.kwargs["llm_config_path"], Path)
    assert callable(FakeClient.last_config.kwargs["pdf_resolver"])


def test_adapter_does_not_request_llm_supplement_without_llm_config(monkeypatch):
    install_fake_extractor(monkeypatch)
    FakeClient.calls.clear()
    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=True,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ))

    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert result.available is True
    assert FakeClient.calls[0]["include_llm_supplement"] is False
    assert FakeClient.last_config.kwargs["pdf_resolver"] is None


def test_pdf_resolver_uses_report_collector_pdf_info(tmp_path):
    pdf = tmp_path / "annual.pdf"
    pdf.write_text("pdf", encoding="utf-8")

    class FakeReportCollector:
        def fetch_latest_pdf_info(self, stock_code, market, report_types):
            return {"file_path": str(pdf)}

    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=False,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ), report_collector=FakeReportCollector())

    resolved = adapter.resolve_pdf(FakePdfQuery(company="00001", market="HK", period_end="2025-12-31"))

    assert resolved == pdf


def test_factory_materializes_config_when_path_missing(monkeypatch):
    from tradingagents.dataflows.financial_reports import adapter as adapter_module
    from tradingagents.dataflows.financial_reports.config import FinancialReportClientConfig

    monkeypatch.setattr(
        adapter_module,
        "materialize_extractor_llm_config",
        lambda cache_root="": "/tmp/generated-llm.json",
    )
    config = FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=True,
        allow_llm_models=(),
        extractor_cache_root="/tmp/cache",
        llm_config_path="",  # not explicitly provided → should be generated
        pdf_root="",
    )

    result = adapter_module.create_financial_report_adapter(config)
    assert result.config.llm_config_path == "/tmp/generated-llm.json"


def test_factory_keeps_explicit_path_over_materialized(monkeypatch):
    from tradingagents.dataflows.financial_reports import adapter as adapter_module
    from tradingagents.dataflows.financial_reports.config import FinancialReportClientConfig

    monkeypatch.setattr(
        adapter_module,
        "materialize_extractor_llm_config",
        lambda cache_root="": "/tmp/should-not-be-used.json",
    )
    config = FinancialReportClientConfig(
        enabled=True, cache_only=True, force_refresh=False,
        include_llm_supplement=True, allow_llm_models=(),
        extractor_cache_root="", llm_config_path="/explicit/llm.json", pdf_root="",
    )

    result = adapter_module.create_financial_report_adapter(config)
    assert result.config.llm_config_path == "/explicit/llm.json"


def test_factory_degrades_when_materialize_returns_none(monkeypatch):
    from tradingagents.dataflows.financial_reports import adapter as adapter_module
    from tradingagents.dataflows.financial_reports.config import FinancialReportClientConfig

    monkeypatch.setattr(
        adapter_module, "materialize_extractor_llm_config", lambda cache_root="": None
    )
    config = FinancialReportClientConfig(
        enabled=True, cache_only=True, force_refresh=False,
        include_llm_supplement=True, allow_llm_models=(),
        extractor_cache_root="", llm_config_path="", pdf_root="",
    )

    result = adapter_module.create_financial_report_adapter(config)
    assert result.config.llm_config_path == ""  # no crash; supplement effectively off
