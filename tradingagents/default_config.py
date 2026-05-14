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

    # Note: Database and cache configuration is now managed by .env file and config.database_manager
    # No database/cache settings in default config to avoid configuration conflicts
}
