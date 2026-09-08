# Turtle v0.15 Data-Source Quality (FX + Provenance) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 Turtle v0.15 跨币 FX 通道（yfinance FX 对、锚定 market_cap 的 as-of）并补齐市场数据 / FX 的来源可追溯性，所有改动 fail-fast 且复用现成降级级联。

**Architecture:** 新增 I/O 隔离的 `turtle/fx.py`（`fetch_fx_rate` / `resolve_fx_rates`）。装配发生在 `prepare_turtle_analysis_payload`：扫描 report+market 的 money fact 币种（归一化去重），**仅当 ≥2 种币种时**调 fx 模块、把 `fx_rates`+provenance 注入 `report.metadata`。币种归一化（`normalize_currency`）同时作用于 `to_hundred_million` 拼 pair 和 `calculations._money_fact_currencies` 收集，避免 `HK$`/`HKD` 误判多币。market provenance（provider/market_as_of/fetched_at）经新增的 `TurtleMarketFacts.metadata` 透传到 tool payload、analyst prompt。

**Tech Stack:** Python 3.10+，dataclasses（frozen），yfinance（FX，惰性 import + 全程 mock），pandas（测试构造 DataFrame），pytest。

**Spec:** `docs/superpowers/specs/2026-05-23-turtle-data-source-quality-design.md`（commit 323af89）

---

## 关键约定（每个 subagent 必读）

- **测试入口**：`.venv/bin/python -m pytest <path> -v`（`.venv` 已装 pytest + langchain_core + pandas；**勿用** homebrew pytest——缺 langchain_core）。跑全套时附 `--ignore=tests/unit/dataflows/test_unified_dataframe.py --ignore=tests/unit/test_stocks_kline_news_api.py`（pre-existing collection errors，与本任务无关）。
- **提交授权**：CLAUDE.md 明示 *NEVER commit unless explicitly asked*。每个 Task 的 commit step 由主控代理（已获 batch 授权）执行；subagent 完成实现 + 测试通过 + 自审后，把 commit 交回主控。
- **零真实网络**：FX 测试一律 mock `yfinance`（注入 `sys.modules["yfinance"]` 或 monkeypatch `fetch_fx_rate`）。
- **FX 锚点**：FX 永远对齐 `market_as_of`（market_cap 快照日期），**绝不** fallback 到 `trade_date`。
- **pair 方向**：`fx_rates["HKD:CNY"]` = 1 HKD 兑多少 CNY。
- **目录**：turtle 单测扁平放在 `tests/unit/test_turtle_*.py`。

---

## Phase 1 — facts.py 基础（币种归一化 + market metadata）

### Task 1: `normalize_currency` helper

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/facts.py`（在 `default_holding_channel` 之后、`MoneyAmount` 之前插入）
- Test: `tests/unit/test_turtle_facts.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_turtle_facts.py` 末尾追加：

```python
import pytest
from tradingagents.dataflows.value_investment.turtle.facts import normalize_currency


@pytest.mark.parametrize("raw,expected", [
    ("RMB", "CNY"), ("rmb", "CNY"), ("CNY", "CNY"), ("人民币", "CNY"), ("元", "CNY"),
    ("HKD", "HKD"), ("HK$", "HKD"), ("港币", "HKD"), ("港元", "HKD"),
    ("USD", "USD"), ("US$", "USD"), ("美元", "USD"),
    ("EUR", "EUR"), ("  hkd  ", "HKD"),
])
def test_normalize_currency_aliases(raw, expected):
    assert normalize_currency(raw) == expected
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_facts.py::test_normalize_currency_aliases -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_currency'`

- [ ] **Step 3: 实现**

在 `facts.py` 的 `default_holding_channel` 函数之后插入：

```python
def normalize_currency(currency: str) -> str:
    """把币种别名归一到 ISO 码（CNY/HKD/USD）；未知币种回退 .upper()。"""
    raw = str(currency or "").strip()
    upper = raw.upper()
    if upper in {"RMB", "CNY"} or raw in {"人民币", "元"}:
        return "CNY"
    if upper in {"HKD", "HK$"} or raw in {"港币", "港元"}:
        return "HKD"
    if upper in {"USD", "US$"} or raw in {"美元"}:
        return "USD"
    return upper
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_facts.py::test_normalize_currency_aliases -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/facts.py tests/unit/test_turtle_facts.py
git commit -m "feat(turtle): add normalize_currency helper (Spec 3)"
```

---

### Task 2: `to_hundred_million` 拼 pair 前归一化

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/facts.py:89-91`（`to_hundred_million` 内）
- Test: `tests/unit/test_turtle_facts.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_facts.py`：

```python
from tradingagents.dataflows.value_investment.turtle.facts import MoneyAmount


def _money(value, currency, unit="yuan"):
    return MoneyAmount(value=value, currency=currency, unit=unit, source_label="t", source_reference="ref")


def test_to_hundred_million_rmb_alias_normalizes_no_fx():
    # RMB 归一为 CNY，target CNY -> 不需要 FX
    m = _money(100_000_000, "RMB").to_hundred_million(target_currency="CNY", fx_rates={})
    assert m.currency == "CNY"
    assert m.value == pytest.approx(1.0)


def test_to_hundred_million_hk_dollar_alias_uses_hkd_pair():
    # "HK$" 归一为 HKD -> 查 fx_rates["HKD:CNY"]，不再误拼 "HK$:CNY"
    m = _money(100_000_000, "HK$").to_hundred_million(target_currency="CNY", fx_rates={"HKD:CNY": 0.9})
    assert m.currency == "CNY"
    assert m.value == pytest.approx(0.9)


def test_to_hundred_million_missing_fx_still_raises():
    with pytest.raises(ValueError):
        _money(100_000_000, "HKD").to_hundred_million(target_currency="CNY", fx_rates={})
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_facts.py::test_to_hundred_million_hk_dollar_alias_uses_hkd_pair -v`
Expected: FAIL — `ValueError: FX rate required for HK$:CNY`（当前用 `.upper()` 拼出 `HK$:CNY`）

- [ ] **Step 3: 实现**

在 `facts.py` 的 `to_hundred_million` 中，把：

```python
        normalized_value = float(self.value) * multipliers[self.unit]
        normalized_currency = self.currency.upper()
        desired_currency = target_currency.upper()
        source_reference = self.source_reference
```

改为：

```python
        normalized_value = float(self.value) * multipliers[self.unit]
        normalized_currency = normalize_currency(self.currency)
        desired_currency = normalize_currency(target_currency)
        source_reference = self.source_reference
```

（`normalize_currency` 已在同模块 Task 1 定义，直接调用即可。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_facts.py -v`
Expected: PASS（含既有 to_hundred_million 用例不回归——既有用例用 ISO 码，归一为 identity）

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/facts.py tests/unit/test_turtle_facts.py
git commit -m "fix(turtle): normalize currency aliases when building FX pair (Spec 3)"
```

---

### Task 3: `TurtleMarketFacts.metadata` 字段

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/facts.py:186-201`（`TurtleMarketFacts`）
- Test: `tests/unit/test_turtle_facts.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_facts.py`：

```python
from tradingagents.dataflows.value_investment.turtle.facts import TurtleMarketFacts


def test_market_facts_metadata_roundtrip():
    mf = TurtleMarketFacts(fields={}, caveats=[], status="complete", metadata={"market_as_of": "2026-05-23"})
    assert mf.metadata == {"market_as_of": "2026-05-23"}
    assert mf.to_dict()["metadata"] == {"market_as_of": "2026-05-23"}


def test_market_facts_metadata_defaults_empty():
    mf = TurtleMarketFacts()
    assert mf.metadata == {}
    assert mf.to_dict()["metadata"] == {}


def test_market_facts_metadata_defensive_copy():
    src = {"market_as_of": "2026-05-23"}
    mf = TurtleMarketFacts(metadata=src)
    src["market_as_of"] = "MUTATED"
    assert mf.metadata["market_as_of"] == "2026-05-23"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_facts.py::test_market_facts_metadata_roundtrip -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'metadata'`

- [ ] **Step 3: 实现**

把 `facts.py` 的 `TurtleMarketFacts` 整体替换为：

```python
@dataclass(frozen=True)
class TurtleMarketFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    status: TurtleStatus = "complete"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _copy_dict(self.fields))
        object.__setattr__(self, "caveats", _copy_list(self.caveats))
        object.__setattr__(self, "metadata", _copy_dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "caveats": _copy_list(self.caveats),
            "status": self.status,
            "metadata": _copy_dict(self.metadata),
        }
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_facts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/facts.py tests/unit/test_turtle_facts.py
git commit -m "feat(turtle): add metadata field to TurtleMarketFacts (Spec 3)"
```

---

## Phase 2 — FX 模块（`turtle/fx.py`）

### Task 4: `fetch_fx_rate`

**Files:**
- Create: `tradingagents/dataflows/value_investment/turtle/fx.py`
- Test: `tests/unit/test_turtle_fx.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_turtle_fx.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_fx.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...turtle.fx'`

- [ ] **Step 3: 实现**

新建 `tradingagents/dataflows/value_investment/turtle/fx.py`：

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_fx.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/fx.py tests/unit/test_turtle_fx.py
git commit -m "feat(turtle): add fetch_fx_rate via yfinance FX pairs (Spec 3)"
```

---

### Task 5: `resolve_fx_rates`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/fx.py`（追加函数）
- Test: `tests/unit/test_turtle_fx.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_fx.py`：

```python
def test_resolve_fx_rates_aggregates_and_skips_target(monkeypatch):
    calls = []

    def fake_fetch(frm, to, as_of):
        calls.append((frm, to))
        return fxmod.FxQuote(pair=f"{frm}:{to}", rate=0.9, provider="yfinance", as_of=as_of, fetched_at="t")

    monkeypatch.setattr(fxmod, "fetch_fx_rate", fake_fetch)
    rates, meta, caveats = fxmod.resolve_fx_rates({"CNY", "HKD"}, "CNY", "2026-05-23")
    assert rates == {"HKD:CNY": 0.9}
    assert meta["HKD:CNY"]["provider"] == "yfinance"
    assert meta["HKD:CNY"]["rate"] == 0.9
    assert caveats == []
    assert ("CNY", "CNY") not in calls  # target 自身被跳过


def test_resolve_fx_rates_partial_failure_adds_caveat(monkeypatch):
    def fake_fetch(frm, to, as_of):
        if frm == "USD":
            return None
        return fxmod.FxQuote(pair=f"{frm}:{to}", rate=0.9, provider="yfinance", as_of=as_of, fetched_at="t")

    monkeypatch.setattr(fxmod, "fetch_fx_rate", fake_fetch)
    rates, meta, caveats = fxmod.resolve_fx_rates({"HKD", "USD"}, "CNY", "2026-05-23")
    assert rates == {"HKD:CNY": 0.9}
    assert any("USD:CNY" in c for c in caveats)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_fx.py::test_resolve_fx_rates_aggregates_and_skips_target -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'resolve_fx_rates'`

- [ ] **Step 3: 实现**

在 `fx.py` 末尾追加：

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_fx.py -v`
Expected: PASS（全部 fx 用例）

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/fx.py tests/unit/test_turtle_fx.py
git commit -m "feat(turtle): add resolve_fx_rates aggregation (Spec 3)"
```

---

### Task 6: 从 turtle 包导出 fx helper

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/__init__.py`
- Test: `tests/unit/test_turtle_fx.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_fx.py`：

```python
def test_fx_helpers_exported_from_package():
    from tradingagents.dataflows.value_investment.turtle import (
        fetch_fx_rate,
        normalize_currency,
        resolve_fx_rates,
    )
    assert callable(fetch_fx_rate)
    assert callable(resolve_fx_rates)
    assert normalize_currency("RMB") == "CNY"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_fx.py::test_fx_helpers_exported_from_package -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_fx_rate'`

- [ ] **Step 3: 实现**

在 `__init__.py` 的 `from .facts import (...)` 块中加入 `normalize_currency`（按字母序放在 `merge_status` 前后均可），并新增一行 import + 三个 `__all__` 条目：

在 import 区追加：

```python
from .fx import FxQuote, fetch_fx_rate, resolve_fx_rates
```

在 `from .facts import (...)` 的导入名单里加 `normalize_currency`：

```python
from .facts import (
    FormulaResult,
    MoneyAmount,
    TurtleComputedSignals,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
    default_holding_channel,
    infer_turtle_period_end,
    merge_status,
    normalize_currency,
)
```

在 `__all__` 列表加入（保持原有项不动）：

```python
    "FxQuote",
    "fetch_fx_rate",
    "normalize_currency",
    "resolve_fx_rates",
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_fx.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/__init__.py tests/unit/test_turtle_fx.py
git commit -m "feat(turtle): export fx helpers from turtle package (Spec 3)"
```

---

## Phase 3 — calculations 币种收集归一化

### Task 7: `_money_fact_currencies` 改用 `normalize_currency`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/calculations.py:8`（import）、`:75`（收集行）
- Test: `tests/unit/test_turtle_calculations.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_calculations.py`：

```python
from tradingagents.dataflows.value_investment.turtle import calculations as calc
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
)


def _facts_single_currency(report_currency, market_currency):
    ctx = TurtleRunContext.for_ticker(ticker="X", market="HK", trade_date="2026-05-23", company_name="X")
    np_ = MoneyAmount(value=1e8, currency=report_currency, unit="yuan", source_label="t", source_reference="r")
    mc = MoneyAmount(value=2e8, currency=market_currency, unit="yuan", source_label="t", source_reference="m")
    report = TurtleReportFacts(fields={
        "net_profit": TurtleFactValue(name="net_profit", value=np_, source_label="t", source_reference="r"),
    })
    market = TurtleMarketFacts(fields={
        "market_cap": TurtleFactValue(name="market_cap", value=mc, source_label="t", source_reference="m"),
    })
    return TurtleFacts(context=ctx, report=report, market=market, status="complete")


def test_money_fact_currencies_collapses_hk_dollar_alias():
    # "HK$" 与 "HKD" 必须收成同一种 HKD，避免误判多币
    facts = _facts_single_currency("HK$", "HKD")
    assert calc._money_fact_currencies(facts, ("net_profit", "market_cap")) == {"HKD"}
    assert calc._money_target_currency(facts, ("net_profit", "market_cap")) == "HKD"


def test_money_fact_currencies_rmb_alias_is_cny():
    facts = _facts_single_currency("RMB", "CNY")
    assert calc._money_fact_currencies(facts, ("net_profit", "market_cap")) == {"CNY"}
    assert calc._money_target_currency(facts, ("net_profit", "market_cap")) == "CNY"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py::test_money_fact_currencies_collapses_hk_dollar_alias -v`
Expected: FAIL — `assert {'HK$', 'HKD'} == {'HKD'}`（当前用原始 `.upper()`）

- [ ] **Step 3: 实现**

(a) `calculations.py:8` 的 import 行追加 `normalize_currency`：

```python
from .facts import FormulaResult, MoneyAmount, TurtleComputedSignals, TurtleFactValue, TurtleFacts, TurtleStatus, normalize_currency
```

(b) `_money_fact_currencies` 内（`:75`）把：

```python
            currencies.add(fact.value.currency.upper())
```

改为：

```python
            currencies.add(normalize_currency(fact.value.currency))
```

`_money_target_currency` 与 `_fx_rates` 等其余逻辑不动。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py -v`
Expected: PASS（含既有 calculations 用例不回归）

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_calculations.py
git commit -m "fix(turtle): normalize currency in _money_fact_currencies (Spec 3 High)"
```

---

## Phase 4 — market_adapter 溯源

### Task 8: `build_market_facts` 写入 provider / market_as_of / fetched_at

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/market_adapter.py`（import 区加 datetime；`:206` 后加派生；market_cap/close_price source_reference；`:350` return 加 metadata）
- Test: `tests/unit/test_turtle_market_adapter.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_market_adapter.py`：

```python
import re

from tradingagents.dataflows.value_investment.turtle.market_adapter import build_market_facts


def test_build_market_facts_carries_provider_and_market_as_of():
    mf = build_market_facts(
        ticker="00700", market="HK", holding_channel="stock_connect",
        market_data={"market_cap": 1e10, "close_price": 100.0, "source": "yfinance_hk"},
        dividend_data=None, buyback_data=None, industry=None,
    )
    cap_ref = mf.fields["market_cap"].value.source_reference
    assert "provider=yfinance_hk" in cap_ref
    assert "fetched_at=" in cap_ref
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", mf.metadata["market_as_of"])


def test_build_market_facts_provider_unknown_when_source_missing():
    mf = build_market_facts(
        ticker="600519", market="A", holding_channel="long_term_domestic",
        market_data={"market_cap": 1e10},
        dividend_data=None, buyback_data=None, industry=None,
    )
    assert "provider=unknown" in mf.fields["market_cap"].value.source_reference
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py::test_build_market_facts_carries_provider_and_market_as_of -v`
Expected: FAIL — `assert 'provider=yfinance_hk' in 'market_data.market_cap'`

- [ ] **Step 3: 实现**

(a) `market_adapter.py` import 区（`from typing import Any` 之后）加：

```python
from datetime import datetime, timezone
```

(b) 在 `build_market_facts` 内、`currency = _currency_for_market(market)`（`:206`）之后插入：

```python
    provider = str(safe_market_data.get("source") or "unknown")
    fetched_at = datetime.now(timezone.utc).isoformat()
    market_as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    market_cap_ref = f"market_data.market_cap; provider={provider}; fetched_at={fetched_at}"
    close_price_ref = f"market_data.close_price; provider={provider}; fetched_at={fetched_at}"
```

(c) market_cap 字段块改为用 `market_cap_ref`（MoneyAmount 与 `_field` 两处 source_reference 都用它）：

```python
    if market_cap is not None and market_cap > 0:
        fields["market_cap"] = _field(
            "market_cap",
            MoneyAmount(
                value=market_cap,
                currency=currency,
                unit="yuan",
                source_label=SOURCE_LABEL,
                source_reference=market_cap_ref,
            ),
            market_cap_ref,
        )
```

(d) close_price 字段块（`:231-233`）改为：

```python
    close_price = safe_market_data.get("close_price")
    if _is_numeric(close_price):
        fields["close_price"] = _field("close_price", float(close_price), close_price_ref)
```

(e) 函数末尾 return（`:350`）改为：

```python
    return TurtleMarketFacts(
        fields=fields,
        caveats=caveats,
        status=status,
        metadata={"market_as_of": market_as_of, "provider": provider},
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/market_adapter.py tests/unit/test_turtle_market_adapter.py
git commit -m "feat(turtle): tag market provider/as_of/fetched_at in market facts (Spec 3 B.2)"
```

---

### Task 9: A股路径注入 `source`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/market_adapter.py`（`_fetch_turtle_market_data` A股 分支，`:107` 附近）
- Test: `tests/unit/test_turtle_market_adapter.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_market_adapter.py`：

```python
from tradingagents.dataflows.value_investment.turtle import market_adapter


def test_fetch_turtle_market_data_stamps_ashare_source(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_market_data_structured",
        lambda ticker, market: {"market_cap": 1e10, "close_price": 100.0, "total_shares": 1e8},
    )
    data = market_adapter._fetch_turtle_market_data("600519", "A")
    assert data["source"] == "akshare.stock_individual_info_em"


def test_fetch_turtle_market_data_keeps_existing_source(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_market_data_structured",
        lambda ticker, market: {"market_cap": 1e10, "source": "custom"},
    )
    data = market_adapter._fetch_turtle_market_data("600519", "A")
    assert data["source"] == "custom"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py::test_fetch_turtle_market_data_stamps_ashare_source -v`
Expected: FAIL — `KeyError: 'source'`

- [ ] **Step 3: 实现**

把 `_fetch_turtle_market_data` 的非 HK 分支改为：

```python
def _fetch_turtle_market_data(ticker: str, market: str) -> dict[str, Any]:
    if _is_hk_market(market):
        return _fetch_hk_market_data(ticker)

    from tradingagents.tools.value_investment_tool import _fetch_market_data_structured

    data = _fetch_market_data_structured(ticker, market)
    if isinstance(data, dict):
        data.setdefault("source", "akshare.stock_individual_info_em")
    return data
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/market_adapter.py tests/unit/test_turtle_market_adapter.py
git commit -m "feat(turtle): stamp A-share market source in _fetch_turtle_market_data (Spec 3 B.2)"
```

---

## Phase 5 — orchestrator FX 注入

### Task 10: `_collect_currencies` helper

**Files:**
- Modify: `tradingagents/tools/turtle_analysis_tool.py`（import 区 + 新增 helper）
- Test: `tests/unit/test_turtle_value_analyst_integration.py`（或新建 `tests/unit/test_turtle_payload_fx.py`——本计划统一用后者承载 Phase 5 集成测试）

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_turtle_payload_fx.py`：

```python
from tradingagents.tools import turtle_analysis_tool as tat
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleFactValue,
    TurtleMarketFacts,
    TurtleReportFacts,
)


def _money_fact(name, value, currency, ref):
    return TurtleFactValue(
        name=name,
        value=MoneyAmount(value=value, currency=currency, unit="yuan", source_label="t", source_reference=ref),
        source_label="t",
        source_reference=ref,
    )


def test_collect_currencies_normalizes_and_dedups():
    report = TurtleReportFacts(fields={"net_profit": _money_fact("net_profit", 5e8, "HK$", "r")})
    market = TurtleMarketFacts(fields={"market_cap": _money_fact("market_cap", 1e10, "HKD", "m")})
    assert tat._collect_currencies(report, market) == {"HKD"}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_payload_fx.py::test_collect_currencies_normalizes_and_dedups -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_collect_currencies'`

- [ ] **Step 3: 实现**

(a) `turtle_analysis_tool.py` 顶部从 turtle 包的 import 名单中加入 `MoneyAmount`、`TurtleFactValue`、`normalize_currency`、`resolve_fx_rates`：

```python
from tradingagents.dataflows.value_investment.turtle import (
    MoneyAmount,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
    compute_turtle_signals,
    get_turtle_market_facts,
    get_turtle_report_facts,
    merge_status,
    normalize_currency,
    resolve_fx_rates,
)
```

并在文件顶部 import 区加：

```python
from datetime import datetime, timezone
```

(b) 在 `_market_facts` 之后新增 helper：

```python
def _collect_currencies(report: TurtleReportFacts, market_facts: TurtleMarketFacts) -> set[str]:
    """收集 report+market（含 historical 各期）money fact 的归一化去重币种集合。"""
    currencies: set[str] = set()

    def _scan(fields: dict[str, TurtleFactValue]) -> None:
        for fact in fields.values():
            value = getattr(fact, "value", None)
            if isinstance(value, MoneyAmount):
                currencies.add(normalize_currency(value.currency))

    _scan(report.fields)
    _scan(market_facts.fields)
    for hist in report.historical.values():
        _scan(hist.fields)
    return currencies
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_payload_fx.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/tools/turtle_analysis_tool.py tests/unit/test_turtle_payload_fx.py
git commit -m "feat(turtle): add _collect_currencies helper in payload tool (Spec 3)"
```

---

### Task 11: `prepare_turtle_analysis_payload` 注入 FX（含门控 + 缺失策略 + snapshot caveat）

**Files:**
- Modify: `tradingagents/tools/turtle_analysis_tool.py:44-83`（`prepare_turtle_analysis_payload`）
- Test: `tests/unit/test_turtle_payload_fx.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_payload_fx.py`：

```python
import json
from unittest.mock import patch


def _report(np_currency):
    return TurtleReportFacts(
        fields={"net_profit": _money_fact("net_profit", 5e8, np_currency, "report.net_profit")},
        status="complete",
    )


def _market(cap_currency, market_as_of="2026-05-23"):
    md = {} if market_as_of is None else {"market_as_of": market_as_of}
    return TurtleMarketFacts(
        fields={"market_cap": _money_fact("market_cap", 1e10, cap_currency, "market_data.market_cap")},
        status="complete",
        metadata=md,
    )


def _run(report, market):
    with patch.object(tat, "get_turtle_report_facts", return_value=report), \
         patch.object(tat, "get_turtle_market_facts", return_value=market):
        return json.loads(tat.prepare_turtle_analysis_payload("00700", "HK", "2026-05-23", "腾讯"))


def test_payload_cross_currency_resolves_fx():
    meta = {"HKD:CNY": {"provider": "yfinance", "as_of": "2026-05-23", "fetched_at": "t", "rate": 0.9}}
    with patch.object(tat, "resolve_fx_rates", return_value=({"HKD:CNY": 0.9}, meta, [])) as rfx:
        out = _run(_report("CNY"), _market("HKD"))
    rfx.assert_called_once()
    rep_meta = out["facts"]["report"]["metadata"]
    assert rep_meta["fx_rates"] == {"HKD:CNY": 0.9}
    assert "HKD:CNY" in rep_meta["fx_rates_meta"]


def test_payload_pure_hkd_skips_fx():
    with patch.object(tat, "resolve_fx_rates") as rfx:
        out = _run(_report("HKD"), _market("HKD"))
    rfx.assert_not_called()
    assert out["facts"]["report"]["metadata"].get("fx_rates", {}) == {}
    assert not any("FX" in c for c in out["facts"]["report"]["caveats"])


def test_payload_fx_failure_adds_caveat():
    with patch.object(tat, "resolve_fx_rates", return_value=({}, {}, ["FX HKD:CNY 取数失败，跨币计算降级"])):
        out = _run(_report("CNY"), _market("HKD"))
    assert any("取数失败" in c for c in out["facts"]["report"]["caveats"])


def test_payload_missing_market_as_of_uses_fetch_date_not_trade_date():
    captured = {}

    def fake_resolve(currencies, target, as_of):
        captured["as_of"] = as_of
        return ({"HKD:CNY": 0.9}, {"HKD:CNY": {}}, [])

    with patch.object(tat, "get_turtle_report_facts", return_value=_report("CNY")), \
         patch.object(tat, "get_turtle_market_facts", return_value=_market("HKD", market_as_of=None)), \
         patch.object(tat, "resolve_fx_rates", side_effect=fake_resolve):
        out = json.loads(tat.prepare_turtle_analysis_payload("00700", "HK", "2020-01-01", "x"))
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", captured["as_of"])
    assert captured["as_of"] != "2020-01-01"
    assert any("market_as_of 缺失" in c for c in out["facts"]["report"]["caveats"])


def test_payload_snapshot_caveat_when_market_as_of_differs_from_trade_date():
    with patch.object(tat, "get_turtle_report_facts", return_value=_report("CNY")), \
         patch.object(tat, "get_turtle_market_facts", return_value=_market("HKD", market_as_of="2026-05-23")), \
         patch.object(tat, "resolve_fx_rates", return_value=({"HKD:CNY": 0.9}, {"HKD:CNY": {}}, [])):
        out = json.loads(tat.prepare_turtle_analysis_payload("00700", "HK", "2020-01-01", "x"))
    assert any("快照" in c for c in out["facts"]["report"]["caveats"])
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_payload_fx.py::test_payload_cross_currency_resolves_fx -v`
Expected: FAIL — `KeyError: 'fx_rates'`（payload 暂无 fx_rates）

- [ ] **Step 3: 实现**

把 `prepare_turtle_analysis_payload` 中、`market_facts = _market_facts(...)` 之后、`facts = TurtleFacts(...)` 之前插入 FX 注入块（其余行不动）：

```python
    market_facts = _market_facts(
        get_turtle_market_facts(
            ticker=ticker,
            market=market,
            holding_channel=holding_channel,
        )
    )

    # --- Spec 3: FX 注入（锚定 market_as_of，绝不 fallback trade_date）---
    fx_caveats: list[str] = []
    market_as_of = market_facts.metadata.get("market_as_of")
    if not market_as_of:
        market_as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fx_caveats.append("market_as_of 缺失，FX 已对齐拉取日")

    currencies = _collect_currencies(report, market_facts)
    if len(currencies) >= 2:
        fx_rates, fx_rates_meta, resolve_caveats = resolve_fx_rates(currencies, "CNY", market_as_of)
        fx_caveats.extend(resolve_caveats)
    else:
        fx_rates, fx_rates_meta = {}, {}

    if market_as_of != trade_date[:10]:
        fx_caveats.append(
            f"market_cap 为当前快照（as_of={market_as_of}），非 trade_date={trade_date} 当日历史值；FX 已对齐快照日期"
        )

    report = TurtleReportFacts(
        fields=report.fields,
        metadata={**report.metadata, "fx_rates": fx_rates, "fx_rates_meta": fx_rates_meta},
        caveats=[*report.caveats, *fx_caveats],
        status=report.status,
        historical=report.historical,
    )
    # --- end Spec 3 ---

    facts = TurtleFacts(
        context=context,
        report=report,
        market=market_facts,
        status=merge_status(report.status, market_facts.status),
        caveats=[*report.caveats, *market_facts.caveats],
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_payload_fx.py -v`
Expected: PASS（全部 6 条 Phase 5 集成用例）

- [ ] **Step 5: Commit**

```bash
git add tradingagents/tools/turtle_analysis_tool.py tests/unit/test_turtle_payload_fx.py
git commit -m "feat(turtle): inject FX rates into payload, gated on >=2 currencies (Spec 3 A.3)"
```

---

### Task 12: `_market_facts` 强转保留 metadata

**Files:**
- Modify: `tradingagents/tools/turtle_analysis_tool.py:34-41`（`_market_facts`）
- Test: `tests/unit/test_turtle_payload_fx.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_payload_fx.py`：

```python
def test_market_facts_coercion_preserves_metadata():
    class _Obj:
        fields = {}
        caveats = []
        status = "complete"
        metadata = {"market_as_of": "2026-05-23"}

    mf = tat._market_facts(_Obj())
    assert mf.metadata == {"market_as_of": "2026-05-23"}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_payload_fx.py::test_market_facts_coercion_preserves_metadata -v`
Expected: FAIL — `assert {} == {'market_as_of': '2026-05-23'}`

- [ ] **Step 3: 实现**

把 `_market_facts` 改为：

```python
def _market_facts(value: Any) -> TurtleMarketFacts:
    if isinstance(value, TurtleMarketFacts):
        return value
    return TurtleMarketFacts(
        fields=getattr(value, "fields", {}) or {},
        caveats=getattr(value, "caveats", []) or [],
        status=getattr(value, "status", "complete"),
        metadata=getattr(value, "metadata", {}) or {},
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_payload_fx.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/tools/turtle_analysis_tool.py tests/unit/test_turtle_payload_fx.py
git commit -m "fix(turtle): preserve market metadata in _market_facts coercion (Spec 3)"
```

---

## Phase 6 — value_analyst provenance 透传

### Task 13: `_plain_turtle_report_prompt` 重建保留 market metadata（可选读取）

**Files:**
- Modify: `tradingagents/agents/analysts/value_analyst.py:192-196`（`TurtleMarketFacts(...)` 重建）
- Test: `tests/unit/test_turtle_value_analyst_integration.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_turtle_value_analyst_integration.py`：

```python
import json
from unittest.mock import patch

from tradingagents.agents.analysts import value_analyst as va


def _payload(with_market_metadata: bool) -> str:
    market = {"fields": {}, "caveats": [], "status": "complete"}
    if with_market_metadata:
        market["metadata"] = {"market_as_of": "2026-05-23"}
    return json.dumps({
        "facts": {
            "context": {
                "ticker": "00700", "market": "HK", "trade_date": "2026-05-23",
                "period_end": "2025-12-31", "holding_channel": "stock_connect", "company_name": "腾讯",
            },
            "report": {"fields": {}, "metadata": {}, "caveats": [], "status": "complete", "historical": {}},
            "market": market,
            "status": "complete", "caveats": [],
        },
        "signals": {"status": "complete", "results": {}, "veto_reasons": [], "caveats": []},
    })


def test_plain_prompt_preserves_market_metadata():
    captured = {}
    with patch.object(va, "build_turtle_decision_prompt",
                      side_effect=lambda facts, signals: captured.update(facts=facts) or "PROMPT"):
        va._plain_turtle_report_prompt("腾讯", "00700", _payload(with_market_metadata=True))
    assert captured["facts"].market.metadata == {"market_as_of": "2026-05-23"}


def test_plain_prompt_legacy_payload_without_market_metadata():
    captured = {}
    with patch.object(va, "build_turtle_decision_prompt",
                      side_effect=lambda facts, signals: captured.update(facts=facts) or "PROMPT"):
        va._plain_turtle_report_prompt("腾讯", "00700", _payload(with_market_metadata=False))
    assert captured["facts"].market.metadata == {}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py::test_plain_prompt_preserves_market_metadata -v`
Expected: FAIL — `assert {} == {'market_as_of': '2026-05-23'}`（当前重建丢 metadata）

- [ ] **Step 3: 实现**

把 `value_analyst.py` 的 market 重建块（`:192-196`）改为：

```python
        market=TurtleMarketFacts(
            fields=_fact_fields_from_payload(market_payload),
            caveats=list(_required_list(market_payload, "caveats")),
            status=_required_status(market_payload, "status"),
            metadata=dict(market_payload.get("metadata") or {}),
        ),
```

（用可选读取 `market_payload.get("metadata") or {}`，**不**用 strict `_required_mapping`——兼容无该键的旧 payload。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py -v`
Expected: PASS（含既有 value_analyst 用例不回归）

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/analysts/value_analyst.py tests/unit/test_turtle_value_analyst_integration.py
git commit -m "fix(turtle): propagate market metadata on payload rebuild in value_analyst (Spec 3)"
```

---

## Phase 7 — 回归收口

### Task 14: 既有断言更新 + 全套回归

**Files:**
- Modify（按需）：`tests/unit/test_value_analyst_payload_propagation.py`、`tests/unit/test_simple_analysis_service_turtle_payload.py`、其它断言 market `to_dict` 结构的既有用例
- Test: 全套 turtle 单测

- [ ] **Step 1: 跑全套 turtle 单测，定位因 `metadata` 新键而失败的既有断言**

Run:
```bash
.venv/bin/python -m pytest tests/unit/ -k turtle -v
```
Expected: 大部分 PASS；可能有断言 market `to_dict()` 精确等于旧 dict（无 `"metadata"`）的用例 FAIL。逐一记录失败用例。

- [ ] **Step 2: 修正失败断言（additive 更新）**

对每个失败用例：若断言形如 `assert mf.to_dict() == {"fields": ..., "caveats": ..., "status": ...}`，补上 `"metadata": {}`（或实际值）。**只**改断言以反映新增的 additive 键，不改测试意图。例如：

```python
# before
assert market_dict == {"fields": {}, "caveats": [], "status": "complete"}
# after
assert market_dict == {"fields": {}, "caveats": [], "status": "complete", "metadata": {}}
```

若用例只断言子集（`assert market_dict["status"] == "complete"`）则无需改动。

- [ ] **Step 3: 跑全套确认通过**

Run:
```bash
.venv/bin/python -m pytest tests/unit/ --ignore=tests/unit/dataflows/test_unified_dataframe.py --ignore=tests/unit/test_stocks_kline_news_api.py -q
```
Expected: 全绿（无 FAIL / ERROR）。

- [ ] **Step 4: Commit**

```bash
git add tests/unit/
git commit -m "test(turtle): update existing assertions for additive market metadata key (Spec 3)"
```

---

## 完成后

所有 Task 完成、全套绿后，按 subagent-driven-development 收尾：dispatch 一个 final code reviewer 复核整支分支，再用 `superpowers:finishing-a-development-branch` 决定 PR / merge。承诺支付率（Spec 2 optional）不在本分支。

## Spec 覆盖自查

| Spec 章节 / 要求 | 对应 Task |
|---|---|
| §5 normalize_currency helper | Task 1 |
| §5 to_hundred_million 归一化 | Task 2 |
| §7 TurtleMarketFacts.metadata 字段 | Task 3 |
| §4 fetch_fx_rate（identity/最近≤as_of/空/异常） | Task 4 |
| §4 resolve_fx_rates（聚合/部分失败 caveat） | Task 5 |
| §10 turtle/__init__ 导出 | Task 6 |
| §5 High：calculations._money_fact_currencies 归一化 | Task 7 |
| §7 B.2：build_market_facts provider/market_as_of/fetched_at | Task 8 |
| §7 B.2：A股 source 注入 | Task 9 |
| §6 _collect_currencies | Task 10 |
| §6 FX 注入 + ≥2 币门控 + 缺失策略 + snapshot caveat；§3 as-of；§8 降级 | Task 11 |
| §7 _market_facts 透传 metadata | Task 12 |
| §7 Medium：value_analyst 可选读取 metadata（新/旧 payload） | Task 13 |
| §10 既有断言 additive 更新 | Task 14 |
