"""Adapt non-report market inputs into Turtle market facts."""

from __future__ import annotations

import logging
import os
from math import isfinite
from typing import Any

from .facts import MoneyAmount, TurtleFactValue, TurtleMarketFacts, TurtleStatus, default_holding_channel


logger = logging.getLogger(__name__)
SOURCE_LABEL = "market-adapter"


def _normalize_market(market: str) -> str:
    return (market or "").strip().upper()


def _is_hk_market(market: str) -> bool:
    return _normalize_market(market) in {"HK", "HKG"}


def _is_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _numeric_value(value: Any) -> float | None:
    if not _is_numeric(value):
        return None
    return float(value)


def _field(
    name: str,
    value: Any,
    source_reference: str,
    *,
    caveat: str | None = None,
    reliability: str = "reliable",
) -> TurtleFactValue:
    return TurtleFactValue(
        name=name,
        value=value,
        source_label=SOURCE_LABEL,
        source_reference=source_reference,
        reliability=reliability,
        caveat=caveat,
    )


def _append_caveat(caveats: list[str], caveat: str) -> None:
    if caveat not in caveats:
        caveats.append(caveat)


def _currency_for_market(market: str) -> str:
    return "HKD" if _is_hk_market(market) else "CNY"


def _first_present(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _fetch_hk_market_data(ticker: str) -> dict[str, Any]:
    """Fetch HK quote facts from the HK provider and map them to Turtle inputs."""
    try:
        from tradingagents.dataflows.providers.hk.hk_stock import get_hk_stock_info

        info = get_hk_stock_info(ticker) or {}
    except Exception as exc:
        logger.warning("Failed to fetch HK market data for %s: %s", ticker, exc)
        return {}

    return {
        "market_cap": _first_present(info, ("market_cap", "marketCap")),
        "close_price": _first_present(
            info,
            (
                "close_price",
                "price",
                "current_price",
                "currentPrice",
                "regularMarketPrice",
                "regular_market_price",
            ),
        ),
        "total_shares": _first_present(info, ("total_shares", "shares_outstanding", "sharesOutstanding")),
        "industry": info.get("industry"),
        "source": info.get("source"),
    }


def _fetch_turtle_market_data(ticker: str, market: str) -> dict[str, Any]:
    if _is_hk_market(market):
        return _fetch_hk_market_data(ticker)

    from tradingagents.tools.value_investment_tool import _fetch_market_data_structured

    return _fetch_market_data_structured(ticker, market)


def _fetch_turtle_dividend_data(ticker: str, market: str) -> dict[str, Any] | None:
    if _is_hk_market(market):
        return None

    from tradingagents.tools.value_investment_tool import _fetch_dividend_data_sync

    return _fetch_dividend_data_sync(ticker, market)


def _fetch_turtle_buyback_data(ticker: str, market: str) -> dict[str, Any] | None:
    if _is_hk_market(market):
        return None

    from tradingagents.tools.value_investment_tool import _fetch_buyback_data_sync

    return _fetch_buyback_data_sync(ticker, market)


def _fetch_turtle_industry(ticker: str, market: str, market_data: dict[str, Any] | None) -> str | None:
    industry = (market_data or {}).get("industry")
    if industry:
        return industry
    if _is_hk_market(market):
        return None

    from tradingagents.tools.value_investment_tool import _get_industry_dynamic

    return _get_industry_dynamic(ticker, market)


def _env_rf_rate(market: str) -> float | None:
    normalized = _normalize_market(market)
    keys = ["TURTLE_RF_RATE_HK"] if normalized in {"HK", "HKG"} else ["TURTLE_RF_RATE_CN", "TURTLE_RF_RATE_A"]
    saw_invalid = False
    for key in keys:
        value = os.getenv(key)
        if value is None:
            continue
        if _is_numeric(value):
            return float(value)
        saw_invalid = True
    if saw_invalid:
        raise ValueError("rf_rate invalid")
    return None


def default_tax_rate(market: str, holding_channel: str) -> float:
    """Return default dividend withholding tax by market and holding channel."""
    normalized_market = _normalize_market(market)
    normalized_channel = (holding_channel or "").strip().lower()

    if normalized_market in {"A", "CN", "CHINA"} and normalized_channel == "long_term_domestic":
        return 0.0
    if normalized_market in {"HK", "HKG"}:
        if normalized_channel == "direct_h_share":
            return 0.28
        return 0.20
    if normalized_market in {"US", "USA"} and normalized_channel == "w8ben":
        return 0.10
    return 0.0


def _is_known_tax_rate_combination(market: str, holding_channel: str) -> bool:
    normalized_market = _normalize_market(market)
    normalized_channel = (holding_channel or "").strip().lower()

    if normalized_market in {"A", "CN", "CHINA"}:
        return normalized_channel == "long_term_domestic"
    if normalized_market in {"HK", "HKG"}:
        return normalized_channel in {"stock_connect", "direct_h_share"}
    if normalized_market in {"US", "USA"}:
        return normalized_channel == "w8ben"
    return False


def build_market_facts(
    *,
    ticker: str,
    market: str,
    holding_channel: str | None,
    market_data: dict[str, Any] | None,
    dividend_data: dict[str, Any] | None,
    buyback_data: dict[str, Any] | None,
    industry: str | None,
    rf_rate: float | None = None,
) -> TurtleMarketFacts:
    """Build Turtle market facts from structured non-PDF inputs."""
    fields: dict[str, TurtleFactValue] = {}
    caveats: list[str] = []
    safe_market_data = market_data or {}
    currency = _currency_for_market(market)

    stripped_channel = holding_channel.strip() if holding_channel else ""
    channel_is_explicit = bool(stripped_channel)
    active_channel = stripped_channel or default_holding_channel(market)

    has_market_cap = "market_cap" in safe_market_data and safe_market_data.get("market_cap") is not None
    market_cap = _numeric_value(safe_market_data.get("market_cap"))
    if market_cap is not None and market_cap > 0:
        fields["market_cap"] = _field(
            "market_cap",
            MoneyAmount(
                value=market_cap,
                currency=currency,
                unit="yuan",
                source_label=SOURCE_LABEL,
                source_reference="market_data.market_cap",
            ),
            "market_data.market_cap",
        )
    elif has_market_cap:
        _append_caveat(caveats, "market_cap invalid")
    else:
        _append_caveat(caveats, "market_cap missing")

    close_price = safe_market_data.get("close_price")
    if _is_numeric(close_price):
        fields["close_price"] = _field("close_price", float(close_price), "market_data.close_price")

    tax_rate_known = _is_known_tax_rate_combination(market, active_channel)
    if not tax_rate_known:
        tax_rate_reliability = "display_only"
        tax_rate_caveat = f"tax_rate unknown for {market}:{active_channel}"
        _append_caveat(caveats, tax_rate_caveat)
    elif not channel_is_explicit:
        tax_rate_reliability = "display_only"
        tax_rate_caveat = (
            f"tax_rate uses default holding_channel '{active_channel}' for {market}; "
            "pass holding_channel explicitly to enable computation"
        )
        _append_caveat(caveats, tax_rate_caveat)
    else:
        tax_rate_reliability = "reliable"
        tax_rate_caveat = None

    fields["tax_rate"] = _field(
        "tax_rate",
        default_tax_rate(market, active_channel),
        "holding_channel.default_tax_rate",
        caveat=tax_rate_caveat,
        reliability=tax_rate_reliability,
    )
    fields["holding_channel"] = _field("holding_channel", active_channel, "holding_channel")

    try:
        active_rf_rate = rf_rate if rf_rate is not None else _env_rf_rate(market)
    except ValueError:
        active_rf_rate = None
        _append_caveat(caveats, "rf_rate invalid")
    if _is_numeric(active_rf_rate):
        fields["rf_rate"] = _field("rf_rate", float(active_rf_rate), "rf_rate")
    elif "rf_rate invalid" not in caveats:
        _append_caveat(caveats, "rf_rate missing")

    if industry:
        fields["industry"] = _field("industry", industry, "industry")

    if dividend_data is None:
        _append_caveat(caveats, "dividend data missing")
    else:
        avg_payout_ratio = dividend_data.get("avg_payout_ratio_3y")
        dividend_records = dividend_data.get("records")
        has_dividend_records = "records" in dividend_data and dividend_records is not None
        dividend_record_list = list(dividend_records) if has_dividend_records else None
        legacy_empty_dividend_default = (
            has_dividend_records
            and dividend_record_list == []
            and _numeric_value(avg_payout_ratio) == 0
        )
        if _is_numeric(avg_payout_ratio) and not legacy_empty_dividend_default:
            fields["dividend_avg_payout_ratio_3y"] = _field(
                "dividend_avg_payout_ratio_3y",
                float(avg_payout_ratio),
                "dividend_data.avg_payout_ratio_3y",
            )
        else:
            _append_caveat(caveats, "avg_payout_ratio_3y missing")

        if has_dividend_records:
            fields["dividend_records"] = _field(
                "dividend_records",
                dividend_record_list,
                "dividend_data.records",
            )
        else:
            _append_caveat(caveats, "dividend records missing")

    if buyback_data is None:
        _append_caveat(caveats, "buyback data missing")
    else:
        has_buyback_amount = "total_cancelled_amount" in buyback_data and buyback_data.get("total_cancelled_amount") is not None
        buyback_amount = _numeric_value(buyback_data.get("total_cancelled_amount"))
        buyback_records = buyback_data.get("records")
        has_buyback_records = "records" in buyback_data and buyback_records is not None
        buyback_record_list = list(buyback_records or []) if has_buyback_records else None
        legacy_empty_buyback_default = has_buyback_records and buyback_record_list == [] and buyback_amount == 0
        if buyback_amount is not None and buyback_amount >= 0 and not legacy_empty_buyback_default:
            fields["buyback_amount"] = _field(
                "buyback_amount",
                MoneyAmount(
                    value=buyback_amount,
                    currency=currency,
                    unit="yuan",
                    source_label=SOURCE_LABEL,
                    source_reference="buyback_data.total_cancelled_amount",
                ),
                "buyback_data.total_cancelled_amount",
            )
        elif has_buyback_amount:
            caveat = "buyback_amount missing" if legacy_empty_buyback_default else "buyback_amount invalid"
            _append_caveat(caveats, caveat)
        else:
            _append_caveat(caveats, "buyback_amount missing")
        if has_buyback_records:
            fields["buyback_records"] = _field(
                "buyback_records",
                buyback_record_list,
                "buyback_data.records",
            )

    _BUILTIN_FIELDS = frozenset({"tax_rate", "holding_channel", "rf_rate"})
    external_fields = {k: v for k, v in fields.items() if k not in _BUILTIN_FIELDS}

    if not external_fields:
        status: TurtleStatus = "non_decisionable"
    elif caveats or any(
        f.reliability != "reliable"
        or (isinstance(f.value, MoneyAmount) and f.value.reliability != "reliable")
        or f.caveat
        for f in fields.values()
    ):
        status = "degraded"
    else:
        status = "complete"

    return TurtleMarketFacts(fields=fields, caveats=caveats, status=status)


def get_turtle_market_facts(ticker: str, market: str, holding_channel: str | None = None) -> TurtleMarketFacts:
    """Fetch non-PDF market inputs and adapt them to Turtle facts."""
    market_data = _fetch_turtle_market_data(ticker, market)

    return build_market_facts(
        ticker=ticker,
        market=market,
        holding_channel=holding_channel,
        market_data=market_data,
        dividend_data=_fetch_turtle_dividend_data(ticker, market),
        buyback_data=_fetch_turtle_buyback_data(ticker, market),
        industry=_fetch_turtle_industry(ticker, market, market_data),
    )
