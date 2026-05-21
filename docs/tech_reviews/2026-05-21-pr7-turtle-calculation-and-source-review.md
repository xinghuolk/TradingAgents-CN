# PR #7 深度评审：Turtle v0.15 计算合理性与数据源引用

- **PR**：[#7 feat: add Turtle v0.15 value analyst flow](https://github.com/xinghuolk/TradingAgents-CN/pull/7)
- **合并提交**：`ca6fa00`
- **评审日期**：2026-05-21
- **聚焦范围**：`tradingagents/dataflows/value_investment/turtle/calculations.py`、`facts.py`、`report_adapter.py`、`market_adapter.py`
- **配套文档**：`2026-05-21-pr7-turtle-v015-value-analyst-review.md`（综合评审）

> 本文专门评估 **公式与数值口径的合理性** 与 **数据源引用（source_label / source_reference / reliability）的合理性**。常规代码风格、脱敏 bug、签名漂移等问题见配套综合评审。

---

## A. 计算层（`calculations.py`）

### A.1 🔴 时间口径不一致：3 年平均分红率 × 当期净利润 / Owner Earnings

`compute_turtle_signals` 主公式（`calculations.py:333,359`）：

```python
r_value  = (net_profit * payout * (1 - tax_rate) + buyback) / market_cap * 100
gg_value = (owner_earnings * payout * (1 - tax_rate) + buyback) / market_cap * 100
```

- `net_profit` / `owner_earnings`：来自**当期年报**（`infer_turtle_period_end` 推导出的单一报告期）
- `payout` (`M`)：通过 `_number_alias(..., "avg_payout_ratio_3y", "dividend_avg_payout_ratio_3y")` 取 **3 年平均分红率**，或 fallback 到 `_derive_report_payout_proxy` 算的**单年度比例**
- `buyback`：`total_cancelled_amount`，时间窗未文档化（见 `market_adapter._fetch_turtle_buyback_data`）

**问题**：

- 周期股低谷年净利润大幅缩水，套用 3 年平均分红率 → R 严重低估
- 高增长股当期净利润远高于过去 → R 严重高估
- payout proxy fallback 时本身就是单年度的，混入 3 年别名后语义被掩盖（公式仍写 `payout_anchor = avg_payout_ratio_3y`）

**建议**：要么把分子统一到 3 年平均口径（`avg_net_profit_3y`、`avg_owner_earnings_3y`），要么把分母换成当期分红率。当前混用是模型层的口径错配，比代码 bug 更隐蔽，但会系统性扭曲所有跨周期的回报率比较。

### A.2 🟡 分红与回购的税务口径不对称

`R / GG` 公式中：

- 分红部分 `payout × (1 - tax_rate)`：扣预提税
- 回购部分 `+ buyback`：**未扣任何税**

实务上：

- **A 股回购注销**对个人持有者免税，对 QFII 有差异
- **港股回购**对持有人无直接现金流，资本利得在卖出时实现（取决于持有人所在地）
- **美股回购**有 1% Excise Tax（公司层面）与持有人卖出资本利得

代码把回购视作"100% 留存给股东"是**简化假设**，但 prompt 里没有告知 LLM 这条假设。会导致回购大户（如苹果、腾讯）的 R 被相对高估。

**建议**：要么对 `buyback` 也乘 `(1 - q_buyback)`（即使大多数渠道 q_buyback=0，也要显式声明），要么在 `build_turtle_decision_prompt` 的「公式核对」里加一段 `"buyback assumes 100% pass-through; cross-check holding_channel"` 提示。

### A.3 🟡 跨市场公司的 FX 通道未打通

`_money_target_currency` 的策略是"相关字段币种**只有一种**就用它，否则 fallback `CNY`"（`calculations.py:80-84`）：

```python
def _money_target_currency(facts: TurtleFacts, names: Iterable[str]) -> str:
    currencies = _money_fact_currencies(facts, names)
    if len(currencies) == 1:
        return next(iter(currencies))
    return "CNY"
```

实际场景：H 股公司（如中海油 0883.HK）年报用 CNY 披露，市值在港交所是 HKD：

- `net_profit.currency = "CNY"`，`market_cap.currency = "HKD"`
- → `r_target_currency = "CNY"`
- → `_money_hm("market_cap", target="CNY")` → `MoneyAmount.to_hundred_million` 需要 `fx_rates["HKD:CNY"]`
- → FX 来源是 `facts.report.metadata.get("fx_rates")`
- → **`report_adapter.build_report_facts_from_extraction` 写的 metadata 只有 `company / market / period_end / catalog_version`，没有 `fx_rates`**

**后果**：跨币公司一定会因为 `FX rate required for HKD:CNY` 触发降级，整条 R/GG 链跌到 `non_decisionable`。

**建议**：

1. 由 `market_adapter` 在拉取 quote 时同步取当日 FX，写入 `facts.report.metadata["fx_rates"]`（语义略奇怪，建议把 metadata 提升到 facts 顶层）
2. 或强约束「同一公司必须同币种披露」，跨币情况直接当 unsupported

另外，`MoneyAmount.to_hundred_million` 对 FX pair 方向没有任何校验：约定 `"HKD:CNY"` 表示 `1 HKD = X CNY`，但如果调用方写反，代码不会发现。建议在 `facts.py` 顶部明确这条约定。

### A.4 🟡 三处死代码：`elif not _critical_missing` 分支永远走不到

`r_market_cap` 经过 `_validate_positive_market_cap` 之后只可能是：

- 严格正数（missing 为空）
- `None`（missing 非空，含 `"market_cap"`）

所以 `r_critical_missing` 为空 ⟹ `r_market_cap > 0` ⟹ `r_market_cap != 0` 必然真。

下列三处分支不可达：

```python
# calculations.py:344-346
elif not r_critical_missing:
    r_critical_missing = ["market_cap"]
    r_missing = _merge_missing(r_critical_missing, r_degraded_buyback_missing)

# calculations.py:370-372
elif not gg_critical_missing:
    gg_critical_missing = ["market_cap"]
    gg_missing = _merge_missing(gg_critical_missing, gg_degraded_buyback_missing)

# calculations.py:411-412
elif not net_cash_missing:
    net_cash_missing = ["market_cap"]
```

不影响正确性，但会让读者怀疑分支边界。建议删掉，让 `_validate_positive_market_cap` 成为单一的市值守卫。

### A.5 🟡 `ev_switch` / `cash_protection` 的 `degraded` 分支永远不会被触发

```python
# calculations.py:431-449
ev_missing = list(results["net_cash_ratio"].missing_inputs)
ev_value = None if ev_missing else (1.0 if net_cash_ratio > 40 else 0.0)
ev_status = (
    "non_decisionable" if "market_cap" in ev_missing
    else "degraded" if ev_missing       # ← 这条
    else "complete"
)
```

但 `net_cash_ratio` 自己的逻辑是：**只要 `net_cash_missing` 非空就不计算**（`calculations.py:408`），因此 `net_cash_ratio.value` 在任何 `missing` 情况下都是 `None`。

结果：

- `ev_missing` 非空 → `ev_value = None` → 输出"无法判断"
- `ev_missing` 非空且不含 market_cap 的状态被标成 `degraded`，但 value 仍是 None
- LLM 看到 `status=degraded` 会误以为有部分可用结果，而实际上没有

实际有"degraded"语义的情形应该是：`cash` 或 `debt` 缺失但市值 OK 时，仍能用"假设缺失项 = 0"输出一个保守估计。当前代码没这么做。

**建议**：要么让 `net_cash_ratio` 在 cash/debt 缺失时退化为单边计算（带 caveat），要么把 ev_switch / cash_protection 的 `degraded` 分支删掉避免误导 LLM。

### A.6 🟢 `owner_earnings = ocf - abs(capex)` 的符号兼容

`abs(capex)` 是为了兼容 capex 数据源既可能给负号（cash flow statement 惯例）也可能给正号（"支出额"语义）。这层兼容**实务上合理但需要文档化**——尤其是 LLM 在引用 substitution `f"{ocf} - abs({capex})"` 时会看到「`abs(-100)`」这种表达，可能困惑。建议在 prompt 旁加一句"capex 按绝对值参与"。

### A.7 🟢 `payout_anchor` 只是别名，不是公式

```python
# calculations.py:300-313
results["payout_anchor"] = _result(
    name="payout_anchor",
    formula="payout_anchor = avg_payout_ratio_3y",
    ...
)
```

与其他 `R/GG/HH/net_cash_ratio` 同级出现，LLM 可能把它当独立指标。建议要么去掉（直接读 fact），要么改名为 `payout_anchor_passthrough` 让读者意识到不是计算结果。

---

## B. 数据源引用层

### B.1 🔴 `tax_rate` 在默认 holding_channel 下仍标记 `reliable`

`market_adapter.py:230-244`：

```python
tax_rate_known = _is_known_tax_rate_combination(market, active_channel)
tax_rate_unknown_caveat = None
tax_rate_reliability = "reliable"
if not tax_rate_known:
    tax_rate_unknown_caveat = f"tax_rate unknown for {market}:{active_channel}"
    tax_rate_reliability = "display_only"
    ...

fields["tax_rate"] = _field(
    "tax_rate",
    default_tax_rate(market, active_channel),
    "holding_channel.default_tax_rate",
    caveat=tax_rate_unknown_caveat or DEFAULT_CHANNEL_CAVEAT,
    reliability=tax_rate_reliability,  # ← 仍是 reliable
)
```

`active_channel = holding_channel or default_holding_channel(market)` —— 当 UI 没传 holding_channel 时，使用默认渠道（A 股→`long_term_domestic`，港股→`stock_connect`）。这时 `_is_known_tax_rate_combination` 返回 True，所以 `reliability` 保持 `"reliable"`，**只挂一条 caveat**：`DEFAULT_CHANNEL_CAVEAT`。

然后 `compute_turtle_signals._is_reliable_fact` 只看 `reliability`：

```python
def _is_reliable_fact(fact, name, caveats):
    if fact.reliability != "reliable":
        _append_caveat(caveats, f"{name} unreliable: {fact.reliability}")
        return False
    return True
```

→ caveat 进入 facts.caveats，但 R/GG 仍然用这个**猜测出的** tax_rate 算出来一个**带"complete"状态的值**，然后整体 `signals.status` 只会因 `has_input_caveats=True` 走到 `degraded`，而不是 `non_decisionable`。

LLM 看到 `R.status=degraded`（或 `complete` 当其他输入都干净时），容易把"用了默认 holding_channel"的事实忽略掉。

**建议**：默认 holding_channel 时把 `tax_rate.reliability` 设为 `display_only`（或新增 `assumed`），让 `_is_reliable_fact` 直接拒绝它，迫使 R/GG 进入 `non_decisionable`，直到上游显式选择 holding_channel。这才符合 CLAUDE.md 里多次强调的「不可决策优先」。

### B.2 🟡 市场数据的 `source_reference` 是字段路径，没有 provider 信息

`market_adapter.py:217, 275, 361`：

```python
MoneyAmount(value=..., currency=..., unit="yuan",
            source_label="market-adapter",
            source_reference="market_data.market_cap")  # 字段路径
MoneyAmount(..., source_reference="buyback_data.total_cancelled_amount")
```

而 `_fetch_hk_market_data` 实际是从 yfinance 拉的（`market_adapter.py:76-102`）：

```python
return {
    "market_cap": ...,
    ...
    "source": info.get("source"),   # ← "yfinance_hk"，但被丢弃了
}
```

`info["source"]` 拿到了（如 `"yfinance_hk"`、`"akshare"`、`"tushare_pro"`），但**没有写入 source_reference**。

LLM 输出报告时引用「市值来源：market_data.market_cap」对用户没意义——用户想知道是 yfinance 还是 akshare、哪一天的快照。

对比 report_adapter 的做法（带 PDF 页码）：`"net_profit p.45"`——清晰且可核对。

**建议**：把 source_reference 拼成 `"{provider}:{field_path}@{timestamp}"`，例如 `"yfinance_hk:market_cap@2026-05-21T08:30Z"`。或至少把 `info["source"]` 透传到 source_label，让计算层带出来。

### B.3 🟡 `_derive_report_payout_proxy` 的 `dividend_avg_payout_ratio_3y` 是单年度比例但用了 3 年别名

`report_adapter.py:237-244`：

```python
fields["dividend_avg_payout_ratio_3y"] = TurtleFactValue(
    name="dividend_avg_payout_ratio_3y",   # ← 名字含 "3y"
    value=round(ratio, 12),                  # ← 实际是单年度
    ...
    reliability="reliable",
    caveat=PAYOUT_PROXY_CAVEAT,              # "single-year report payout proxy; not a 3-year average"
)
```

`caveat` 已经声明了真实语义，但**字段名**仍叫 `dividend_avg_payout_ratio_3y`，下游 `_number_alias("avg_payout_ratio_3y", "dividend_avg_payout_ratio_3y")` 把它当 3y 取用，**且 `reliability="reliable"`**。

`compute_turtle_signals._is_reliable_fact` 只看 reliability，不读 caveat。计算照旧执行，输出的 `R.sources` 里会带这个 source_reference，但 LLM 不一定逐条读完 caveat。

**风险**：单年度分红率（尤其周期股或刚开始分红的公司）波动剧烈，用它代理 3y 平均会让 R/GG 显著失真，但状态仍是"complete"。

**建议**：单年度代理的 `reliability` 应设为 `"display_only"`，或新增 `"approximate"` 一档，让计算层在不可用时显式降级，而不是悄悄进入"看起来 complete"的状态。

### B.4 🟢 R/GG `source_reference` 在 FX 转换后会拼接 FX 记录

`facts.py:84` 在 FX 转换时：

```python
source_reference = f"{source_reference}; FX {pair}={rates[pair]}"
```

这是可取的——后续 `FormulaResult.sources` 里会看到 `"market_data.market_cap; FX HKD:CNY=1.0991"`，可追溯。✓

但 FX rate 本身没有 source_label（哪儿来的、什么时间点的），调用方只能放裸 dict。建议在 `facts.report.metadata` 里同时存 `{"fx_rates": {...}, "fx_provider": "ccb_quotation", "fx_timestamp": "..."}`，并把这些信息也拼进 source_reference。

### B.5 🟢 `FormulaResult.sources` 列表偏长

例如 `GG.sources = owner_sources ∪ payout_sources ∪ tax_sources ∪ gg_buyback_sources ∪ gg_market_cap_sources`，其中 `owner_sources` 又是 `ocf_sources ∪ capex_sources`。最终一条公式可能列出 5–6 条 source_reference，对 LLM 是好事（可一一引用），但**对人工阅读 markdown JSON 时偏长**。可考虑：

- 在 prompt 的「公式核对」要求 LLM 至少引用一条最具体的源
- 或在 `formatting.py` 里对源做 dedup + 截断

非阻塞，但能提升报告可读性。

---

## C. 数据源整体架构

回答一个上层问题：**Turtle 的数据是否主要来自 `financial-report-llm-extractor`？**

**结论：是的——但仅限"年报口径"的财务字段。市场快照与分红/回购历史走的是另一套数据通道。**

### C.1 来源分工

**`report_adapter.py`（PDF 抽取年报）** 通过 `tradingagents.dataflows.financial_reports.adapter` 调用 `financial_report_llm_extractor.client.FinancialReportClient`：

```python
# tradingagents/dataflows/financial_reports/adapter.py:35
from financial_report_llm_extractor.client import (
    ExtractorConfig, ExtractorError, FinancialReportClient, RefreshPolicy,
)
```

`get_turtle_report_facts` 通过 `create_financial_report_adapter(config).get_annual_report_data(...)` 调用 `FinancialReportClient`，再由 `build_report_facts_from_extraction` 把字段映射到 Turtle facts。字段别名（`report_adapter.py:21-26`）：

| extractor 字段 | Turtle 字段 | 用途 |
|----------------|-------------|------|
| `net_profit` | `net_profit` | R 公式分子 |
| `operating_cash_flow` | `operating_cash_flow` | owner_earnings |
| `capital_expenditures` | `capex` | owner_earnings |
| `cash_and_equivalents` / `money_cap` | `cash` | net_cash_ratio |
| `interest_bearing_debt` | `interest_bearing_debt` | net_cash_ratio |
| `dividends_paid` | `dividends_paid` | payout proxy |

**这些字段全部来自 `financial-report-llm-extractor` 的 PDF 抽取结果**（带 `field_id` + `evidence_page` 页码引用）。

**`market_adapter.py`（旧路径，不依赖 extractor）**：

| Turtle 字段 | 数据源 |
|-------------|--------|
| `market_cap` / `close_price` / `total_shares` / `industry` | A 股：`tools.value_investment_tool._fetch_market_data_structured`（akshare/tushare）。港股：`dataflows.providers.hk.hk_stock.get_hk_stock_info`（yfinance） |
| `dividend_avg_payout_ratio_3y` / `dividend_records` | `value_investment_tool._fetch_dividend_data_sync`（A 股；港股已显式跳过） |
| `buyback_amount` / `buyback_records` | `value_investment_tool._fetch_buyback_data_sync`（同上） |
| `tax_rate` / `holding_channel` / `rf_rate` | 内置常量 + 环境变量（`TURTLE_RF_RATE_CN` / `TURTLE_RF_RATE_HK`） |

### C.2 一句话总结

**核心年报口径数据（损益、现金流、资产负债关键科目）由 `financial-report-llm-extractor` 抽取，可靠性高、有 PDF 页码引用；市场快照与历史分红/回购仍走旧的 akshare / yfinance 通道，可靠性参差且 source_reference 只是字段路径（见 §B.2）。**

这解释了为什么本文档 §B 里反复出现"PDF p.45"这种引用格式——它只出现在 report 来源，市场来源给不出这种粒度。**两套来源之间存在明显的可追溯性落差**，是 §B.2 的根因。

---

## D. 中间展示页提案

回答另一个上层问题：**最终报告是否应该有"展示数据 + 计算值"的中间页？**

**强烈建议：应该有。这是当前架构的明显 UX 缺口，而且补齐成本很低——数据已经在 backend 内部以结构化形式存在，只是没透传到 UI。**

### D.1 当前状态

`value_analyst_node` 返回值：

```python
return {
    "value_report": report_content or "",     # ← 只有 LLM 渲染的 markdown
    "value_tool_call_count": tool_call_count,
}
```

下游路径：

- `graph.propagation.py:52` → `final_state["value_report"]`
- `graph.trading_graph.py:870` → 复制进 propagator 输出
- `app/services/simple_analysis_service.py:2796` → 持久化为 `value_report.md`，前端按 markdown 文件渲染

**`TurtleFacts` / `TurtleComputedSignals` 的结构化数据只在 ToolMessage 里短暂存在**（被 `_plain_turtle_report_prompt` 反序列化并塞进 LLM prompt），之后就丢了。用户在前端看到的只有 LLM 写出来的散文，没有：

- 每个事实的 `source_label` / `source_reference` / `reliability` / `caveat`
- 每个公式的 `formula` / `substitution` / `value` / `unit` / `missing_inputs` / `status`
- `facts.status` 与 `signals.status` 的真实状态标签

### D.2 为什么这是个真问题

**1. 与框架设计哲学冲突。** Turtle v0.15 的核心卖点是"事实先行、计算先行、不可决策优先"——`docs/superpowers/specs/2026-05-19-turtle-v015-flow-layer-design.md` 反复强调这点。LLM 散文渲染恰恰是这个哲学的最薄弱环节：

- LLM 可能省略 source_reference（prompt 要求"逐项引用"，但实际依从度无保证）
- LLM 可能把 `degraded` / `non_decisionable` 软化成"数据较完整"之类的措辞
- LLM 看到 `R.value=null, status=non_decisionable` 时仍可能用其他段落算出的数字"补完"判断

**2. 审计可追溯性。** PDF 抽取场景下 `source_reference="net_profit p.45"` 是真正的可核对凭证——但这个信息只在 prompt 里，没在最终报告里以结构化形式落盘。一旦报告写完，PDF 页码就丢了。

**3. 数据已经在那儿。** `prepare_turtle_analysis_payload` 返回的 JSON 是完整的事实/信号；只需要把它和 `value_report` 一起持久化即可，无需重新计算。

### D.3 推荐方案

最小代价的改造：

**Backend（Apache 2.0 部分）**

1. `value_analyst_node` 同时返回 `value_turtle_payload`（保留原 JSON）：

   ```python
   return {
       "value_report": report_content or "",
       "value_turtle_payload": turtle_payload,   # ← 新增，已是 JSON 字符串
       "value_tool_call_count": tool_call_count,
   }
   ```

2. `graph.propagation.py:52` 与 `graph.trading_graph.py:870` 把新字段透传到 final_state。

3. `app/services/simple_analysis_service.py:2796` 附近新增持久化目标：

   ```python
   'value_turtle_payload': {
       'filename': 'value_turtle_payload.json',
       'state_key': 'value_turtle_payload',
       'content_type': 'application/json',
   }
   ```

**Frontend（proprietary 部分，需评估改动成本）**

在价值分析师的报告 Tab 旁加子 Tab：

| Tab | 内容 |
|-----|------|
| **报告**（默认） | 当前 LLM 渲染的 markdown |
| **数据**（TurtleFacts） | 表格：字段名 / 值 / 单位 / 货币 / 可靠性 / 来源 / caveat；按 report / market 分组；可点击 PDF 页码定位 |
| **计算**（TurtleComputedSignals） | 表格：公式 / 代入式 / 数值 / 单位 / 状态 / 缺失输入 / 来源列表 |
| **状态**（顶部状态条） | facts.status + signals.status 高亮显示；不可决策时显著标红 |

**渐进式 fallback**：在 frontend 改造完成前，先把 `value_turtle_payload.json` 作为附件落盘，让高级用户直接看 JSON——基础设施先打通，UI 再迭代。

### D.4 与本文其他条目的协同收益

中间页能直接缓解本文及综合评审中多个问题：

| 关联条目 | 中间页如何缓解 |
|----------|---------------|
| 综合评审 §2.2 `facts.status` 硬编码 complete | 状态条直接显示真实 status，用户能立刻看到契约漂移 |
| §B.1 `tax_rate` 默认渠道仍 reliable | 数据表格上"tax_rate / reliable / caveat: tax_rate uses default holding_channel..."一目了然 |
| §B.2 市场 source_reference 缺 provider | 来源列暴露后会立刻被用户反馈"这个 market_data.market_cap 到底是哪儿来的"，倒逼修正 |
| §B.3 单年度 payout proxy 假装 3y | 数据表格里 `dividend_avg_payout_ratio_3y / caveat: single-year report payout proxy` 一行就把伪装拆穿 |
| §A.1 时间口径不一致 | 把 payout 的来源年份与净利润的报告期同表展示，时间口径不匹配会立刻可见 |

**换句话说，中间页本身就是 Turtle 框架"事实先行"理念在 UI 层的兑现。** 没有它，前面所有 reliability / caveat / source_reference 设计的可观测性都依赖 LLM 善意——而 LLM 不应该是审计层。

---

## E. 总结

| ID | 类别 | 严重度 | 影响 |
|----|------|--------|------|
| A.1 | 时间口径不一致（3y payout × 当期净利润） | 🔴 模型层失真 | 跨周期 R/GG 系统性偏差 |
| A.2 | 分红/回购税务口径不对称 | 🟡 设计假设 | 回购大户的 R 偏高 |
| A.3 | 跨币 FX 通道未打通 | 🟡 实战阻塞 | H 股 + CNY 报表公司直接降级 |
| A.4 | 三处死代码 | 🟡 维护性 | 误导阅读 |
| A.5 | `ev_switch` / `cash_protection` 的 degraded 不可达 | 🟡 状态语义 | 错误的状态标签 |
| A.6 | `abs(capex)` 符号兼容 | 🟢 文档化 | LLM 输出可能困惑 |
| A.7 | `payout_anchor` 别名混入公式表 | 🟢 命名 | 易被当独立指标 |
| B.1 | `tax_rate` 默认渠道下仍 reliable | 🔴 可决策性破坏 | 默认渠道悄悄进入"complete" |
| B.2 | 市场 source_reference 缺 provider | 🟡 可追溯性 | 输出报告无法定位上游 |
| B.3 | 单年度 payout proxy 仍 reliable | 🟡 reliability 漂移 | R/GG 在单年度数据下假完整 |
| B.4 | FX 元数据缺 provider/timestamp | 🟢 可追溯性 | FX 难以核对 |
| B.5 | sources 列表偏长 | 🟢 可读性 | 报告噪声 |

## F. 优先级建议

最值得优先处理的两个：

1. **A.1（时间口径不一致）**——模型层的口径错配，所有 R/GG 数字都受影响，比代码 bug 危害更大
2. **B.1（默认渠道下的 `tax_rate.reliability`）**——直接违反框架"不可决策优先"的设计理念，让默认值悄悄混入"complete"判断

紧随其后：

3. **D.3（中间展示页 backend 部分）**——透传 `value_turtle_payload` 是低风险高收益的改造，能让上述所有问题在生产环境立刻可见，且不依赖前端改动
4. **A.3 跨币 FX 通道未打通**——影响 H 股可用性
5. **B.3 单年度 payout proxy 假装 3y**——影响新分红/周期股的 R 准确度

死代码与命名问题（A.4 / A.7）可在一次清扫 PR 中一起处理，零风险。

中间展示页前端部分（D.3 frontend tab）成本中等，可独立排期。
