"""Dataflow package exports.

The package initializer stays lightweight so subpackages such as
`tradingagents.dataflows.financial_reports` can be imported without loading
news providers, market data providers, or optional runtime dependencies.
Legacy top-level exports are loaded on demand through `__getattr__`.
"""

from __future__ import annotations


_INTERFACE_EXPORTS = {
    "get_finnhub_news",
    "get_finnhub_company_insider_sentiment",
    "get_finnhub_company_insider_transactions",
    "get_google_news",
    "get_reddit_global_news",
    "get_reddit_company_news",
    "get_simfin_balance_sheet",
    "get_simfin_cashflow",
    "get_simfin_income_statements",
    "get_stock_stats_indicators_window",
    "get_stockstats_indicator",
    "get_YFin_data_window",
    "get_YFin_data",
    "get_china_stock_data_tushare",
    "get_china_stock_fundamentals_tushare",
    "get_china_stock_data_unified",
    "get_china_stock_info_unified",
    "switch_china_data_source",
    "get_current_china_data_source",
    "get_hk_stock_data_unified",
    "get_hk_stock_info_unified",
    "get_stock_data_by_market",
}

_PROVIDER_EXPORTS = {
    "get_data_in_range",
    "YFinanceUtils",
    "YFINANCE_AVAILABLE",
}

_NEWS_EXPORTS = {
    "getNewsData",
    "fetch_top_from_category",
}

_TECHNICAL_EXPORTS = {
    "StockstatsUtils",
    "STOCKSTATS_AVAILABLE",
}


def __getattr__(name: str):
    if name in _INTERFACE_EXPORTS:
        from . import interface

        value = getattr(interface, name)
        globals()[name] = value
        return value

    if name in _PROVIDER_EXPORTS:
        from .providers import us

        value = getattr(us, name)
        globals()[name] = value
        return value

    if name in _NEWS_EXPORTS:
        from . import news

        value = getattr(news, name)
        globals()[name] = value
        return value

    if name in _TECHNICAL_EXPORTS:
        from . import technical

        value = getattr(technical, name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(
    _INTERFACE_EXPORTS
    | _PROVIDER_EXPORTS
    | _NEWS_EXPORTS
    | _TECHNICAL_EXPORTS
)
