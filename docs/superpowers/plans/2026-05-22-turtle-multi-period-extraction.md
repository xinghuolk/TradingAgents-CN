# Turtle Multi-Period Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `get_turtle_report_facts` 支持跨 3 期年报数据获取（`TurtleReportFacts.historical` 嵌套字段 + `history_periods` opt-in 参数 + ThreadPoolExecutor 并发），并在 calculations 层提供 `_money_hm_report_3y_avg` / `_number_report_3y_avg` 聚合 helpers，为 Spec 2 的 R/GG 公式重建提供 multi-period 数据基础。

**Architecture:** 沿用 Spec 1 的 adapter-emitted status + fail-fast 架构。adapter 只做数据获取（每期独立走 `build_report_facts_from_extraction`），聚合智能留 calculations 层。`historical` 字段嵌套但不递归（约定子 facts 的 historical 为空）。**不修改任何 R/GG 公式**——公式重做留 Spec 2。

**Tech Stack:** Python 3.11, `pytest`, `dataclasses` (frozen), `concurrent.futures.ThreadPoolExecutor`, 已有 `financial_report_llm_extractor` adapter, Spec 1 已建立的 turtle dataclasses。

**Commit policy:** 项目 CLAUDE.md 写明"NEVER commit unless explicitly asked"。每个 task 末尾的 commit 步骤在执行前需向用户确认，或在开始执行整份 plan 时一次性获得 batch 提交授权。Push / PR 单独确认。

**Test command convention:**
- 单测：`.venv/bin/python -m pytest tests/unit/<file>::<test> -v`
- 套件：`.venv/bin/python -m pytest tests/unit/ -q --ignore=tests/unit/dataflows/test_unified_dataframe.py --ignore=tests/unit/test_stocks_kline_news_api.py`
- **必须用 `.venv/bin/python -m pytest`**（.venv 装了 pytest + langchain_core）；homebrew pytest 缺 langchain_core 会让 integration 测试 collection 失败。

---

## File Structure

| 文件 | 职责 | Tasks |
|------|------|-------|
| `tradingagents/dataflows/value_investment/turtle/facts.py` | `TurtleReportFacts.historical` 字段 + `_copy_historical` helper | 1 |
| `tradingagents/dataflows/value_investment/turtle/report_adapter.py` | `_derive_historical_period_ends` + `_fetch_single_period_facts`（重构）+ `_fetch_periods_concurrently` + `get_turtle_report_facts(history_periods=)` | 2-5 |
| `tradingagents/dataflows/value_investment/turtle/calculations.py` | `_money_hm_report_3y_avg` + `_number_report_3y_avg` | 6-7 |
| `tradingagents/tools/turtle_analysis_tool.py` | `prepare_turtle_analysis_payload(history_periods=2)` + `_report_facts` 补 historical | 8 |
| `tradingagents/agents/analysts/value_analyst.py` | `_historical_from_payload` + rehydration | 9 |
| `tests/unit/test_turtle_multi_period.py` | 新建主测试集 | 2-5, 9 |
| `tests/unit/test_turtle_facts.py` | 追加 `TestTurtleReportFactsHistorical` | 1 |
| `tests/unit/test_turtle_calculations.py` | 追加 `TestMoneyHM3yAvg` / `TestNumber3yAvg` | 6-7 |
| `tests/unit/test_turtle_value_analyst_integration.py` | 追加 rehydration + multi-period payload 测试 | 8-9 |

---

## Phase 1：facts.py — historical 字段

### Task 1：`TurtleReportFacts.historical` + `_copy_historical`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/facts.py` (lines 19-21 helper area + 145-163 TurtleReportFacts)
- Test: `tests/unit/test_turtle_facts.py`

- [ ] **Step 1.1：写 failing tests**

追加到 `tests/unit/test_turtle_facts.py`（`TurtleReportFacts` 已在文件顶部 import，不要重复 import）：

```python
class TestTurtleReportFactsHistorical:
    def test_historical_defaults_to_empty(self):
        facts = TurtleReportFacts()
        assert facts.historical == {}

    def test_historical_stores_prior_periods(self):
        prior = TurtleReportFacts(status="complete")
        facts = TurtleReportFacts(historical={"2023-12-31": prior})
        assert "2023-12-31" in facts.historical
        assert facts.historical["2023-12-31"].status == "complete"

    def test_to_dict_includes_historical(self):
        prior = TurtleReportFacts(status="degraded", caveats=["x"])
        facts = TurtleReportFacts(historical={"2023-12-31": prior})
        d = facts.to_dict()
        assert "historical" in d
        assert d["historical"]["2023-12-31"]["status"] == "degraded"
        assert d["historical"]["2023-12-31"]["caveats"] == ["x"]

    def test_nested_historical_stripped(self):
        # 约定：嵌套 historical 应为空；_copy_historical 防御性 strip
        deep = TurtleReportFacts(status="complete")
        mid = TurtleReportFacts(status="complete", historical={"2022-12-31": deep})
        facts = TurtleReportFacts(historical={"2023-12-31": mid})
        assert facts.historical["2023-12-31"].historical == {}

    def test_empty_historical_dict_round_trips(self):
        facts = TurtleReportFacts()
        assert facts.to_dict()["historical"] == {}
```

- [ ] **Step 1.2：跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_facts.py::TestTurtleReportFactsHistorical -v
```

Expected: FAIL（`TypeError: unexpected keyword argument 'historical'` 或 `KeyError: 'historical'`）

- [ ] **Step 1.3：加 `_copy_historical` helper**

在 `tradingagents/dataflows/value_investment/turtle/facts.py` 的 `_copy_list`（约 line 19-21）之后追加：

```python
def _copy_historical(value: dict[str, "TurtleReportFacts"]) -> dict[str, "TurtleReportFacts"]:
    """Defensive copy of historical mapping. Strip nested historical to avoid recursion."""
    if not value:
        return {}
    result: dict[str, "TurtleReportFacts"] = {}
    for period_end, facts in value.items():
        if facts.historical:
            facts = TurtleReportFacts(
                fields=facts.fields,
                metadata=facts.metadata,
                caveats=facts.caveats,
                status=facts.status,
                historical={},
            )
        result[period_end] = facts
    return result
```

注意：`_copy_historical` 引用 `TurtleReportFacts`，但定义在类之前——用字符串前向引用 `"TurtleReportFacts"`，且函数体在运行时才解析，所以 OK（类已在调用时定义）。

- [ ] **Step 1.4：修改 `TurtleReportFacts` dataclass**

替换 `tradingagents/dataflows/value_investment/turtle/facts.py` 的 `TurtleReportFacts`（line 145-163）：

```python
@dataclass(frozen=True)
class TurtleReportFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    status: TurtleStatus = "complete"
    historical: dict[str, "TurtleReportFacts"] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _copy_dict(self.fields))
        object.__setattr__(self, "metadata", _copy_dict(self.metadata))
        object.__setattr__(self, "caveats", _copy_list(self.caveats))
        object.__setattr__(self, "historical", _copy_historical(self.historical))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "metadata": _copy_dict(self.metadata),
            "caveats": _copy_list(self.caveats),
            "status": self.status,
            "historical": {pe: facts.to_dict() for pe, facts in self.historical.items()},
        }
```

- [ ] **Step 1.5：跑测试 + 全套 facts**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_facts.py -v
```

Expected: 全 PASS（含新 5 用例 + 现有用例无回归）

- [ ] **Step 1.6：commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/facts.py tests/unit/test_turtle_facts.py
git commit -m "feat(turtle): add historical field to TurtleReportFacts with defensive nested strip"
```

---

## Phase 2：report_adapter.py — multi-period fetching

### Task 2：`_derive_historical_period_ends`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`
- Test: `tests/unit/test_turtle_multi_period.py` (新建)

- [ ] **Step 2.1：新建测试文件 + failing test**

创建 `tests/unit/test_turtle_multi_period.py`：

```python
from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    _derive_historical_period_ends,
)


class TestDeriveHistoricalPeriodEnds:
    def test_two_periods(self):
        assert _derive_historical_period_ends("2024-12-31", 2) == ["2023-12-31", "2022-12-31"]

    def test_zero_periods(self):
        assert _derive_historical_period_ends("2024-12-31", 0) == []

    def test_negative_periods(self):
        assert _derive_historical_period_ends("2024-12-31", -1) == []

    def test_three_periods(self):
        assert _derive_historical_period_ends("2025-12-31", 3) == [
            "2024-12-31", "2023-12-31", "2022-12-31",
        ]
```

- [ ] **Step 2.2：跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_multi_period.py::TestDeriveHistoricalPeriodEnds -v
```

Expected: FAIL（`ImportError: cannot import name '_derive_historical_period_ends'`）

- [ ] **Step 2.3：实现 helper**

在 `report_adapter.py` 的 `get_turtle_report_facts`（line 320）之前追加：

```python
def _derive_historical_period_ends(latest_period_end: str, history_periods: int) -> list[str]:
    """Compute historical period_ends from latest period_end.

    Example: latest='2024-12-31', history_periods=2 -> ['2023-12-31', '2022-12-31']
    Only supports YYYY-12-31 (annual reports).
    """
    if history_periods <= 0:
        return []
    latest_year = int(latest_period_end[:4])
    return [f"{latest_year - n}-12-31" for n in range(1, history_periods + 1)]
```

- [ ] **Step 2.4：跑测试通过**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_multi_period.py::TestDeriveHistoricalPeriodEnds -v
```

Expected: 4 PASS

- [ ] **Step 2.5：commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/report_adapter.py tests/unit/test_turtle_multi_period.py
git commit -m "feat(turtle): add _derive_historical_period_ends for multi-period support"
```

---

### Task 3：提取 `_fetch_single_period_facts`（行为保持重构）

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py` (lines 320-360, get_turtle_report_facts)
- Test: `tests/unit/test_turtle_report_adapter.py`（现有测试验证行为不变）

- [ ] **Step 3.1：先跑现有 report_adapter 测试建立基线**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py -q
```

记录通过数（应为现有全 PASS，例如 38）。

- [ ] **Step 3.2：提取 `_fetch_single_period_facts` helper**

在 `report_adapter.py` 的 `get_turtle_report_facts` 之前（`_derive_historical_period_ends` 之后）新增：

```python
def _fetch_single_period_facts(
    *,
    adapter: Any,
    ticker: str,
    market: str,
    period_end: str,
    reference_date: str,
    allow_llm_models: tuple[str, ...],
) -> TurtleReportFacts:
    """Fetch + adapt one period's annual report. market is already normalized."""
    result = adapter.get_annual_report_data(
        ticker=ticker,
        market=market,
        period_end=period_end,
        reference_date=reference_date,
    )
    facts = build_report_facts_from_extraction(
        extraction=result.extraction,
        allow_llm_models=allow_llm_models,
        adapter_caveats=list(result.warnings) + list(result.errors),
    )
    if facts.metadata.get("period_end") is not None:
        return facts
    metadata = dict(facts.metadata)
    metadata["period_end"] = period_end
    return TurtleReportFacts(
        fields=facts.fields,
        metadata=metadata,
        caveats=facts.caveats,
        status=facts.status,
        historical=facts.historical,
    )
```

- [ ] **Step 3.3：重写 `get_turtle_report_facts` 单期路径调用新 helper**

替换 `get_turtle_report_facts`（line 320-360 区域，本 task 只做单期路径，多期路径在 Task 5 加）：

```python
def get_turtle_report_facts(
    *,
    ticker: str,
    market: str,
    trade_date: str,
    adapter: Any | None = None,
    allow_llm_models: tuple[str, ...] | None = None,
) -> TurtleReportFacts:
    """Fetch annual report data and adapt it to Turtle report facts."""
    config = None
    active_adapter = adapter
    if active_adapter is None:
        config = get_financial_report_client_config()
        active_adapter = create_financial_report_adapter(config)

    allowed_models = allow_llm_models
    if allowed_models is None:
        config = config or get_financial_report_client_config()
        allowed_models = config.allow_llm_models

    period_end = infer_turtle_period_end(trade_date)
    return _fetch_single_period_facts(
        adapter=active_adapter,
        ticker=ticker,
        market=_normalize_market(market),
        period_end=period_end,
        reference_date=trade_date,
        allow_llm_models=allowed_models,
    )
```

注意：本 task 暂不加 `history_periods` 参数（Task 5 加），只是把原内联逻辑抽成 helper。

- [ ] **Step 3.4：跑测试确认行为不变**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py -q
```

Expected: 与 Step 3.1 同样的通过数，零失败（纯重构）。

- [ ] **Step 3.5：commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/report_adapter.py
git commit -m "refactor(turtle): extract _fetch_single_period_facts from get_turtle_report_facts"
```

---

### Task 4：`_fetch_periods_concurrently`（ThreadPoolExecutor）

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`
- Test: `tests/unit/test_turtle_multi_period.py`

- [ ] **Step 4.1：写 failing tests（用 fake adapter）**

追加到 `tests/unit/test_turtle_multi_period.py`：

```python
from dataclasses import dataclass
from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    _fetch_periods_concurrently,
)
from tradingagents.dataflows.value_investment.turtle.facts import TurtleReportFacts


@dataclass
class _FakeExtraction:
    fields: dict
    staleness: object = None
    company: object = None
    market: object = None
    period_end: str = ""
    catalog_version: object = None


@dataclass
class _FakeResult:
    extraction: object
    warnings: list
    errors: list


class _FakeAdapter:
    """Returns a result per period; raises for periods in fail_set."""
    def __init__(self, fail_set=None):
        self.fail_set = fail_set or set()

    def get_annual_report_data(self, *, ticker, market, period_end, reference_date):
        if period_end in self.fail_set:
            raise RuntimeError(f"simulated fetch failure for {period_end}")
        return _FakeResult(
            extraction=_FakeExtraction(fields={}, period_end=period_end),
            warnings=[], errors=[],
        )


class TestFetchPeriodsConcurrently:
    def test_all_periods_succeed(self):
        adapter = _FakeAdapter()
        results = _fetch_periods_concurrently(
            active_adapter=adapter, ticker="600519", market="CN",
            period_ends=["2024-12-31", "2023-12-31", "2022-12-31"],
            reference_date="2025-05-19", allow_llm_models=(),
        )
        assert set(results.keys()) == {"2024-12-31", "2023-12-31", "2022-12-31"}
        assert all(isinstance(v, TurtleReportFacts) for v in results.values())

    def test_one_period_fails_is_dropped(self):
        adapter = _FakeAdapter(fail_set={"2022-12-31"})
        results = _fetch_periods_concurrently(
            active_adapter=adapter, ticker="600519", market="CN",
            period_ends=["2024-12-31", "2023-12-31", "2022-12-31"],
            reference_date="2025-05-19", allow_llm_models=(),
        )
        # 失败那期 silently drop，不抛异常
        assert set(results.keys()) == {"2024-12-31", "2023-12-31"}

    def test_all_periods_fail_returns_empty(self):
        adapter = _FakeAdapter(fail_set={"2024-12-31", "2023-12-31"})
        results = _fetch_periods_concurrently(
            active_adapter=adapter, ticker="600519", market="CN",
            period_ends=["2024-12-31", "2023-12-31"],
            reference_date="2025-05-19", allow_llm_models=(),
        )
        assert results == {}
```

- [ ] **Step 4.2：跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_multi_period.py::TestFetchPeriodsConcurrently -v
```

Expected: FAIL（`ImportError: cannot import name '_fetch_periods_concurrently'`）

- [ ] **Step 4.3：实现 `_fetch_periods_concurrently`**

在 `report_adapter.py` 的 `_fetch_single_period_facts` 之后追加：

```python
def _fetch_periods_concurrently(
    *,
    active_adapter: Any,
    ticker: str,
    market: str,
    period_ends: list[str],
    reference_date: str,
    allow_llm_models: tuple[str, ...],
) -> dict[str, TurtleReportFacts]:
    """Fetch multiple periods in parallel, keyed by period_end.

    Failed periods are silently omitted (caller applies the >=2 threshold).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, TurtleReportFacts] = {}
    max_workers = min(len(period_ends), 3)
    if max_workers <= 0:
        return results

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_period = {
            pool.submit(
                _fetch_single_period_facts,
                adapter=active_adapter, ticker=ticker, market=market,
                period_end=pe, reference_date=reference_date,
                allow_llm_models=allow_llm_models,
            ): pe
            for pe in period_ends
        }
        for future in as_completed(future_to_period):
            pe = future_to_period[future]
            try:
                results[pe] = future.result()
            except Exception as exc:
                logger.warning(
                    "Failed to fetch annual report for %s %s period_end=%s: %s",
                    ticker, market, pe, exc,
                )
    return results
```

确认 `report_adapter.py` 顶部已有 `logger`——如果没有，加 `import logging` + `logger = logging.getLogger(__name__)`（grep 确认；Spec 1 的 report_adapter 可能用的是别的 logger pattern）。

- [ ] **Step 4.4：跑测试通过**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_multi_period.py::TestFetchPeriodsConcurrently -v
```

Expected: 3 PASS

- [ ] **Step 4.5：commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/report_adapter.py tests/unit/test_turtle_multi_period.py
git commit -m "feat(turtle): add _fetch_periods_concurrently with silent per-period failure drop"
```

---

### Task 5：`get_turtle_report_facts` 加 `history_periods` 参数 + 多期路径

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py` (get_turtle_report_facts)
- Test: `tests/unit/test_turtle_multi_period.py`

- [ ] **Step 5.1：写 failing tests**

追加到 `tests/unit/test_turtle_multi_period.py`：

```python
from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    get_turtle_report_facts,
)


class TestGetTurtleReportFactsMultiPeriod:
    def test_history_periods_zero_no_historical(self):
        adapter = _FakeAdapter()
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=0,
        )
        assert facts.historical == {}

    def test_history_periods_two_populates_historical(self):
        adapter = _FakeAdapter()
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        # latest = 2024-12-31 (trade_date 2025-05, month>3 -> year-1)
        # historical = 2023-12-31, 2022-12-31
        assert set(facts.historical.keys()) == {"2023-12-31", "2022-12-31"}

    def test_one_historical_period_fails_dropped(self):
        adapter = _FakeAdapter(fail_set={"2022-12-31"})
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        assert set(facts.historical.keys()) == {"2023-12-31"}

    def test_latest_fails_returns_synthetic_non_decisionable(self):
        adapter = _FakeAdapter(fail_set={"2024-12-31"})
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        assert facts.status == "non_decisionable"
        assert facts.fields == {}
        assert facts.historical == {}
        assert any("2024-12-31" in c for c in facts.caveats)
```

- [ ] **Step 5.2：跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_multi_period.py::TestGetTurtleReportFactsMultiPeriod -v
```

Expected: FAIL（`history_periods` 参数不存在 / historical 永远空）

- [ ] **Step 5.3：修改 `get_turtle_report_facts` 加多期路径**

替换 `get_turtle_report_facts`（Task 3 已重构成单期 helper 调用版）：

```python
def get_turtle_report_facts(
    *,
    ticker: str,
    market: str,
    trade_date: str,
    adapter: Any | None = None,
    allow_llm_models: tuple[str, ...] | None = None,
    history_periods: int = 0,
) -> TurtleReportFacts:
    """Fetch annual report data and adapt it to Turtle report facts.

    history_periods=0 -> latest period only (Spec 1 behavior)
    history_periods=2 -> latest + 2 prior periods (populated as facts.historical)
    """
    config = None
    active_adapter = adapter
    if active_adapter is None:
        config = get_financial_report_client_config()
        active_adapter = create_financial_report_adapter(config)

    allowed_models = allow_llm_models
    if allowed_models is None:
        config = config or get_financial_report_client_config()
        allowed_models = config.allow_llm_models

    latest_period_end = infer_turtle_period_end(trade_date)
    market_normalized = _normalize_market(market)

    if history_periods <= 0:
        return _fetch_single_period_facts(
            adapter=active_adapter, ticker=ticker, market=market_normalized,
            period_end=latest_period_end, reference_date=trade_date,
            allow_llm_models=allowed_models,
        )

    historical_period_ends = _derive_historical_period_ends(latest_period_end, history_periods)
    all_periods = [latest_period_end] + historical_period_ends

    period_facts_results = _fetch_periods_concurrently(
        active_adapter=active_adapter, ticker=ticker, market=market_normalized,
        period_ends=all_periods, reference_date=trade_date,
        allow_llm_models=allowed_models,
    )

    if latest_period_end not in period_facts_results:
        return TurtleReportFacts(
            fields={},
            metadata={"period_end": latest_period_end},
            caveats=[
                f"latest period {latest_period_end} extraction raised exception during concurrent fetch"
            ],
            status="non_decisionable",
            historical={},
        )

    latest_facts = period_facts_results[latest_period_end]
    historical_facts = {
        pe: period_facts_results[pe]
        for pe in historical_period_ends
        if pe in period_facts_results
    }

    return TurtleReportFacts(
        fields=latest_facts.fields,
        metadata=latest_facts.metadata,
        caveats=latest_facts.caveats,
        status=latest_facts.status,
        historical=historical_facts,
    )
```

- [ ] **Step 5.4：跑测试通过**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_multi_period.py -v
```

Expected: 全 PASS（含 Task 2/4/5 用例）

- [ ] **Step 5.5：跑全套 report_adapter 确认单期行为不变**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py -q
```

Expected: 与 Task 3 基线一致，零失败。

- [ ] **Step 5.6：commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/report_adapter.py tests/unit/test_turtle_multi_period.py
git commit -m "feat(turtle): get_turtle_report_facts history_periods opt-in multi-period path"
```

---

## Phase 3：calculations.py — 聚合 helpers

### Task 6：`_money_hm_report_3y_avg`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/calculations.py`
- Test: `tests/unit/test_turtle_calculations.py`

- [ ] **Step 6.1：写 failing tests**

追加到 `tests/unit/test_turtle_calculations.py`（先确认顶部 import 有 `TurtleFacts`、`TurtleReportFacts`、`TurtleMarketFacts`、`TurtleFactValue`、`MoneyAmount`、`TurtleRunContext`；缺则补 import）：

```python
from tradingagents.dataflows.value_investment.turtle.calculations import (
    _money_hm_report_3y_avg,
    _number_report_3y_avg,
)
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount, TurtleFactValue, TurtleFacts, TurtleReportFacts,
    TurtleMarketFacts, TurtleRunContext,
)


def _money_fact(value, currency="CNY"):
    return TurtleFactValue(
        name="net_profit",
        value=MoneyAmount(
            value=value, currency=currency, unit="hundred_million",
            source_label="report", source_reference=f"p.{value}",
        ),
        source_label="report",
        source_reference=f"p.{value}",
        reliability="reliable",
    )


def _make_facts_with_history(latest_val, history_vals):
    """latest_val + per-year historical money facts for 'net_profit'."""
    ctx = TurtleRunContext(
        ticker="600519", market="A", trade_date="2025-05-19",
        period_end="2024-12-31", holding_channel="long_term_domestic",
        company_name="X",
    )
    historical = {}
    for i, v in enumerate(history_vals, start=1):
        pe = f"{2023 - i + 1}-12-31"
        historical[pe] = TurtleReportFacts(fields={"net_profit": _money_fact(v)} if v is not None else {})
    report = TurtleReportFacts(
        fields={"net_profit": _money_fact(latest_val)} if latest_val is not None else {},
        historical=historical,
    )
    return TurtleFacts(
        context=ctx, report=report, market=TurtleMarketFacts(),
        status="complete", caveats=[],
    )


class TestMoneyHM3yAvg:
    def test_three_periods_mean(self):
        facts = _make_facts_with_history(100.0, [90.0, 110.0])
        caveats = []
        value, sources, missing = _money_hm_report_3y_avg(facts, "net_profit", caveats, "CNY")
        assert value == 100.0   # mean(100, 90, 110)
        assert missing == []
        assert len(sources) == 3

    def test_two_periods_mean_with_caveat(self):
        facts = _make_facts_with_history(100.0, [80.0, None])
        caveats = []
        value, sources, missing = _money_hm_report_3y_avg(facts, "net_profit", caveats, "CNY")
        assert value == 90.0    # mean(100, 80)
        assert missing == []
        assert any("2/3 periods" in c for c in caveats)

    def test_one_period_below_threshold(self):
        facts = _make_facts_with_history(100.0, [None, None])
        caveats = []
        value, sources, missing = _money_hm_report_3y_avg(facts, "net_profit", caveats, "CNY")
        assert value is None
        assert missing == ["net_profit_3y_avg"]

    def test_zero_periods(self):
        facts = _make_facts_with_history(None, [None, None])
        caveats = []
        value, sources, missing = _money_hm_report_3y_avg(facts, "net_profit", caveats, "CNY")
        assert value is None
        assert missing == ["net_profit_3y_avg"]
```

- [ ] **Step 6.2：跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py::TestMoneyHM3yAvg -v
```

Expected: FAIL（`ImportError: cannot import name '_money_hm_report_3y_avg'`）

- [ ] **Step 6.3：实现 `_money_hm_report_3y_avg`**

在 `calculations.py` 的 `_money_hm`（现有函数）之后追加：

```python
def _money_hm_report_3y_avg(
    facts: TurtleFacts,
    name: str,
    caveats: list[str],
    target_currency: str,
) -> tuple[float | None, list[str], list[str]]:
    """Average a report-side money field across [latest + historical periods].

    Reads facts.report and facts.report.historical (report-side only).
    Threshold: >= 2 periods reliable -> mean; else None.
    """
    period_facts_list = [facts.report, *facts.report.historical.values()]
    available_values: list[float] = []
    sources: list[str] = []
    failed_sources: list[str] = []

    for period in period_facts_list:
        fact = period.fields.get(name)
        if fact is None:
            continue
        if not isinstance(fact.value, MoneyAmount):
            failed_sources.append(fact.source_reference)
            continue
        if fact.reliability != "reliable" or fact.value.reliability != "reliable":
            failed_sources.append(fact.source_reference)
            continue
        if isinstance(fact.value.value, bool) or not isinstance(fact.value.value, (int, float)):
            failed_sources.append(fact.source_reference)
            continue
        try:
            amount = fact.value.to_hundred_million(
                target_currency=target_currency, fx_rates=_fx_rates(facts),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            _append_caveat(caveats, str(exc))
            failed_sources.append(fact.source_reference)
            continue
        if not math.isfinite(amount.value):
            failed_sources.append(fact.source_reference)
            continue

        available_values.append(float(amount.value))
        sources.append(amount.source_reference)

    if len(available_values) < 2:
        return None, _merge_sources(sources, failed_sources), [f"{name}_3y_avg"]

    if len(available_values) < 3:
        _append_caveat(caveats, f"{name}_3y_avg computed from {len(available_values)}/3 periods")

    return sum(available_values) / len(available_values), sources, []
```

确认 `calculations.py` 顶部已 import `math`、`MoneyAmount`，且有 `_fx_rates` / `_append_caveat` / `_merge_sources` helpers（Spec 1 已建立）。

- [ ] **Step 6.4：跑测试通过**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py::TestMoneyHM3yAvg -v
```

Expected: 4 PASS

- [ ] **Step 6.5：commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_calculations.py
git commit -m "feat(turtle): add _money_hm_report_3y_avg aggregation helper"
```

---

### Task 7：`_number_report_3y_avg`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/calculations.py`
- Test: `tests/unit/test_turtle_calculations.py`

- [ ] **Step 7.1：写 failing tests**

追加到 `tests/unit/test_turtle_calculations.py`：

```python
def _ratio_fact(value):
    return TurtleFactValue(
        name="payout_ratio",
        value=value,
        source_label="report",
        source_reference=f"ratio.{value}",
        reliability="reliable",
    )


def _make_facts_with_ratio_history(latest_val, history_vals):
    ctx = TurtleRunContext(
        ticker="600519", market="A", trade_date="2025-05-19",
        period_end="2024-12-31", holding_channel="long_term_domestic",
        company_name="X",
    )
    historical = {}
    for i, v in enumerate(history_vals, start=1):
        pe = f"{2023 - i + 1}-12-31"
        historical[pe] = TurtleReportFacts(fields={"payout_ratio": _ratio_fact(v)} if v is not None else {})
    report = TurtleReportFacts(
        fields={"payout_ratio": _ratio_fact(latest_val)} if latest_val is not None else {},
        historical=historical,
    )
    return TurtleFacts(
        context=ctx, report=report, market=TurtleMarketFacts(),
        status="complete", caveats=[],
    )


class TestNumber3yAvg:
    def test_three_periods_mean(self):
        facts = _make_facts_with_ratio_history(0.6, [0.5, 0.4])
        caveats = []
        value, sources, missing = _number_report_3y_avg(facts, "payout_ratio", caveats)
        assert abs(value - 0.5) < 1e-9   # mean(0.6, 0.5, 0.4)
        assert missing == []

    def test_two_periods_with_caveat(self):
        facts = _make_facts_with_ratio_history(0.6, [0.4, None])
        caveats = []
        value, sources, missing = _number_report_3y_avg(facts, "payout_ratio", caveats)
        assert abs(value - 0.5) < 1e-9
        assert any("2/3 periods" in c for c in caveats)

    def test_one_period_below_threshold(self):
        facts = _make_facts_with_ratio_history(0.6, [None, None])
        caveats = []
        value, sources, missing = _number_report_3y_avg(facts, "payout_ratio", caveats)
        assert value is None
        assert missing == ["payout_ratio_3y_avg"]
```

- [ ] **Step 7.2：跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py::TestNumber3yAvg -v
```

Expected: FAIL（import error 或 assert error）

- [ ] **Step 7.3：实现 `_number_report_3y_avg`**

在 `calculations.py` 的 `_money_hm_report_3y_avg` 之后追加：

```python
def _number_report_3y_avg(
    facts: TurtleFacts,
    name: str,
    caveats: list[str],
) -> tuple[float | None, list[str], list[str]]:
    """Average a report-side numeric (non-money) field across periods.

    Used for derived payout ratios (Spec 2 M algorithm). Same >=2 threshold.
    """
    period_facts_list = [facts.report, *facts.report.historical.values()]
    available_values: list[float] = []
    sources: list[str] = []
    failed_sources: list[str] = []

    for period in period_facts_list:
        fact = period.fields.get(name)
        if fact is None:
            continue
        if isinstance(fact.value, bool) or not isinstance(fact.value, (int, float)):
            failed_sources.append(fact.source_reference)
            continue
        if fact.reliability != "reliable":
            failed_sources.append(fact.source_reference)
            continue
        value = float(fact.value)
        if not math.isfinite(value):
            failed_sources.append(fact.source_reference)
            continue

        available_values.append(value)
        sources.append(fact.source_reference)

    if len(available_values) < 2:
        return None, _merge_sources(sources, failed_sources), [f"{name}_3y_avg"]

    if len(available_values) < 3:
        _append_caveat(caveats, f"{name}_3y_avg computed from {len(available_values)}/3 periods")

    return sum(available_values) / len(available_values), sources, []
```

- [ ] **Step 7.4：跑测试通过**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py::TestNumber3yAvg -v
```

Expected: 3 PASS

- [ ] **Step 7.5：跑全套 calculations 确认 R/GG 未受影响**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py -q
```

Expected: 全 PASS（R/GG 公式未改，现有用例不变）

- [ ] **Step 7.6：commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_calculations.py
git commit -m "feat(turtle): add _number_report_3y_avg aggregation helper"
```

---

## Phase 4：integration — tool + value_analyst

### Task 8：`turtle_analysis_tool.py` — opt-in 多期 + `_report_facts` historical

**Files:**
- Modify: `tradingagents/tools/turtle_analysis_tool.py` (lines ~18-30 _report_facts + prepare_turtle_analysis_payload)
- Test: `tests/unit/test_turtle_value_analyst_integration.py`

- [ ] **Step 8.1：写 failing test**

追加到 `tests/unit/test_turtle_value_analyst_integration.py`：

```python
import json
from unittest.mock import patch
from tradingagents.tools.turtle_analysis_tool import prepare_turtle_analysis_payload
from tradingagents.dataflows.value_investment.turtle.facts import (
    TurtleReportFacts, TurtleMarketFacts,
)


class TestPrepareTurtlePayloadMultiPeriod:
    def test_payload_includes_historical(self):
        report_with_history = TurtleReportFacts(
            status="complete",
            historical={"2023-12-31": TurtleReportFacts(status="complete")},
        )
        captured = {}

        def fake_report(**kwargs):
            captured["history_periods"] = kwargs.get("history_periods")
            return report_with_history

        with patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_report_facts",
            side_effect=fake_report,
        ), patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_market_facts",
            return_value=TurtleMarketFacts(status="complete"),
        ):
            payload = prepare_turtle_analysis_payload(
                ticker="600519", market="A", trade_date="2025-05-19", company_name="X",
            )
        # prepare_turtle_analysis_payload 必须传 history_periods=2
        assert captured["history_periods"] == 2
        data = json.loads(payload)
        assert "historical" in data["facts"]["report"]
        assert "2023-12-31" in data["facts"]["report"]["historical"]
```

- [ ] **Step 8.2：跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py::TestPrepareTurtlePayloadMultiPeriod -v
```

Expected: FAIL（`history_periods` 未传 → captured 是 None；或 historical 不在 payload）

- [ ] **Step 8.3：修改 `_report_facts` 兜底分支补 historical**

`turtle_analysis_tool.py` 的 `_report_facts`（约 line 18-30）：

```python
def _report_facts(value: Any) -> TurtleReportFacts:
    if isinstance(value, TurtleReportFacts):
        return value
    return TurtleReportFacts(
        fields=getattr(value, "fields", {}) or {},
        metadata=getattr(value, "metadata", {}) or {},
        caveats=getattr(value, "caveats", []) or [],
        status=getattr(value, "status", "complete"),
        historical=getattr(value, "historical", {}) or {},
    )
```

- [ ] **Step 8.4：修改 `prepare_turtle_analysis_payload` 传 `history_periods=2`**

找到 `prepare_turtle_analysis_payload` 中调用 `get_turtle_report_facts` 的位置，加 `history_periods=2`：

```python
    report = _report_facts(
        get_turtle_report_facts(
            ticker=ticker, market=market, trade_date=trade_date,
            history_periods=2,
        )
    )
```

- [ ] **Step 8.5：跑测试通过**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py::TestPrepareTurtlePayloadMultiPeriod -v
```

Expected: PASS

- [ ] **Step 8.6：跑全套 integration 无回归**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py -v
```

Expected: 全 PASS

- [ ] **Step 8.7：commit**

```bash
git add tradingagents/tools/turtle_analysis_tool.py tests/unit/test_turtle_value_analyst_integration.py
git commit -m "feat(turtle): prepare_turtle_analysis_payload opts into history_periods=2 + _report_facts carries historical"
```

---

### Task 9：`value_analyst.py` — rehydration 支持 historical

**Files:**
- Modify: `tradingagents/agents/analysts/value_analyst.py` (_plain_turtle_report_prompt + new _historical_from_payload)
- Test: `tests/unit/test_turtle_value_analyst_integration.py`

- [ ] **Step 9.1：写 failing test**

追加到 `tests/unit/test_turtle_value_analyst_integration.py`：

```python
class TestValueAnalystRehydratesHistorical:
    def test_plain_prompt_rehydrates_historical(self):
        """_plain_turtle_report_prompt 反序列化含 historical 的 payload 不丢历史数据。"""
        from tradingagents.agents.analysts.value_analyst import _plain_turtle_report_prompt

        payload = json.dumps({
            "facts": {
                "context": {
                    "ticker": "600519", "market": "A", "trade_date": "2025-05-19",
                    "period_end": "2024-12-31", "holding_channel": "long_term_domestic",
                    "company_name": "X",
                },
                "report": {
                    "fields": {}, "metadata": {"period_end": "2024-12-31"},
                    "caveats": [], "status": "complete",
                    "historical": {
                        "2023-12-31": {
                            "fields": {}, "metadata": {"period_end": "2023-12-31"},
                            "caveats": [], "status": "complete", "historical": {},
                        }
                    },
                },
                "market": {"fields": {}, "caveats": [], "status": "complete"},
                "status": "complete", "caveats": [],
            },
            "signals": {"status": "complete", "results": {}, "veto_reasons": [], "caveats": []},
        }, ensure_ascii=False)

        prompt = _plain_turtle_report_prompt("X", "600519", payload)
        # historical 的 period_end 应出现在序列化进 prompt 的 facts markdown 中
        assert "2023-12-31" in prompt
```

- [ ] **Step 9.2：跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py::TestValueAnalystRehydratesHistorical -v
```

Expected: FAIL（rehydration 丢 historical → prompt 不含 "2023-12-31"）

- [ ] **Step 9.3：新增 `_historical_from_payload` helper**

在 `value_analyst.py` 的 `_fact_fields_from_payload`（line 77）附近新增：

```python
def _historical_from_payload(payload: Any) -> dict[str, TurtleReportFacts]:
    """Rehydrate the historical mapping from a serialized report payload."""
    if not isinstance(payload, dict):
        return {}
    result: dict[str, TurtleReportFacts] = {}
    for period_end, raw in payload.items():
        if not isinstance(raw, dict):
            continue
        result[period_end] = TurtleReportFacts(
            fields=_fact_fields_from_payload(raw),
            metadata=dict(_required_mapping(raw, "metadata")),
            caveats=list(_required_list(raw, "caveats")),
            status=_required_status(raw, "status"),
            historical={},   # 嵌套约定为空（_copy_historical 也会 strip）
        )
    return result
```

- [ ] **Step 9.4：在 `_plain_turtle_report_prompt` 重建 report 时填充 historical**

找到 `_plain_turtle_report_prompt` 中构造 `TurtleReportFacts(...)` 的位置，加 `historical`：

```python
        report=TurtleReportFacts(
            fields=_fact_fields_from_payload(report_payload),
            metadata=dict(report_metadata),
            caveats=list(_required_list(report_payload, "caveats")),
            status=_required_status(report_payload, "status"),
            historical=_historical_from_payload(report_payload.get("historical", {})),
        ),
```

- [ ] **Step 9.5：跑测试通过**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py::TestValueAnalystRehydratesHistorical -v
```

Expected: PASS

- [ ] **Step 9.6：跑全套 integration + value_analyst payload propagation**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py tests/unit/test_value_analyst_payload_propagation.py -v
```

Expected: 全 PASS

- [ ] **Step 9.7：commit**

```bash
git add tradingagents/agents/analysts/value_analyst.py tests/unit/test_turtle_value_analyst_integration.py
git commit -m "feat(turtle): rehydrate historical in value_analyst _plain_turtle_report_prompt"
```

---

## Phase 5：终极验收 + 路线图

### Task 10：全套验收 + smoke + 路线图更新

- [ ] **Step 10.1：跑全套 unit 测试**

```bash
.venv/bin/python -m pytest tests/unit/ -q --ignore=tests/unit/dataflows/test_unified_dataframe.py --ignore=tests/unit/test_stocks_kline_news_api.py 2>&1 | tail -5
```

Expected: 全 PASS（基线 380+；Spec 5 新增约 20-30 用例）。若有 failure，STOP 并报告。

- [ ] **Step 10.2：跑 Spec 5 主测试集单独**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_multi_period.py -v
```

Expected: 全 PASS

- [ ] **Step 10.3：跑 Spec 1 turtle 全套确认零回归（兼容性硬要求）**

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_facts.py tests/unit/test_turtle_report_adapter.py tests/unit/test_turtle_market_adapter.py tests/unit/test_turtle_calculations.py tests/unit/test_turtle_decision.py tests/unit/test_turtle_value_analyst_integration.py tests/unit/test_value_analyst_entry.py tests/unit/test_value_analyst_payload_propagation.py -q
```

Expected: 全 PASS

- [ ] **Step 10.4：smoke 脚本验证 historical 字段出现**

```bash
.venv/bin/python scripts/smoke_test_turtle_value.py --ticker 600519 --market A --holding-channel long_term_domestic 2>/dev/null | python3 -c "import sys, json; d=json.load(sys.stdin); print('available_results:', d.get('available_results'))"
```

Expected: 退出 0，stdout 是合法 JSON。（注：smoke summary 不一定直接展示 historical；若 smoke 因网络 / extractor 数据失败，记录 stderr 但不阻断——属于已知环境问题。本步主要验证 payload 生成不崩。）

- [ ] **Step 10.5：路线图更新 Spec 5 状态 🟢 → 🔵/🟠**

修改 `docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md` §4 状态表 Spec 5 行：

```markdown
| Spec 5：multi-period-extraction | 🟠 | docs/superpowers/specs/2026-05-22-turtle-multi-period-extraction-design.md | docs/superpowers/plans/2026-05-22-turtle-multi-period-extraction.md | (待开 PR) | 实施完成，等待 push + PR |
```

§7 当前进度更新为"Spec 5 实施完成，等用户 push + PR；Spec 2 可启动"。

- [ ] **Step 10.6：commit 路线图**

```bash
git add docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md
git commit -m "docs(roadmap): mark Spec 5 implementation complete; Spec 2 unblocked"
```

- [ ] **Step 10.7：⛔ push + PR 等用户单独确认**

不要自动 push / `gh pr create`。报告最终测试通过数 + commit 数，等用户授权。

---

## Self-Review

### 1. Spec 覆盖度

| Spec §9 改动项 | 对应 task |
|----------------|-----------|
| facts.py — historical 字段 + _copy_historical | Task 1 |
| report_adapter.py — _derive_historical_period_ends | Task 2 |
| report_adapter.py — _fetch_single_period_facts 提取 | Task 3 |
| report_adapter.py — _fetch_periods_concurrently | Task 4 |
| report_adapter.py — get_turtle_report_facts history_periods | Task 5 |
| calculations.py — _money_hm_report_3y_avg | Task 6 |
| calculations.py — _number_report_3y_avg | Task 7 |
| turtle_analysis_tool.py — history_periods=2 + _report_facts historical | Task 8 |
| value_analyst.py — _historical_from_payload + rehydration | Task 9 |
| test_turtle_multi_period.py 新建 | Task 2/4/5 |
| test_turtle_facts.py 追加 | Task 1 |
| test_turtle_calculations.py 追加 | Task 6/7 |
| test_turtle_value_analyst_integration.py 追加 | Task 8/9 |

13/13 覆盖 ✓。**不修改 R/GG 公式**（Spec 5 边界）—— Task 6/7 只加 helper，Task 7 Step 7.5 验证 R/GG 现有用例不变。

### 2. Placeholder 扫描

无 TBD / TODO；每个 step 有 concrete code 或命令。

Task 4 Step 4.3 注明"确认 logger 存在，没有则加" —— 这是执行时 verify 项，非 placeholder（logger 存在与否需读现场，已给出 fallback 指令）。

### 3. 类型一致性

- `_derive_historical_period_ends(latest, n) -> list[str]`：Task 2 定义、Task 5 调用 ✓
- `_fetch_single_period_facts(*, adapter, ticker, market, period_end, reference_date, allow_llm_models) -> TurtleReportFacts`：Task 3 定义、Task 4/5 调用 ✓
- `_fetch_periods_concurrently(*, active_adapter, ticker, market, period_ends, reference_date, allow_llm_models) -> dict[str, TurtleReportFacts]`：Task 4 定义、Task 5 调用 ✓
- `_money_hm_report_3y_avg(facts, name, caveats, target_currency) -> tuple`：Task 6，返回 `(value, sources, missing)` 与 `_money_hm` 一致 ✓
- `_number_report_3y_avg(facts, name, caveats) -> tuple`：Task 7 ✓
- `historical` 字段名贯穿 facts.py / report_adapter / calculations / tool / value_analyst 一致 ✓
- `_historical_from_payload(payload) -> dict[str, TurtleReportFacts]`：Task 9 ✓
