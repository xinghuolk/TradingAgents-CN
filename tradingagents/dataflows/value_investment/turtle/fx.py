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
    """解析跨币 FX。先取每个非 target 币种的 X:target 直连汇率，再经 target 三角化产出
    全部有序配对（含倒数与交叉），使 calculations 选任意 native target 都能命中所需 pair。
    返回 (fx_rates, fx_rates_meta, caveats)；失败的币种进 caveats、不参与矩阵。
    """
    dst = normalize_currency(target)
    to_target: dict[str, float] = {dst: 1.0}          # 各币种兑 target 的直连汇率（target 自身=1）
    as_of_by_ccy: dict[str, str] = {dst: as_of_date}  # 各币种实际数据日期（target 用请求日，取数币种用 quote.as_of）
    fetched_meta: dict[str, dict] = {}                # 仅直连抓取的 pair 的真实 provenance
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
        to_target[src] = quote.rate
        as_of_by_ccy[src] = quote.as_of
        fetched_meta[quote.pair] = {
            "provider": quote.provider,
            "as_of": quote.as_of,
            "fetched_at": quote.fetched_at,
            "rate": quote.rate,
        }

    # 经 target 三角化产出全部有序配对（含倒数与交叉），exact。
    fx_rates: dict[str, float] = {}
    fx_rates_meta: dict[str, dict] = {}
    derived_at = datetime.now(timezone.utc).isoformat()
    universe = list(to_target.keys())
    for a in universe:
        for b in universe:
            if a == b:
                continue
            rate = to_target[a] / to_target[b]
            pair = f"{a}:{b}"
            fx_rates[pair] = rate
            if pair in fetched_meta:
                fx_rates_meta[pair] = fetched_meta[pair]
            else:
                legs = [f"{c}:{dst}" for c in (a, b) if c != dst]
                fx_rates_meta[pair] = {
                    "provider": f"derived(via {dst})",
                    "as_of": min(as_of_by_ccy[a], as_of_by_ccy[b]),  # 取底层最旧数据日期（YYYY-MM-DD 字典序=时序）
                    "fetched_at": derived_at,
                    "rate": rate,
                    "derived_from": legs,
                }
    return fx_rates, fx_rates_meta, caveats
