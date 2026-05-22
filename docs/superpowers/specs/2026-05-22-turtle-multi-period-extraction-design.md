# Turtle Multi-Period Extraction 设计文档

- **创建日期**：2026-05-22
- **工作分支**：`feat/turtle-spec5-multi-period-extraction`
- **Spec 编号**：Spec 5（路线图 §2 拆分后；Path C 决策的 Spec 2 真正 blocker）
- **路线图**：`docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md`
- **问题来源**：
  - Path C brainstorming（subagent 上游对比发现 Spec 2 R/GG 公式需要 multi-period 数据）
  - 上游算法定义：`/Users/like/source/Stock_Analyze_Prompts/turtle_framework/龟龟投资策略_v0.15/phase3_分析与报告.md`（R 公式 M = `max(min(近 3 年支付率均值, 承诺), 新信号DPS)`，O = 注销型回购年均金额（过去 3 年））

## 1. 目标

扩展 `tradingagents/dataflows/value_investment/turtle/report_adapter.py` 的 `get_turtle_report_facts` 以支持跨 3 期年报数据获取，为 Spec 2 R/GG 公式的 `3y avg` 类输入（M 算法的 `min(近 3 年支付率均值, ...)`、O 公式的"过去 3 年回购年均"）提供数据基础。**本 Spec 不修改任何现有公式**——R/GG 公式重做留 Spec 2。

## 2. 范围

### 2.1 范围内（7 项）

| 编号 | 内容 |
|------|------|
| S5-1 | `TurtleReportFacts` 加 `historical: dict[str, TurtleReportFacts]` 字段；`__post_init__` / `to_dict` 同步处理 |
| S5-2 | `get_turtle_report_facts` 加 `history_periods: int = 0` 参数（opt-in 多期） |
| S5-3 | `history_periods > 0` 时通过 `ThreadPoolExecutor(max_workers=min(N, 3))` 并发 N+1 次 extractor 调用 |
| S5-4 | 跨期失败覆盖：threshold ≥ 2 期 reliable（calculations helpers 落实） |
| S5-5 | calculations 层新增聚合 helpers：`_money_hm_report_3y_avg`、`_number_report_3y_avg` |
| S5-6 | `prepare_turtle_analysis_payload` 显式传 `history_periods=2`（默认启用多期路径） |
| S5-7 | `value_analyst._plain_turtle_report_prompt` 反序列化路径支持 historical 字段 |

### 2.2 范围外（明示推迟）

- R/GG 公式 M / O 算法重做 → Spec 2（依赖本 Spec 5 数据就位）
- payout_anchor → payout_M / payout_anchor_passthrough 命名 → Spec 2
- 跨币 FX 完整支持（A.3 + B.4） → Spec 3
- 承诺支付率字段（extractor 扩展 / dividend_policy_text 二次提取） → Spec 3
- market-side multi-period（akshare dividend pipeline 多年） → 不需要（已有按年聚合）
- 5y / 10y 历史扩展 → 未来（`history_periods` 参数已留扩展空间）
- 跨期 company / unit 强制校验 → 未来 spec follow-up（Spec 5 宽松通过）
- GG bottom-up 现金流量表逐行追踪 → 未来更远 spec

## 3. 核心设计决策

来自 brainstorming Q1-Q5：

1. **数据结构**（Q1=C）：`TurtleReportFacts.historical: dict[period_end, TurtleReportFacts]` 嵌套；adapter 只做数据获取，聚合智能留 calculations 层
2. **跨期失败覆盖**（Q2=C）：threshold ≥ 2 期 reliable 才输出 3y avg；< 2 期返回 None；2 期可用时 caveat 标注 `<field>_3y_avg computed from 2/3 periods`
3. **historical 字段语义**（Q3=A + 固定 3 期）：`historical` 只含**过去 2 期**（latest 仍在 `facts.report` 自身）；嵌套 `TurtleReportFacts` 的 `historical` 应为空；总 3 期 = 1 latest + 2 historical
4. **并发模式**（Q4=B）：`ThreadPoolExecutor` 并发 3 次 extractor 调用；首次分析 ~90s 降至 ~30s；与 Turtle 同步设计一致（不引入 asyncio 边界）
5. **API 表面**（Q5=B）：`history_periods: int = 0` 参数 opt-in；默认 0 保留 Spec 1 单期行为；`prepare_turtle_analysis_payload` 显式传 2 启用多期

## 4. `TurtleReportFacts` 数据结构变更

### 4.1 frozen dataclass 字段扩展

`tradingagents/dataflows/value_investment/turtle/facts.py`：

```python
@dataclass(frozen=True)
class TurtleReportFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    status: TurtleStatus = "complete"
    historical: dict[str, "TurtleReportFacts"] = field(default_factory=dict)   # ← 新增

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

### 4.2 防递归深拷贝 helper

`facts.py` 顶部 helper 区新增：

```python
def _copy_historical(value: dict[str, "TurtleReportFacts"]) -> dict[str, "TurtleReportFacts"]:
    """Defensive copy of historical mapping. Strip any nested historical to avoid recursion."""
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
                historical={},   # strip nested historical (约定)
            )
        result[period_end] = facts
    return result
```

**约定**：

- 顶层 `facts.report.historical = {"2023-12-31": <facts>, "2022-12-31": <facts>}` 填充
- 嵌套 `facts.report.historical["2023-12-31"].historical` 应为 `{}`（不再嵌套）
- `_copy_historical` 在 `__post_init__` 中防御性 strip 任何嵌套的 historical

### 4.3 序列化语义

`to_dict()` 输出形态：

```json
{
  "fields": { "net_profit": {...}, "dividends_paid": {...}, ... },
  "metadata": { "period_end": "2024-12-31", ... },
  "caveats": [...],
  "status": "complete",
  "historical": {
    "2023-12-31": {
      "fields": { ... }, "metadata": { ... },
      "caveats": [...], "status": "complete", "historical": {}
    },
    "2022-12-31": { ... }
  }
}
```

JSON 体积：单期 ~3-5KB，3 期 ~9-15KB；LLM context 仍轻量（< 20KB）。

### 4.4 现有调用方零影响

- 现有 `TurtleReportFacts()` 调用方默认 `historical = {}`，向后兼容
- 现有 `to_dict()` 消费者多出 `historical` key，但只读已知 key 的代码不受影响
- 现有测试 fixture 无 `historical` 参数 → 默认空 dict

### 4.5 value_analyst rehydration 路径更新

`tradingagents/agents/analysts/value_analyst.py` 的 `_plain_turtle_report_prompt` 反序列化 ToolMessage payload 时也要还原 historical：

```python
report=TurtleReportFacts(
    fields=_fact_fields_from_payload(report_payload),
    metadata=dict(report_metadata),
    caveats=list(_required_list(report_payload, "caveats")),
    status=_required_status(report_payload, "status"),
    historical=_historical_from_payload(report_payload.get("historical", {})),
),
```

新增 `_historical_from_payload(payload: dict[str, Any]) -> dict[str, TurtleReportFacts]` helper，递归复用已有的 `_fact_fields_from_payload` / `_required_list` / `_required_status`。嵌套 historical 自动为空（约定 4.2）。

## 5. API 改动

### 5.1 `get_turtle_report_facts` 新签名

```python
def get_turtle_report_facts(
    *,
    ticker: str,
    market: str,
    trade_date: str,
    adapter: Any | None = None,
    allow_llm_models: tuple[str, ...] | None = None,
    history_periods: int = 0,   # ← 新增
) -> TurtleReportFacts:
    """Fetch annual report data and adapt it to Turtle report facts.

    history_periods=0  → fetch only latest period (Spec 1 behavior)
    history_periods=2  → fetch latest + 2 prior periods, populated as facts.historical
    """
```

### 5.2 Period 推导

```python
def _derive_historical_period_ends(latest_period_end: str, history_periods: int) -> list[str]:
    """Compute historical period_ends from latest period_end.

    Example: latest='2024-12-31', history_periods=2 → ['2023-12-31', '2022-12-31']
    Only supports YYYY-12-31 (annual reports). Non-12-31 fiscal years are
    out of scope for Spec 5; the input is assumed normalized by infer_turtle_period_end.
    """
    if history_periods <= 0:
        return []
    latest_year = int(latest_period_end[:4])
    return [f"{latest_year - n}-12-31" for n in range(1, history_periods + 1)]
```

### 5.3 主流程：单期 vs 多期分支

`get_turtle_report_facts` 重构（保留单期路径 + 新增多期路径）：

```python
def get_turtle_report_facts(
    *, ticker, market, trade_date, adapter=None, allow_llm_models=None, history_periods=0,
) -> TurtleReportFacts:
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

    # 单期路径（Spec 1 行为）
    if history_periods <= 0:
        return _fetch_single_period_facts(
            adapter=active_adapter, ticker=ticker, market=market_normalized,
            period_end=latest_period_end, reference_date=trade_date,
            allow_llm_models=allowed_models,
        )

    # 多期路径
    historical_period_ends = _derive_historical_period_ends(latest_period_end, history_periods)
    all_periods = [latest_period_end] + historical_period_ends

    period_facts_results = _fetch_periods_concurrently(
        active_adapter=active_adapter,
        ticker=ticker,
        market=market_normalized,
        period_ends=all_periods,
        reference_date=trade_date,
        allow_llm_models=allowed_models,
    )

    # 如果 latest 期 fetch 在并发执行中抛异常（network / adapter 内部错误），
    # _fetch_periods_concurrently 已 silently drop 并记 warning；这里合成空 facts
    # 避免再发起一次必然失败的 fetch
    if latest_period_end not in period_facts_results:
        return TurtleReportFacts(
            fields={},
            metadata={"period_end": latest_period_end},
            caveats=[f"latest period {latest_period_end} extraction raised exception during concurrent fetch"],
            status="non_decisionable",
            historical={},   # 不带 historical，latest 失败时聚合无意义
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


def _fetch_single_period_facts(
    *, adapter, ticker, market, period_end, reference_date, allow_llm_models,
) -> TurtleReportFacts:
    """Existing Spec 1 single-period logic extracted as helper.

    Used by both history_periods=0 and as building block for multi-period.
    """
    result = adapter.get_annual_report_data(
        ticker=ticker, market=market,
        period_end=period_end, reference_date=reference_date,
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
        fields=facts.fields, metadata=metadata,
        caveats=facts.caveats, status=facts.status,
        historical=facts.historical,
    )
```

### 5.4 ThreadPoolExecutor 并发 helper

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
    """Fetch multiple periods in parallel, returning a dict keyed by period_end.

    Failed periods are silently omitted from the result dict (caller decides
    whether to treat absence as fail-fast per Q2 threshold logic).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, TurtleReportFacts] = {}
    max_workers = min(len(period_ends), 3)

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

**关键设计**：

- 单期失败 → result dict 中不出现，**不抛异常**
- 异常 logged 但不传播
- `max_workers=min(len, 3)` 防止未来 5y/10y 时 ThreadPool 过大

### 5.5 `prepare_turtle_analysis_payload` opt-in

`tradingagents/tools/turtle_analysis_tool.py`：

```python
def prepare_turtle_analysis_payload(
    ticker: str, market: str, trade_date: str, company_name: str,
    holding_channel: str | None = None,
) -> str:
    ...
    report = _report_facts(
        get_turtle_report_facts(
            ticker=ticker, market=market, trade_date=trade_date,
            history_periods=2,   # ← Spec 5 启用多期
        )
    )
    ...
```

实际路径（`get_turtle_report_facts` 返回真实 `TurtleReportFacts`）走 `_report_facts` 的 `isinstance` 分支，直接返回原对象，historical 完整保留。

**但 `_report_facts` 的 duck-typed 兜底分支需要补 `historical`**（`turtle_analysis_tool.py:22` 附近）—— 当前该分支只拷 `fields / metadata / caveats / status`，会丢 historical。改为：

```python
def _report_facts(value: Any) -> TurtleReportFacts:
    if isinstance(value, TurtleReportFacts):
        return value
    return TurtleReportFacts(
        fields=getattr(value, "fields", {}) or {},
        metadata=getattr(value, "metadata", {}) or {},
        caveats=getattr(value, "caveats", []) or [],
        status=getattr(value, "status", "complete"),
        historical=getattr(value, "historical", {}) or {},   # ← 新增，避免测试 double / 兼容路径丢历史
    )
```

真实路径不受影响（走 isinstance 分支），此改动仅保证 test double / duck-typed 兼容路径不丢 historical 字段。

## 6. calculations 层聚合 helpers

`tradingagents/dataflows/value_investment/turtle/calculations.py` 顶部 helper 区新增。

### 6.1 `_money_hm_report_3y_avg`

```python
def _money_hm_report_3y_avg(
    facts: TurtleFacts,
    name: str,
    caveats: list[str],
    target_currency: str,
) -> tuple[float | None, list[str], list[str]]:
    """Average a report-side money field across [latest + historical periods].

    Reads facts.report and facts.report.historical (report-side only — does
    NOT consult facts.market unlike _money_hm). Validates each period's fact
    the same way _money_hm does, then computes mean of available values.

    Threshold (Q2=C): >= 2 periods reliable → mean; else None.
    When 2/3 reliable, append caveat noting partial coverage.

    Returns (value, sources, missing_inputs) consistent with _money_hm shape.
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
        _append_caveat(
            caveats,
            f"{name}_3y_avg computed from {len(available_values)}/3 periods",
        )

    return sum(available_values) / len(available_values), sources, []
```

### 6.2 `_number_report_3y_avg`

```python
def _number_report_3y_avg(
    facts: TurtleFacts,
    name: str,
    caveats: list[str],
) -> tuple[float | None, list[str], list[str]]:
    """Average a report-side numeric (non-money) field across periods.

    Used for payout ratios (Spec 2 M algorithm). Same threshold semantics
    as _money_hm_report_3y_avg.
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
        _append_caveat(
            caveats,
            f"{name}_3y_avg computed from {len(available_values)}/3 periods",
        )

    return sum(available_values) / len(available_values), sources, []
```

### 6.3 设计要点

- 签名与 `_money_hm` / `_number` 对齐：返回 `(value, sources, missing_inputs)` 三元组
- 只读 report-side historical（market-side 无 historical）
- 跨期 currency / unit 差异通过 `to_hundred_million(target, fx_rates)` 各期独立 normalize 后求 mean（宽容处理，不强制三期同币种）
- 失败模式与 `_money_hm` 一致：单期不可用 → 跳过；< 2 期可用 → None + missing_inputs

### 6.4 Spec 5 内的使用 vs Spec 2 接管

| 阶段 | 行为 |
|------|------|
| Spec 5 | 提供 helpers + 完整单元测试。**不修改 R/GG 公式现有调用**（依然 Spec 1 单期 `_money_hm`） |
| Spec 2 | 在 R/GG 公式中切换到 `_money_hm_report_3y_avg`、`_number_report_3y_avg`；重写 M 算法；更新 R/GG 测试 fixture |

### 6.5 `_number_report_3y_avg` 与 payout ratio 数据流（关键澄清）

`_number_report_3y_avg` 消费的是 **per-period 派生的 reliable plain-numeric 字段**，而非 raw extractor 数值字段。两者数据流不同：

**Raw extractor 数值字段**（net_profit / dividends_paid / capex 等）：经 `_adapt_value`（`report_adapter.py`），有 currency + 合法 unit 才转 `MoneyAmount`（reliable），缺 currency/unit 降为 `display_only` 原始数值。这类字段是 **money**，由 `_money_hm_report_3y_avg` 处理，不是 `_number_report_3y_avg`。

**Derived ratio 字段**（payout ratio）：由 `_derive_report_payout_*`（`report_adapter.py:197+`）**手工创建** TurtleFactValue，`value=round(ratio, 12)`（无量纲 plain float），**不经过 `_adapt_value` 的 currency/unit 机制**。这才是 `_number_report_3y_avg` 的消费目标。

**"3 年支付率均值"= mean of per-year ratios（不是 ratio of means）**：

- ✅ 正确（上游"近 3 年支付率均值"语义）：`mean([div_2024/np_2024, div_2023/np_2023, div_2022/np_2022])`
  —— 通过对每期独立派生的 `dividend_payout_ratio_current_year`（Spec 2 命名）做 `_number_report_3y_avg`
- ❌ 错误（ratio of means）：`mean(dividends_3y) / mean(net_profit_3y)` —— 用两次 `_money_hm_report_3y_avg` 相除会得到这个，**Spec 2 不可用此路径**

**per-period 派生自动发生**：multi-period 流程中每期都走 `_fetch_single_period_facts → build_report_facts_from_extraction → _derive_report_payout_*`，所以每个 historical period 的 facts 都含自己年度的 payout ratio 字段。Spec 5 无需新增"per-period 派生"逻辑——它已经在 build 流程里。

**reliability 边界（Spec 5 vs Spec 2）**：

- Spec 1 / Spec 5 当下：派生 payout 字段是 `dividend_payout_ratio_proxy_single_year`，reliability=`display_only` → `_number_report_3y_avg`（要求 reliable）对它返回 None。**这是预期** —— Spec 5 不把它接进 R/GG
- Spec 2：重命名为 `dividend_payout_ratio_current_year` 并升级 reliability=`reliable`，届时 `_number_report_3y_avg` 才真正产出 3y avg payout
- **Spec 5 单元测试用 synthetic facts**（手工构造含 reliability=reliable 的 plain-numeric 字段的 TurtleReportFacts + historical），验证 helper 逻辑本身；不依赖真实 payout 字段的 reliability 状态

**结论**：`_number_report_3y_avg` 设计正确，与 derived ratio 字段的手工创建路径吻合。Spec 5 交付 + 单测 helper；Spec 2 负责升级 payout 字段 reliability 并 wire 进 M 算法。

## 7. 边界情况与缓存

### 7.1 边界情况

| 情况 | 处理 |
|------|------|
| `history_periods=2` 但公司刚上市 1 年（historical period_end 不存在） | extractor 返回 stale / 失败 → silently drop → calculations 判 < 2 期 → 3y avg None |
| trade_date 月 ≤ 3，latest_period_end 偏早（如 trade_date=2026-02 → latest=2024-12-31） | 接受这种近似；与 Spec 1 `infer_turtle_period_end` 一致 |
| 跨期 `company` / `market` 不一致（公司改名 / 重组） | adapter 不强制校验；caveats 自然反映；强制校验留未来 spec follow-up（不在 Spec 5 范围） |
| 跨期 `period_end` 非 `YYYY-12-31`（港股中期 6-30） | Spec 5 不支持；非 12-31 公司继续走单期路径（Spec 1 行为） |
| 跨期 reliability mix（latest reliable + 1 prior display_only + 1 prior reliable）→ 2 reliable | helper 跳过 display_only，只计 reliable → 2 期 mean + caveat |
| `historical` dict 中嵌套了非空 `historical` | `_copy_historical` 防御性 strip → 不抛异常 |
| extractor `fields={}` 但 extraction 非 None | Spec 1 已有逻辑：empty fields → status=non_decisionable；historical 该期不进 reliable 计数 |

### 7.2 缓存策略

extractor 自身的 `RefreshPolicy.CACHE_FIRST` 已是 per-period 缓存。Spec 5 **不增加额外缓存**：

- 3 期调用各自独立 hit extractor cache
- 重复调用 → cache hit → ~milliseconds
- 首次调用 → 并发 fetch → ~30s

测试时通过 mock adapter 避免触发真实 cache 路径。

## 8. 测试策略

### 8.1 新增测试文件

`tests/unit/test_turtle_multi_period.py`（主要）+ 在现有 `test_turtle_facts.py` / `test_turtle_report_adapter.py` / `test_turtle_calculations.py` / `test_turtle_value_analyst_integration.py` 中追加相关用例。

### 8.2 测试场景表

| 测试类 | 用例 |
|--------|------|
| `TestDeriveHistoricalPeriodEnds` | `latest=2024-12-31, history_periods=2 → [2023-12-31, 2022-12-31]`；`history_periods=0 → []`；`history_periods=3 → [2023, 2022, 2021]` |
| `TestTurtleReportFactsHistorical` | 默认 `historical={}`；构造时传入 historical → 字段正确；`to_dict` 包含 historical；嵌套 historical 被 `_copy_historical` strip |
| `TestGetTurtleReportFactsSinglePeriod` | `history_periods=0` 行为与 Spec 1 完全一致（用现有 fixture） |
| `TestGetTurtleReportFactsMultiPeriod` | 3 期全成功 → facts.report.fields + 2 historical；1 期失败 → 1 historical；latest 失败 → fallback 单期路径；全部失败 → empty facts |
| `TestFetchPeriodsConcurrently` | mock adapter 抛异常 → silently drop + log warning；mock 多 worker 完成顺序 |
| `TestMoneyHM3yAvg` | 3 期 reliable → mean 正确；2 期 reliable → mean + caveat "2/3"；1 期 reliable → None + missing；0 期 reliable → None + missing；display_only 跳过；FX 跨期 normalize |
| `TestNumber3yAvg` | 类似覆盖（payout ratio 数值字段） |
| `TestValueAnalystRehydratesHistorical` | `_plain_turtle_report_prompt` 反序列化含 historical 的 ToolMessage payload → 还原成完整 TurtleFacts |
| `TestPrepareTurtleAnalysisPayloadMultiPeriod` | `prepare_turtle_analysis_payload` 透传 `history_periods=2`；输出 JSON 含 `facts.report.historical` |

### 8.3 验收标准

PR 必须满足：

1. `.venv/bin/python -m pytest tests/unit/ -q --ignore=tests/unit/dataflows/test_unified_dataframe.py --ignore=tests/unit/test_stocks_kline_news_api.py` → 全 PASS（基线 380+；Spec 5 新增约 20-30 用例）
2. `.venv/bin/python -m pytest tests/unit/test_turtle_multi_period.py -v` → 单独跑全 PASS
3. **Spec 1 现有测试零失败**：`pytest tests/unit/test_turtle_*.py -v` 全绿（兼容性硬要求）
4. **Spec 1 `prepare_turtle_analysis_payload` 单期路径行为不变**：现有 `test_turtle_value_analyst_integration.py` 全绿
5. `scripts/smoke_test_turtle_value.py --ticker 600519 --market A --holding-channel long_term_domestic` → stdout JSON 含 `facts.report.historical` 字段（可能为空或含 1-2 期数据）
6. 手动 FastAPI 全链路冒烟（PR checklist）：A 股完整分析 → `reports/value_turtle_payload.json` 含 historical 字段
7. Pyright 不引入新 `reportArgumentType` / `reportMissingImports`（pre-existing 不计）

## 9. 改动清单

| 文件 | 改动概要 |
|------|---------|
| `tradingagents/dataflows/value_investment/turtle/facts.py` | `TurtleReportFacts` 加 `historical` 字段；`__post_init__` / `to_dict` 同步；新增 `_copy_historical` helper |
| `tradingagents/dataflows/value_investment/turtle/report_adapter.py` | `get_turtle_report_facts` 加 `history_periods` 参数；提取 `_fetch_single_period_facts` helper；新增 `_derive_historical_period_ends` 与 `_fetch_periods_concurrently` |
| `tradingagents/dataflows/value_investment/turtle/calculations.py` | 新增 `_money_hm_report_3y_avg` 与 `_number_report_3y_avg`（**不**修改现有 R/GG 公式调用） |
| `tradingagents/tools/turtle_analysis_tool.py` | `prepare_turtle_analysis_payload` 调用 `get_turtle_report_facts` 传 `history_periods=2`；`_report_facts` duck-typed 兜底分支补 `historical=getattr(...)` |
| `tradingagents/agents/analysts/value_analyst.py` | `_plain_turtle_report_prompt` 反序列化路径加 `_historical_from_payload` 并填充 `TurtleReportFacts.historical` |
| `tests/unit/test_turtle_multi_period.py` | 新建（主要测试集） |
| `tests/unit/test_turtle_facts.py` | 追加 `TestTurtleReportFactsHistorical` 类 |
| `tests/unit/test_turtle_report_adapter.py` | 追加单期 / 多期 / 失败覆盖测试 |
| `tests/unit/test_turtle_calculations.py` | 追加 `TestMoneyHM3yAvg` / `TestNumber3yAvg` 类 |
| `tests/unit/test_turtle_value_analyst_integration.py` | 追加 `TestValueAnalystRehydratesHistorical` 类 |

## 10. 与后续 Spec 的依赖

- **Spec 2**（model-recalibration）：**强依赖本 Spec 5**。Spec 2 实施时切换 R/GG 公式中 `_money_hm("net_profit", ...)` → `_money_hm_report_3y_avg("net_profit", ...)` 等；重写 M 算法。
- **Spec 3**（data-source-quality）：与 Spec 5 完全独立可并行。Spec 3 完成的承诺支付率字段是 Spec 2 M 算法的可选增强项。
- **Spec 4**（frontend tab）：与 Spec 5 完全独立可并行。Spec 4 渲染 facts JSON 时会顺手看到 `historical` 字段，可在数据 tab 里增加"历史年份对照"视图（Spec 4 设计时决定是否做）。
