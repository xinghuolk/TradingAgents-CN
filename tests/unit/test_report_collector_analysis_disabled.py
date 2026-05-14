from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.services.report_collector_config import get_report_collector_config


def test_report_collector_analysis_disabled_by_default():
    assert DEFAULT_CONFIG["report_collector_analysis_enabled"] is False


def test_value_tool_report_collector_config_separates_service_and_analysis(monkeypatch):
    monkeypatch.setenv("REPORT_COLLECTOR_ENABLED", "true")
    monkeypatch.delenv("REPORT_COLLECTOR_ANALYSIS_ENABLED", raising=False)

    config = get_report_collector_config()

    assert config["enabled"] is True
    assert config["analysis_enabled"] is False


def test_value_tool_report_collector_analysis_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("REPORT_COLLECTOR_ENABLED", "true")
    monkeypatch.setenv("REPORT_COLLECTOR_ANALYSIS_ENABLED", "true")

    config = get_report_collector_config()

    assert config["enabled"] is True
    assert config["analysis_enabled"] is True
