import sys
import types

import pandas as pd
import pytest

from tradingagents.dataflows.value_investment.turtle import fx as fxmod


def _fake_yf(history_df):
    """构造一个假的 yfinance 模块，Ticker(...).history(...) 返回给定 DataFrame。"""
    class _Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, start=None, end=None):
            return history_df

    return types.SimpleNamespace(Ticker=_Ticker)


def test_fetch_fx_rate_identity_no_network(monkeypatch):
    # 强制：若误用 yfinance 则 import 失败；identity 路径应在 import 前返回
    monkeypatch.setitem(sys.modules, "yfinance", None)
    q = fxmod.fetch_fx_rate("CNY", "CNY", "2026-05-23")
    assert q is not None
    assert q.rate == 1.0
    assert q.pair == "CNY:CNY"
    assert q.provider == "identity"


def test_fetch_fx_rate_takes_last_row_le_as_of(monkeypatch):
    df = pd.DataFrame(
        {"Close": [0.90, 0.91, 0.92]},
        index=pd.to_datetime(["2026-05-20", "2026-05-21", "2026-05-22"]),
    )
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(df))
    q = fxmod.fetch_fx_rate("HKD", "CNY", "2026-05-23")
    assert q is not None
    assert q.rate == pytest.approx(0.92)
    assert q.as_of == "2026-05-22"
    assert q.pair == "HKD:CNY"
    assert q.provider == "yfinance"


def test_fetch_fx_rate_alias_normalized(monkeypatch):
    df = pd.DataFrame({"Close": [0.9]}, index=pd.to_datetime(["2026-05-22"]))
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(df))
    q = fxmod.fetch_fx_rate("HK$", "RMB", "2026-05-23")
    assert q is not None
    assert q.pair == "HKD:CNY"


def test_fetch_fx_rate_empty_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(pd.DataFrame({"Close": []})))
    assert fxmod.fetch_fx_rate("HKD", "CNY", "2026-05-23") is None


def test_fetch_fx_rate_exception_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a):
            pass

        def history(self, **k):
            raise RuntimeError("network down")

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_Boom))
    assert fxmod.fetch_fx_rate("HKD", "CNY", "2026-05-23") is None


def test_resolve_fx_rates_aggregates_and_skips_target(monkeypatch):
    calls = []

    def fake_fetch(frm, to, as_of):
        calls.append((frm, to))
        return fxmod.FxQuote(pair=f"{frm}:{to}", rate=0.9, provider="yfinance", as_of=as_of, fetched_at="t")

    monkeypatch.setattr(fxmod, "fetch_fx_rate", fake_fetch)
    rates, meta, caveats = fxmod.resolve_fx_rates({"CNY", "HKD"}, "CNY", "2026-05-23")
    assert rates["HKD:CNY"] == pytest.approx(0.9)
    assert rates["CNY:HKD"] == pytest.approx(1 / 0.9)     # reciprocal now present (M3 fix)
    assert meta["HKD:CNY"]["provider"] == "yfinance"
    assert meta["HKD:CNY"]["rate"] == 0.9
    assert meta["CNY:HKD"]["provider"].startswith("derived")
    assert caveats == []
    assert ("CNY", "CNY") not in calls  # target 自身被跳过


def test_resolve_fx_rates_partial_failure_adds_caveat(monkeypatch):
    def fake_fetch(frm, to, as_of):
        if frm == "USD":
            return None
        return fxmod.FxQuote(pair=f"{frm}:{to}", rate=0.9, provider="yfinance", as_of=as_of, fetched_at="t")

    monkeypatch.setattr(fxmod, "fetch_fx_rate", fake_fetch)
    rates, meta, caveats = fxmod.resolve_fx_rates({"HKD", "USD"}, "CNY", "2026-05-23")
    assert rates["HKD:CNY"] == pytest.approx(0.9)
    assert rates["CNY:HKD"] == pytest.approx(1 / 0.9)
    assert all("USD" not in pair for pair in rates)  # USD 取数失败 → 不参与矩阵
    assert any("USD:CNY" in c for c in caveats)


def test_resolve_fx_rates_reciprocal_enables_native_hkd_target(monkeypatch):
    # 复现 M3：net_profit/market_cap 同为 HKD → calculations 选 native HKD target，
    # 此时 CNY buyback 需 CNY:HKD。矩阵必须同时含 HKD:CNY 与 CNY:HKD。
    def fake_fetch(frm, to, as_of):
        return fxmod.FxQuote(pair=f"{frm}:{to}", rate=0.9, provider="yfinance", as_of=as_of, fetched_at="t")

    monkeypatch.setattr(fxmod, "fetch_fx_rate", fake_fetch)
    rates, _, _ = fxmod.resolve_fx_rates({"HKD", "CNY"}, "CNY", "2026-05-23")
    assert "HKD:CNY" in rates and "CNY:HKD" in rates
    assert rates["HKD:CNY"] * rates["CNY:HKD"] == pytest.approx(1.0)


def test_resolve_fx_rates_cross_pair_via_triangulation(monkeypatch):
    rate_map = {"HKD": 0.9, "USD": 7.2}  # HKD:CNY=0.9, USD:CNY=7.2

    def fake_fetch(frm, to, as_of):
        return fxmod.FxQuote(pair=f"{frm}:{to}", rate=rate_map[frm], provider="yfinance", as_of=as_of, fetched_at="t")

    monkeypatch.setattr(fxmod, "fetch_fx_rate", fake_fetch)
    rates, _, _ = fxmod.resolve_fx_rates({"HKD", "USD", "CNY"}, "CNY", "2026-05-23")
    # 交叉对经 CNY 三角化：HKD:USD = (HKD:CNY)/(USD:CNY)
    assert rates["HKD:USD"] == pytest.approx(0.9 / 7.2)
    assert rates["USD:HKD"] == pytest.approx(7.2 / 0.9)


def test_fx_helpers_exported_from_package():
    from tradingagents.dataflows.value_investment.turtle import (
        FxQuote,
        fetch_fx_rate,
        normalize_currency,
        resolve_fx_rates,
    )
    assert callable(fetch_fx_rate)
    assert callable(resolve_fx_rates)
    assert normalize_currency("RMB") == "CNY"
    q = FxQuote(pair="HKD:CNY", rate=0.9, provider="yfinance", as_of="2026-05-23", fetched_at="t")
    assert q.pair == "HKD:CNY"


def test_resolve_fx_rates_derived_meta_uses_underlying_as_of(monkeypatch):
    # 底层 HKD:CNY 实际取到 2026-05-22，派生 CNY:HKD 的 as_of 应跟随底层而非请求日 2026-05-23
    def fake_fetch(frm, to, as_of):
        return fxmod.FxQuote(pair=f"{frm}:{to}", rate=0.9, provider="yfinance", as_of="2026-05-22", fetched_at="t")

    monkeypatch.setattr(fxmod, "fetch_fx_rate", fake_fetch)
    rates, meta, _ = fxmod.resolve_fx_rates({"HKD", "CNY"}, "CNY", "2026-05-23")
    assert meta["HKD:CNY"]["as_of"] == "2026-05-22"          # 直连：真实报价日
    assert meta["CNY:HKD"]["as_of"] == "2026-05-22"          # 派生：跟随底层，非请求日
    assert meta["CNY:HKD"]["derived_from"] == ["HKD:CNY"]


def test_resolve_fx_rates_cross_derived_meta_uses_oldest_underlying(monkeypatch):
    as_of_map = {"HKD": "2026-05-22", "USD": "2026-05-21"}
    rate_map = {"HKD": 0.9, "USD": 7.2}

    def fake_fetch(frm, to, as_of):
        return fxmod.FxQuote(pair=f"{frm}:{to}", rate=rate_map[frm], provider="yfinance",
                             as_of=as_of_map[frm], fetched_at="t")

    monkeypatch.setattr(fxmod, "fetch_fx_rate", fake_fetch)
    _, meta, _ = fxmod.resolve_fx_rates({"HKD", "USD", "CNY"}, "CNY", "2026-05-23")
    # HKD:USD 由 HKD:CNY(05-22) 与 USD:CNY(05-21) 三角化 → 取最旧 05-21
    assert meta["HKD:USD"]["as_of"] == "2026-05-21"
    assert sorted(meta["HKD:USD"]["derived_from"]) == ["HKD:CNY", "USD:CNY"]
