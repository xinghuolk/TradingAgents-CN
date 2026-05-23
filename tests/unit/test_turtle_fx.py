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
