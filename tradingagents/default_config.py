import os

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", "./results"),
    "data_dir": os.path.join(os.path.expanduser("~"), "Documents", "TradingAgents", "data"),
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "o4-mini",
    "quick_think_llm": "gpt-4o-mini",
    "backend_url": "https://api.openai.com/v1",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Tool settings - 从环境变量读取，提供默认值
    "online_tools": os.getenv("ONLINE_TOOLS_ENABLED", "false").lower() == "true",
    "online_news": os.getenv("ONLINE_NEWS_ENABLED", "true").lower() == "true",
    "realtime_data": os.getenv("REALTIME_DATA_ENABLED", "false").lower() == "true",

    # Report-Collector 财报收集服务
    "report_collector_url": os.getenv("REPORT_COLLECTOR_URL", "http://localhost"),
    "report_collector_port": int(os.getenv("REPORT_COLLECTOR_PORT", "8001")),
    "report_collector_enabled": os.getenv("REPORT_COLLECTOR_ENABLED", "false").lower() == "true",
    "report_collector_analysis_enabled": os.getenv("REPORT_COLLECTOR_ANALYSIS_ENABLED", "false").lower() == "true",
    "report_collector_timeout": int(os.getenv("REPORT_COLLECTOR_TIMEOUT", "60")),
    "report_collector_max_reports": int(os.getenv("REPORT_COLLECTOR_MAX_REPORTS", "5")),
    "report_collector_usd_hkd_rate": float(os.getenv("REPORT_COLLECTOR_USD_HKD_RATE", "7.8")),

    # FinancialReportClient annual-report extractor integration
    "financial_report_client_enabled": os.getenv("FINANCIAL_REPORT_CLIENT_ENABLED", "false").lower() == "true",
    "financial_report_cache_only": os.getenv("FINANCIAL_REPORT_CACHE_ONLY", "true").lower() == "true",
    "financial_report_force_refresh": os.getenv("FINANCIAL_REPORT_FORCE_REFRESH", "false").lower() == "true",
    "financial_report_include_llm_supplement": os.getenv("FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT", "false").lower() == "true",
    "financial_report_allow_llm_models": os.getenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "gpt-5.5,codex"),
    "financial_report_extractor_cache_root": os.getenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", ""),
    "financial_report_llm_config_path": os.getenv("FINANCIAL_REPORT_LLM_CONFIG_PATH", ""),
    "financial_report_pdf_root": os.getenv("FINANCIAL_REPORT_PDF_ROOT", ""),

    # Note: Database and cache configuration is now managed by .env file and config.database_manager
    # No database/cache settings in default config to avoid configuration conflicts
}
