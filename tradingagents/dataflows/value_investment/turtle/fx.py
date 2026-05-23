"""Turtle v0.15 跨币 FX 取数（yfinance），I/O 隔离、可 mock。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .facts import normalize_currency


@dataclass(frozen=True)
class FxQuote:
    pair: str          # 归一化后的 "FROM:TO"，如 "HKD:CNY"
    rate: float        # 1 FROM 兑 rate 个 TO
    provider: str      # "yfinance" | "identity"
    as_of: str         # 实际取到的汇率日期 (YYYY-MM-DD)
    fetched_at: str    # 拉取时刻 ISO timestamp


def fetch_fx_rate(from_currency: str, to_currency: str, as_of_date: str) -> FxQuote | None:
    """取 1 from_currency 兑多少 to_currency 的汇率，对齐 as_of_date（取最近 <= as_of 的交易日）。
    失败 / 无数据 → None（不抛）。
    """
    src = normalize_currency(from_currency)
    dst = normalize_currency(to_currency)
    fetched_at = datetime.now(timezone.utc).isoformat()

    if src == dst:
        return FxQuote(pair=f"{src}:{dst}", rate=1.0, provider="identity", as_of=as_of_date, fetched_at=fetched_at)

    try:
        import yfinance as yf

        end = datetime.strptime(as_of_date[:10], "%Y-%m-%d")
        start = end - timedelta(days=7)
        symbol = f"{src}{dst}=X"
        hist = yf.Ticker(symbol).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        )
    except Exception:
        return None

    if hist is None or len(hist) == 0:
        return None

    try:
        rate = float(hist.iloc[-1]["Close"])
        as_of = hist.index[-1].strftime("%Y-%m-%d")
    except Exception:
        return None

    if not (rate > 0):
        return None

    return FxQuote(pair=f"{src}:{dst}", rate=rate, provider="yfinance", as_of=as_of, fetched_at=fetched_at)


def resolve_fx_rates(
    currencies: Iterable[str], target: str, as_of_date: str
) -> tuple[dict[str, float], dict[str, dict], list[str]]:
    """对每个 != target 的归一币种取 *:target 汇率。
    返回 (fx_rates, fx_rates_meta, caveats)。失败的 pair 进 caveats、不进 fx_rates。
    """
    dst = normalize_currency(target)
    fx_rates: dict[str, float] = {}
    fx_rates_meta: dict[str, dict] = {}
    caveats: list[str] = []
    seen: set[str] = set()

    for raw in currencies:
        src = normalize_currency(raw)
        if src == dst or src in seen:
            continue
        seen.add(src)
        quote = fetch_fx_rate(src, dst, as_of_date)
        if quote is None:
            caveats.append(f"FX {src}:{dst} 取数失败，跨币计算降级")
            continue
        fx_rates[quote.pair] = quote.rate
        fx_rates_meta[quote.pair] = {
            "provider": quote.provider,
            "as_of": quote.as_of,
            "fetched_at": quote.fetched_at,
            "rate": quote.rate,
        }
    return fx_rates, fx_rates_meta, caveats
