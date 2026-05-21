# Turtle Correctness Fixes 设计文档

- **创建日期**：2026-05-21
- **工作分支**：`fix/turtle-v015-review-followups`
- **Spec 编号**：Spec 1（于 4-spec 路线图）
- **路线图**：`docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md`
- **问题来源**：
  - `docs/tech_reviews/2026-05-21-pr7-turtle-v015-value-analyst-review.md`
  - `docs/tech_reviews/2026-05-21-pr7-turtle-calculation-and-source-review.md`

## 1. 目标

在 **不改变 Turtle 金融模型口径**（A.1 / A.2 留给 Spec 2）和 **不引入新数据通道**（A.3 留给 Spec 3）的前提下，把当前已存在的语义错误、可观测性缺口和死代码全部清理，让 Turtle v0.15 框架"事实先行、不可决策优先"的设计在运行时真正兑现。

## 2. 范围

### 2.1 范围内（10 项）

| 来源 | 条目 | 形态 |
|------|------|------|
| 综合 2.1 | 删除 `_safe_non_decision_text` redaction | 删代码 |
| 综合 2.2 | `facts.status` 由 adapter 上报 + 聚合 | 架构变更 |
| 综合 2.3 | 对齐 `prepare_turtle_analysis` 工具签名 | 修类型 |
| 计算 A.4 | 删 `calculations.py` 三处死代码 | 删代码 |
| 计算 A.5 | 删 `ev_switch` / `cash_protection` 不可达 degraded 分支 | 删代码 |
| 计算 A.6 | 在 decision prompt 加 `abs(capex)` 说明 | 文档 |
| 计算 B.1 | 默认 `holding_channel` 时 `tax_rate.reliability = display_only` | 语义修复 |
| 计算 B.1（附） | 显式 `holding_channel` 时停止追加 `DEFAULT_CHANNEL_CAVEAT` | 语义修复 |
| 计算 B.3 | payout proxy 重命名 + 降级 + 修复 key 撞车 | 语义修复 |
| D 章 | backend 透传 `value_turtle_payload`（原样 JSON） | 数据流扩展 |

### 2.2 范围外（明示推迟）

- A.1 时间口径不一致 → Spec 2
- A.2 分红 / 回购税务口径 → Spec 2
- A.3 跨币 FX 通道 → Spec 3
- A.7 `payout_anchor` 重命名 → Spec 2（避免连续重命名干扰）
- B.2 市场 source_reference 缺 provider → Spec 3
- B.4 / B.5 → Spec 3 / Spec 4
- frontend tab 与 payload API 端点 → Spec 4

## 3. 核心设计决策

1. **Reliability / status 哲学**：Fail-fast 严格不可决策优先。任何不确定性（默认 holding_channel、单年度 proxy、关键字段缺失）都让公式或聚合状态降级到 `non_decisionable`，不依赖 LLM 善意读 caveat。
2. **`facts.status` 派生**：Adapter-emitted。`TurtleReportFacts` 与 `TurtleMarketFacts` 各自携带 status；`prepare_turtle_analysis_payload` 通过 `merge_status` 聚合。不维护"critical fields 白名单"，per-formula 判定仍归 `compute_turtle_signals`。
3. **Redaction**：完全删除。护栏留在 `build_turtle_decision_prompt` 的系统提示词层面。
4. **Payload 透传形式**：原样 `{facts, signals}` JSON，不加 wrapper metadata。
5. **payout proxy**：重命名为 `dividend_payout_ratio_proxy_single_year`，reliability=display_only，**保留在 facts 供 UI 展示但不进入计算**；附带消除 report-side 与 market-side 同 key 撞车 bug。
6. **测试策略**：双轨——happy-path 显式传 `holding_channel`，新增 fail-fast 专属测试覆盖默认路径。

## 4. 架构变更

### 4.1 状态枚举聚合工具

`tradingagents/dataflows/value_investment/turtle/facts.py` 顶部新增：

```python
_STATUS_RANK: dict[TurtleStatus, int] = {
    "complete": 0,
    "degraded": 1,
    "non_decisionable": 2,
    "unsupported": 3,
}

def merge_status(*statuses: TurtleStatus) -> TurtleStatus:
    """Return the most severe status across the inputs."""
    return max(statuses, key=lambda s: _STATUS_RANK[s])
```

### 4.2 Adapter 数据结构扩展

`TurtleReportFacts` 与 `TurtleMarketFacts` 各加 status 字段（保持 frozen dataclass）：

```python
@dataclass(frozen=True)
class TurtleReportFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    status: TurtleStatus = "complete"

    # 其余 __post_init__ / to_dict 保持，to_dict 增加 status 序列化

@dataclass(frozen=True)
class TurtleMarketFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    status: TurtleStatus = "complete"
```

`to_dict()` 同步把 status 写入字典输出，让前端 / LLM 都看得到分源状态。

### 4.3 Adapter status 派生规则

**统一规则**（不维护字段白名单）：

- `extraction is None`（report adapter）或 `market_data is None and dividend_data is None and buyback_data is None`（market adapter）→ `non_decisionable`
- **adapter 适配后 `fields` 仍为空**（如 `extraction.fields` 不是 dict、所有字段被 policy 拒绝、market_cap 等所有可识别字段全失败）→ `non_decisionable`。注：仅有 `tax_rate` / `holding_channel` 这类"内置常量字段"不算实际数据采集成功
- 任一 caveat（含 adapter 级 `caveats: list[str]` 与字段级 `TurtleFactValue.caveat`）或任一字段 `reliability != "reliable"` → `degraded`
- 全字段 reliable 且 adapter / 字段两层 caveat 都为空 → `complete`
- 来源整体不支持（如某市场 extractor 未集成）→ `unsupported`

**实现提示**：

- report adapter：在 `build_report_facts_from_extraction` 末尾，如果 `adapted` dict 为空，强制 status=`non_decisionable`，其他规则按上述判
- market adapter：内置常量字段（`tax_rate` / `holding_channel` / `rf_rate`）单独识别，判定时只看"是否拿到外部数据字段"（market_cap、close_price、dividend records 等）。具体白名单常量放在 `market_adapter.py` 顶部

实现位置：

- `build_report_facts_from_extraction`（`report_adapter.py:248`）末尾派生 status 后构造 `TurtleReportFacts`
- `build_market_facts`（`market_adapter.py:189`）末尾派生 status 后构造 `TurtleMarketFacts`

### 4.4 聚合点

`tradingagents/tools/turtle_analysis_tool.py:62-69` 的硬编码 `status="complete"` 改为：

```python
facts = TurtleFacts(
    context=context,
    report=report,
    market=market_facts,
    status=merge_status(report.status, market_facts.status),
    caveats=[*report.caveats, *market_facts.caveats],
)
```

`compute_turtle_signals`（`calculations.py:471-481`）的 `facts.status == "non_decisionable"` 检查已存在，现在会真实生效，**本身代码不动**。

## 5. B.1 + B.3 Fail-fast 落地

### 5.1 B.1：`tax_rate` 默认渠道降级

**问题**：`turtle_analysis_tool.py:48-62` 通过 `TurtleRunContext.for_ticker` 把 `holding_channel=None` 解析为默认值，然后传 `context.holding_channel` 给 `get_turtle_market_facts`。`build_market_facts` 拿到的永远是字符串，无法区分"显式传入"与"默认推断"。

**修复**：让 market adapter 拿到原始 `holding_channel`（None 或显式值），自行判断：

```python
# turtle_analysis_tool.py:62
market_facts = get_turtle_market_facts(
    ticker=ticker, market=market,
    holding_channel=holding_channel,   # ← 原始参数，不再走 context
)
```

`context.holding_channel`（解析后的字符串）只服务于 prompt 显示与 channel 标签。

`build_market_facts`（`market_adapter.py:189`）内部：

```python
# 空串视同未传，避免上游不小心传 "" 绕过 fail-fast
channel_is_explicit = bool(holding_channel and holding_channel.strip())
active_channel = (holding_channel.strip() if channel_is_explicit else None) \
                 or default_holding_channel(market)

tax_rate_known = _is_known_tax_rate_combination(market, active_channel)
if not tax_rate_known:
    tax_rate_reliability = "display_only"
    tax_rate_caveat = f"tax_rate unknown for {market}:{active_channel}"
elif not channel_is_explicit:
    tax_rate_reliability = "display_only"
    tax_rate_caveat = (
        f"tax_rate uses default holding_channel '{active_channel}' for {market}; "
        "pass holding_channel explicitly to enable computation"
    )
else:
    tax_rate_reliability = "reliable"
    tax_rate_caveat = None

# tax_rate 字段写入（caveat 已携带必要说明）
fields["tax_rate"] = _field(
    "tax_rate",
    default_tax_rate(market, active_channel),
    "holding_channel.default_tax_rate",
    caveat=tax_rate_caveat,
    reliability=tax_rate_reliability,
)

# ⚠️ 关键修改（B.1 附）：删掉原 market_adapter.py:245 的无条件
# `_append_caveat(caveats, DEFAULT_CHANNEL_CAVEAT)`。理由：
# - 显式 channel 时不该有这条 caveat（否则 market.status 永远 degraded）
# - 默认 channel 时，tax_rate_caveat 本身已经携带等价说明，无需在 adapter 级
#   caveats 列表里重复
# 同时 DEFAULT_CHANNEL_CAVEAT 常量可以删除（最后一处使用消失）。
```

**下游效果**（fail-fast 真实链路，澄清 §3 决策 1 的实现）：

- 默认 channel 时 `tax_rate.reliability == "display_only"`
- `compute_turtle_signals._is_reliable_fact(tax_rate, ...)`（`calculations.py:35-39`）判定不可靠 → 写入 calculations 内部的 caveats → skip
- `missing_tax = ["tax_rate"]`
- R / GG `critical_missing` 含 `"tax_rate"` → R/GG status = `non_decisionable`
- signals 顶层 status 经 `critical_non_decisionable` 判定为 `non_decisionable`

**注意路径分层**：fail-fast 是经 **calculations 层** 的 reliability filter 落实的，**不是**经 `facts.status` 直接派生。按 §4.3 规则，display_only 字段只把 `market.status` 推到 `degraded`；non_decisionable 由 signals 层最终决定。两条路径职责分明：

- `facts.status`：表示"数据采集结果是否有阴影"——caveat / display_only 即 `degraded`
- `signals.status`：表示"基于这套数据公式能不能决策"——critical 字段不可靠即 `non_decisionable`

### 5.2 B.3：payout proxy 重命名 + 降级 + 修复撞车

**已发现的撞车 bug**：

- `market_adapter.py:274` 写 `dividend_avg_payout_ratio_3y`（真 3 年平均，reliable）
- `report_adapter.py:237` 写同 key（**单年度** proxy，曾经 reliable）
- `calculations._field` 在 `report.fields` 与 `market.fields` 上取值时 **report 优先**
- 结果：如果 report adapter 算出 proxy，会**静默覆盖** market adapter 的真 3 年数据

**修复**：

1. `report_adapter.py` 顶部新增：
   ```python
   PAYOUT_PROXY_FIELD = "dividend_payout_ratio_proxy_single_year"
   ```

2. `_derive_report_payout_proxy` 写入改为：
   ```python
   fields[PAYOUT_PROXY_FIELD] = TurtleFactValue(
       name=PAYOUT_PROXY_FIELD,
       value=round(ratio, 12),
       source_label="financial-report-client",
       source_reference=f"{dividend.source_reference}; {profit.source_reference}",
       reliability="display_only",
       caveat=PAYOUT_PROXY_CAVEAT,
   )
   ```

3. `_derive_report_payout_proxy` 顶部的"已存在则跳过"检查改为读新 key：
   ```python
   if _is_reliable_numeric_field(fields.get(PAYOUT_PROXY_FIELD)):
       return
   ```
   **注**：因为新 proxy 的 `reliability="display_only"`，`_is_reliable_numeric_field`
   永远返回 False，这条早返实际上不会触发。逻辑上无害（最多被多算一次，结果一致），
   保留它仅为防御性编程。可在 plan 阶段决定是否改为 `fields.get(PAYOUT_PROXY_FIELD)
   is not None` 的存在性检查，或直接删除。Spec 暂保留以减少改动面。

4. `calculations.py:292-297` 的 `_number_alias` **保持原状**——`dividend_avg_payout_ratio_3y` 仍是合法的 fallback 候选（market adapter 真 3 年数据），不删。proxy 改名后自然不再被 `_number_alias` 拾起。

**效果**：

- 真 3 年数据（market）：reliable，按现有逻辑进入 R/GG
- 单年度 proxy（report）：display_only，命名诚实，UI 数据页可展示，不进入计算
- 撞车 bug 自然消失（不同 key）

## 6. Backend Payload 透传链路

### 6.1 LangGraph state schema 扩展

`tradingagents/agents/utils/agent_states.py:67` 的 `AgentState` TypedDict 在 `value_report` 旁新增：

```python
class AgentState(MessagesState):
    ...
    value_report: Annotated[str, "Report from the Value Investment Analyst"]
    value_turtle_payload: Annotated[
        str,
        "Raw {facts, signals} JSON payload produced by prepare_turtle_analysis",
    ]
    ...
```

**重要**：LangGraph TypedDict 是 reducer 派生的 state schema 来源，不在这里加字段会导致后续节点读 `state["value_turtle_payload"]` 时类型契约不全，工具链各处类型检查失稳。这是 spec 必改项。

### 6.2 LangGraph InitialState 新字段

`tradingagents/graph/propagation.py:52` InitialState 平行新增：

```python
"value_report": "",
"value_turtle_payload": "",
```

### 6.3 `value_analyst_node` 返回 payload（所有路径）

`tradingagents/agents/analysts/value_analyst.py`：节点开始处获取 `turtle_payload = _latest_turtle_tool_payload(messages) or ""`。**所有写入 `value_report` 的 return 路径都带 `"value_turtle_payload": turtle_payload`**（即使值为 `""`），形成统一不变量。触发 ToolNode 的中间返回不写 payload key——LangGraph state merge 保留已有值（即 InitialState 设的 `""`）。

| 路径 | `value_report` | `value_turtle_payload` |
|------|----------------|----------------------|
| 美股 unsupported | `"美股 {ticker} 暂不支持..."` | `""`（无工具调用） |
| Turtle 路径成功 | LLM 生成的 markdown | 原 JSON 字符串 |
| Turtle 路径异常 | 错误说明 | 原 JSON 字符串（事后排查用） |
| LLM 触发 ToolNode（中间态） | 不写 | 不写（state merge 保留旧值） |

### 6.4 LangGraph 透传

**无需额外改动**。`trading_graph.py:388` 的 `propagate()` 直接返回 LangGraph 自动传播的 `final_state`；只要 `AgentState`（§6.1）与 InitialState（§6.2）都包含 `value_turtle_payload` 字段，state 自动透传到 `final_state` 给下游消费者。

可选附加（plan 阶段决定是否做）：`trading_graph.py:861` 的 `_log_state` 写日志文件时同步把 `value_turtle_payload` 加进去（便于事后排查）。这是日志增强，不影响主流程：

```python
# trading_graph.py:861-870 附近的 _log_state（可选）
"value_report": final_state.get("value_report", ""),
"value_turtle_payload": final_state.get("value_turtle_payload", ""),  # ← 可选
```

### 6.5 持久化

`app/services/simple_analysis_service.py:2796` 附近的 `report_modules` 字典新增条目：

```python
'value_turtle_payload': {
    'filename': 'value_turtle_payload.json',
    'title': '价值投资 Turtle payload',
    'state_key': 'value_turtle_payload',
},
```

**关于空 payload 的处理**：核查现状（`simple_analysis_service.py:2829-2849`）发现现有循环 **不跳过空串**——它只检查 `if state_key in state`，空内容仍会写 0 字节文件。

**统一契约**：

- `value_analyst_node` **始终**返回 `"value_turtle_payload"` 键（即使值为 `""`），与 §6.3 的"所有 return 路径都带"一致
- 美股 unsupported / 工具未调用等路径 → 值为 `""`
- Turtle 路径成功 / Turtle 路径异常 → 值为原 JSON 字符串
- 持久化层（`simple_analysis_service.py:2832`）加"内容非空"短路负责过滤——空串不落盘

这样上游契约简单（始终有键），落盘策略集中在一处，行为可单元测试。具体改动：

```python
# simple_analysis_service.py:2829-2832 改动
for module_key, module_info in report_modules.items():
    try:
        state_key = module_info['state_key']
        if state_key not in state:
            continue
        module_content = state[state_key]
        if isinstance(module_content, str) and not module_content.strip():
            # 空字符串跳过——避免 value_turtle_payload 在 US/异常路径下落 0 字节
            continue
        ...
```

注意：这个空串跳过改动对所有 report module 生效（包括 `value_report` 自身），是普遍性改进，无副作用——原本写 0 字节 markdown 也是 noise。

### 6.6 不属于 Spec 1 的部分

- **不动** `app/routers/analysis.py:407` 的 `report_fields` 列表
- **不动** `app/routers/analysis.py:525` 的 summary 拼接
- **不暴露** payload 专属 API 端点
- 以上全部留给 Spec 4 实施时一起设计 API + 前端 tab

理由：`report_fields` 是 markdown 报告白名单，被 `reports` dict 渲染、`summary` 拼接消费。把 JSON payload 混进去会污染 markdown viewer、summary 文案、key_points 兜底逻辑。

**Spec 4 前端如何读 payload（边界澄清）**：

- Spec 1 阶段：`value_turtle_payload.json` 落在与 `value_report.md` 同目录（`{reports_dir}/value_turtle_payload.json`），但**前端没有路径直接拉取**。
- Spec 4 阶段：建议新增 GET `/api/analysis/{id}/turtle-payload` 端点，从 MongoDB 的 analysis 文档或磁盘 reports 目录读取并返回 JSON。**不**让前端绕过 API 直读静态文件——保持鉴权、CORS、版本化控制的统一入口。
- 备选：若 Spec 4 评估认为新建端点成本高，可扩展现有 `/api/analysis/{id}/result` 在 response 顶层加 `turtle_payload` 字段（与 `reports` dict 平行），从 MongoDB / 磁盘按 id 读取并附加。

Spec 4 brainstorming 时再定具体方案；Spec 1 只承诺"落盘存在 + state 链路可见"。

### 6.7 数据流总览

```
prepare_turtle_analysis (tool)
       │  returns JSON {facts, signals}
       ▼
ToolMessage.content (langgraph)
       │
       ▼
value_analyst_node
       │  _latest_turtle_tool_payload() → turtle_payload (str)
       │
       ├─→ value_report (LLM markdown)
       └─→ value_turtle_payload (原 JSON 字符串)
       │
       ▼
LangGraph state → propagator final_state → trading_graph output dict
       │
       ▼
simple_analysis_service 持久化
       ├─→ value_report.md
       └─→ value_turtle_payload.json
```

## 7. 边角清理

### 7.1 综合 2.1：删除 redaction

`tradingagents/dataflows/value_investment/turtle/decision.py`：

- 删除 `_SOURCE_TEXT_REDACTIONS` 常量（line 11-29）
- 删除 `_safe_non_decision_text` 函数（line 32-37）
- `_formula_status_lines` 与 `build_non_decisionable_report` 中所有 `_safe_non_decision_text(x)` 调用替换为 `str(x)`
- 移除不再使用的 `import re`

### 7.2 综合 2.3：工具签名对齐

`tradingagents/tools/turtle_analysis_tool.py:84` 统一为 `str = ""`：

```python
company_name: Annotated[str, "公司名称"] = "",
```

与 `agent_utils.py:2120` 的 Toolkit 方法保持一致。底层 `prepare_turtle_analysis_payload` 内 `company_name or ticker` 兜底逻辑对 `""` 与 `None` 行为一致。

### 7.3 计算 A.4：删三处死代码

`calculations.py` 中以下 `elif not _critical_missing` 分支不可达（因 `_validate_positive_market_cap` 已保证 market_cap > 0 或在 missing 中），全部删除：

- line 344-346（R 公式）
- line 370-372（GG 公式）
- line 411-412（net_cash_ratio）

**附带（Spec 1 范围内）**：相邻的 `r_market_cap != 0` / `gg_market_cap != 0` / `net_cash_market_cap != 0` 冗余检查同步删除——既然 `_validate_positive_market_cap` 已保证 > 0，`!= 0` 永真。这是 A.4 清理的自然延伸，不独立列入 §2.1 表格但视为同一项。

### 7.4 计算 A.5：删 ev_switch / cash_protection 不可达 degraded 分支

Fail-fast 下 `net_cash_ratio` 不做单边降级（cash 或 debt 缺失即 non_decisionable），导致：

- `calculations.py:436-437`（ev_switch 的 `else "degraded" if ev_missing`）
- `calculations.py:456-457`（cash_protection 同结构）

两处 degraded 分支不可触发，删除。简化为：

```python
ev_status: TurtleStatus = "non_decisionable" if ev_missing else "complete"
```

### 7.5 计算 A.6：`abs(capex)` 文档化

`decision.py:88-93` `build_turtle_decision_prompt` 的"输出结构"段第 2 项追加一句说明 capex 按绝对值参与（无论数据源以正号还是负号披露）。一行说明，零代码改动。

## 8. 测试策略

### 8.1 双轨策略（来自决策 Q6）

- **Happy-path 测试**：fixture 显式传 `holding_channel`，保留公式正确性回归覆盖
- **Fail-fast 测试**：新增专属用例固化默认 channel / display_only 字段 / 关键字段缺失的不可决策行为

### 8.2 测试组织

| 测试文件 | 改动类型 | 内容 |
|---------|---------|------|
| `test_turtle_facts.py` | 新增 | `TestMergeStatus`：各组合的最严重状态聚合；`TurtleReportFacts.status` / `TurtleMarketFacts.status` 默认值与构造 |
| `test_turtle_report_adapter.py` | 改 | 旧 proxy 字段名替换为 `dividend_payout_ratio_proxy_single_year`；proxy 字段断言 `reliability == "display_only"` |
| `test_turtle_report_adapter.py` | 新增 | `TestReportAdapterStatus`：空 extraction → non_decisionable；含 display_only → degraded；纯 reliable → complete |
| `test_turtle_market_adapter.py` | 改 | 现有缺失字段用例补 `market.status` 至少为 degraded 的断言 |
| `test_turtle_market_adapter.py` | 新增 | `TestFailFastDefaults`：不传 holding_channel → tax_rate display_only + 含默认 channel caveat；显式传入 → reliable |
| `test_turtle_calculations.py` | 改 | R/GG happy-path 用例补 `tax_rate` reliable；调整旧 proxy 名引用 |
| `test_turtle_calculations.py` | 新增 | `TestFailFastFromDefaults`：tax_rate display_only → R `non_decisionable`；proxy 不进 `_number_alias` |
| `test_turtle_calculations.py` | 新增 | `TestSignalsAggregateStatus`：facts.status non_decisionable → signals.status non_decisionable |
| `test_turtle_decision.py` | 改 | 删除断言"投资动作词被替换"的现有用例与 `_safe_non_decision_text` 测试 |
| `test_turtle_decision.py` | 新增 | `TestNonDecisionableReportPreservesIdentifiers`：`buyback_amount` / `shareholder_return` 等字段名 **不被改写**——综合评审 2.1 的回归钉子 |
| `test_turtle_value_analyst_integration.py` | 改 | "Turtle 路径成功"断言补 `result["value_turtle_payload"] == turtle_payload_json` |
| `test_turtle_value_analyst_integration.py` | 新增 | `TestPayloadOnFailure`：报告生成异常时 payload 仍透传；美股 unsupported 路径 payload 为空串 |
| `tests/unit/test_value_analyst_payload_propagation.py` | 新建 | 断言 `value_analyst_node` 返回 dict 含 `"value_turtle_payload"` 键（除 ToolNode 中间态外） |
| `tests/unit/test_simple_analysis_service_turtle_payload.py` | 新建 | 断言持久化层在非空 payload 下写 `value_turtle_payload.json`、空串下跳过——覆盖 §6.5 的"空内容短路"实现，替换原依赖 smoke 的验收条目 |

### 8.3 smoke 脚本调整

`scripts/smoke_test_turtle_value.py`：

- 新增 `--holding-channel`（default None）参数
- 不传时触发 fail-fast，stdout JSON 的 `signals_status == "non_decisionable"`（行为变更，文档化）
- 传 `--holding-channel long_term_domestic` 时按原 happy-path 行为

### 8.4 验收标准

PR 必须满足：

1. `pytest tests/unit/` 全绿，含所有新增用例
2. `pytest tests/unit/test_turtle_decision.py::TestNonDecisionableReportPreservesIdentifiers` 单独可跑且通过
3. `scripts/smoke_test_turtle_value.py --ticker 600519 --market A`（不传 holding_channel）输出 `signals_status == "non_decisionable"`
4. 同 smoke 传 `--holding-channel long_term_domestic`，A 股全 reliable 数据下 `signals_status in {"complete", "degraded"}`
5. **持久化集成验证**（替换原"smoke 后磁盘有文件"——smoke 脚本不经过 LangGraph / `simple_analysis_service` 持久化链路）：新增 `tests/unit/test_simple_analysis_service_turtle_payload.py`，断言：
   - 给定 `state["value_turtle_payload"] = '{"facts": ..., "signals": ...}'` 时，`save_reports_to_disk`（或等价持久化入口）在 `reports_dir/value_turtle_payload.json` 写入对应内容
   - 给定 `state["value_turtle_payload"] = ""`（美股 unsupported 路径模拟），不创建该文件
   - 这两个用例同时锁定 §6.3 的"始终返回 key"契约与 §6.5 的"空内容短路"行为
6. 任意一处引用 `dividend_avg_payout_ratio_3y` 作为 report-side proxy 的代码 / 测试被清理

**手工冒烟（PR checklist 项，非自动验收）**：通过 FastAPI 完整分析端点（如 POST `/api/analysis`）跑一次 A 股分析，显式传 holding_channel，确认 reports 目录下同时存在 `value_report.md` 与 `value_turtle_payload.json` 且后者可被 `json.tool` 解析。

### 8.5 不在 Spec 1 测试范围

- 跨币 FX → Spec 3
- 时间口径调整（A.1 / A.2）→ Spec 2
- frontend tab 渲染 → Spec 4
- API 端点暴露 payload → Spec 4
- MongoDB 历史数据兼容（缺 `value_turtle_payload` 字段读 None 即可，无破坏）→ Spec 4

## 9. 改动清单

| 文件 | 改动概要 |
|------|---------|
| `tradingagents/dataflows/value_investment/turtle/facts.py` | 加 `_STATUS_RANK` / `merge_status`；`TurtleReportFacts` / `TurtleMarketFacts` 加 `status` 字段；`to_dict` 同步序列化 status |
| `tradingagents/dataflows/value_investment/turtle/report_adapter.py` | `PAYOUT_PROXY_FIELD` 常量；`_derive_report_payout_proxy` 改用新 key 且 reliability=display_only；`build_report_facts_from_extraction` 末尾派生 status |
| `tradingagents/dataflows/value_investment/turtle/market_adapter.py` | `build_market_facts` 区分 `channel_is_explicit`，默认 channel 时 tax_rate display_only；删除无条件 `_append_caveat(caveats, DEFAULT_CHANNEL_CAVEAT)`（line 245）；可移除常量；末尾派生 status |
| `tradingagents/dataflows/value_investment/turtle/calculations.py` | 删 A.4 三处死代码 + A.5 两处 degraded 分支；alias chain 不动 |
| `tradingagents/dataflows/value_investment/turtle/decision.py` | 删 redaction 全部；prompt 加 `abs(capex)` 说明 |
| `tradingagents/tools/turtle_analysis_tool.py` | `holding_channel` 直传不走 context；`status` 改用 `merge_status` 聚合；`company_name` 签名对齐 `str = ""` |
| `tradingagents/agents/utils/agent_states.py` | `AgentState` TypedDict 新增 `value_turtle_payload` 字段 |
| `tradingagents/agents/analysts/value_analyst.py` | 所有 return 路径补 `value_turtle_payload`（除 unsupported 等无 payload 路径——参见 §6.4） |
| `tradingagents/graph/propagation.py` | InitialState 加 `"value_turtle_payload": ""` |
| `tradingagents/graph/trading_graph.py` | **可选**：`_log_state` 写日志时附 `value_turtle_payload`（plan 阶段决定） |
| `app/services/simple_analysis_service.py` | 持久化配置新增 `value_turtle_payload`；持久化循环加 "内容非空" 短路 |
| `scripts/smoke_test_turtle_value.py` | 新增 `--holding-channel` 参数 |
| `tests/unit/test_turtle_*.py` | 多文件改动 + 新增（详见 §8.2） |
| `tests/unit/test_value_analyst_payload_propagation.py` | 新建 |

## 10. 与后续 Spec 的依赖

- **Spec 2**（model-recalibration）：不依赖 Spec 1，可并行。但若 Spec 2 与 Spec 1 同期合入，Spec 1 提供的 fail-fast 测试基线让 Spec 2 的口径变更影响更易识别。
- **Spec 3**（cross-currency-fx）：不依赖 Spec 1，可并行。Spec 3 引入的 FX provider 会扩展 caveat 体系，但不冲突。
- **Spec 4**（turtle-data-view-frontend）：**强依赖 Spec 1 的 backend 透传**。Spec 4 实施时再设计 API 端点暴露 `value_turtle_payload`，并实现数据 / 计算 / 状态 tab 渲染。
