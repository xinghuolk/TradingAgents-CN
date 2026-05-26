# Turtle Model Recalibration 设计文档

- **创建日期**：2026-05-26
- **工作分支**：`main`
- **Spec 编号**：Spec 2（model-recalibration）
- **路线图**：`docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md`
- **依赖状态**：Spec 5 multi-period extraction ✅；Spec 3 data-source-quality ✅；Spec 4 frontend tab ✅
- **问题来源**：
  - PR #7 follow-up roadmap 的 Spec 2 范围
  - 上游 Turtle v0.15 模型定义：R/GG 的 M/O 口径、分红与回购税务不对称、`payout_anchor` 命名偏差
  - Spec 5 handoff：multi-period helpers 与 per-period payout derivation 需要由 Spec 2 接线

## 1. 目标

把 Turtle v0.15 核心分红/回购收益模型从当前的市场 action fallback 口径，校准为报告侧、跨期、可追溯的计算口径：

- `payout_anchor` 重命名为 `payout_M`，表达公式 M 的最终值。
- M 使用报告侧 3 年支付率均值和最新年信号：`max(payout_3y_avg, latest_signal)`。
- 承诺支付率本 Spec 不抽取；保留未来 hook，缺失时不执行 `min(3y avg, commitment)` 约束，并把状态降级说明清楚。
- O 使用报告侧 `repurchase_of_stock` 映射出的 `buyback_amount` 3 年均值，不再用市场 action 回购金额。
- R/GG 明确分红扣税、注销型回购不扣税的税务不对称。
- 市场分红/回购记录保留为上下文，不再影响核心公式状态。

## 2. 范围

### 2.1 范围内

| 编号 | 内容 |
|------|------|
| S2-1 | `report_adapter` 新增可靠字段 `dividend_payout_ratio_current_year = abs(dividends_paid) / net_profit` |
| S2-2 | 保留旧 `dividend_payout_ratio_proxy_single_year` 为 `display_only`，不让计算层读取它 |
| S2-3 | `TURTLE_FIELD_ALIASES` 增加 `repurchase_of_stock -> buyback_amount` |
| S2-4 | `calculations.py` 增加内部 resolved input structs，隔离字段读取、跨期聚合、状态合并 |
| S2-5 | `payout_anchor` 改为 `payout_M`，新 payload 不保留旧 alias |
| S2-6 | `payout_M` 使用报告侧 3 年平均 + 最新年信号，不再 fallback 到市场 action payout |
| S2-7 | R/GG 的 buyback O 切到报告侧 `buyback_amount` 3 年均值，不再 fallback 到市场 action buyback |
| S2-8 | 区分 material/status-affecting caveats 与 context-only caveats，市场 action 缺失不再降级核心模型 |
| S2-9 | 决策 prompt 文档化分红/回购税务口径不对称，以及 `repurchase_of_stock` 未验证注销进度的限制 |
| S2-10 | 更新单元测试、payload FX 测试、prompt/rehydration 兼容测试 |

### 2.2 范围外

- 不实现承诺支付率抽取，不从 `dividend_policy_text` 做二次 LLM 解析。
- 不实现 DPS/EPS 数据通道；“新信号 DPS 调整值”本 Spec 用最新年 `abs(dividends_paid) / net_profit` 表示。
- 不验证回购注销进度；`repurchase_of_stock` 只作为数量化 O 输入，注销进度通过 caveat/prompt 说明。
- 不做 GG bottom-up 现金流量表逐行追踪；当前仍使用已有 owner earnings 近似口径。
- 不扩展前端结构；现有 Turtle payload tabs 按泛化 schema 展示新字段即可。
- 不迁移历史 payload；旧 payload 中的 `payout_anchor` 仍由泛化展示/rehydration 兼容读取。

## 3. 设计决策

### 3.1 M 的承诺支付率

本 Spec 不抽取承诺支付率。公式保留未来 hook：

```text
未来完整形态：M = max(min(payout_3y_avg, commitment_ratio), latest_signal)
本 Spec 形态：M = max(payout_3y_avg, latest_signal)
```

当 `commitment_ratio` 缺失时，`payout_M` 可以 decisionable，但状态为 `degraded`，caveat 写明承诺约束未应用。

### 3.2 最新年信号

“新信号 DPS 调整值”不引入 DPS/EPS 新字段。本 Spec 用最新年报告侧可靠值：

```text
latest_signal = abs(dividends_paid) / net_profit
```

派生条件：

- `dividends_paid` 与 `net_profit` 都是 report-side reliable。
- 两者币种经 `normalize_currency(...)` 后一致。
- `net_profit > 0`。
- `dividends_paid` 可为负数，按现金流出语义取绝对值。

### 3.3 内部数据结构

使用方案 2：在 `calculations.py` 内增加内部 resolved input structs。它们只服务计算层，不进入 `facts.py`，也不改变 API payload schema。

建议结构：

```python
@dataclass(frozen=True)
class PayoutInputs:
    payout_3y_avg: Decimal | None
    latest_signal: Decimal | None
    commitment_ratio: Decimal | None
    applied_value: Decimal | None
    sources: list[TurtleSourceReference]
    missing_inputs: list[str]
    caveats: list[str]
    status: TurtleStatus

@dataclass(frozen=True)
class BuybackInputs:
    buyback_3y_avg_hm: Decimal | None
    sources: list[TurtleSourceReference]
    missing_inputs: list[str]
    caveats: list[str]
    status: TurtleStatus
```

这些 structs 的价值是把“字段读取 + 多期聚合 + fallback/降级规则”集中在一个边界内，避免 R/GG/HH 分支各自散落判断。

## 4. 报告侧数据适配

### 4.1 新增可靠 payout 当前年字段

`report_adapter.py` 新增：

- `CURRENT_YEAR_PAYOUT_FIELD = "dividend_payout_ratio_current_year"`
- `CURRENT_YEAR_PAYOUT_CAVEAT`：说明它由报告侧分红和净利润派生，不是承诺支付率。

派生函数可以复用现有 `_derive_report_payout_proxy` 的骨架，但输出字段和 reliability 不同：

- `dividend_payout_ratio_current_year`：`reliable`，供计算层读取。
- `dividend_payout_ratio_proxy_single_year`：继续 `display_only`，保留 UI/兼容上下文。

历史期也必须派生 `dividend_payout_ratio_current_year`。Spec 5 的 `historical` 中每一期都应包含自己的当前年 payout ratio，计算层再用 `_number_report_3y_avg` 对逐年 ratio 取均值。

### 4.2 均值口径

3 年 payout 均值必须是逐年 ratio 的平均：

```text
payout_3y_avg = mean(
  payout_ratio_2024,
  payout_ratio_2023,
  payout_ratio_2022,
)
```

不能写成：

```text
sum(dividends_paid_3y) / sum(net_profit_3y)
```

原因是上游模型要求“近 3 年支付率均值”，并且逐年 ratio 能保留每年利润波动对分红政策的影响。

### 4.3 回购字段 alias

`TURTLE_FIELD_ALIASES` 增加：

```python
"buyback_amount": ("repurchase_of_stock", ...)
```

若上游 extractor 字段名已经是 `repurchase_of_stock`，Turtle report facts 对外仍统一输出 `buyback_amount`。这让计算层只读 Turtle canonical field，不依赖 extractor 原始字段名。

`repurchase_of_stock` 代表数量化回购金额，但并不证明股份已经注销。本 Spec 只把它用于 O 的数量输入，prompt/caveat 必须说明注销进度未验证。

### 4.4 币种归一

派生 payout ratio 时不能用 raw uppercase 比较币种。应使用已有 `normalize_currency(...)`，避免 `HK$`、`HKD` 等等价写法误判不一致。

## 5. 计算层设计

### 5.1 `payout_M`

计算步骤：

1. 读取 `dividend_payout_ratio_current_year` 的 report-side 3 年均值。
2. 读取 latest report 的 `dividend_payout_ratio_current_year` 作为 latest signal。
3. 承诺支付率本 Spec 恒为空，记录 future hook caveat。
4. 若 3 年均值和 latest signal 至少一个存在，取最大值。
5. 若两者都缺失，`payout_M` 为 `non_decisionable`。

状态规则：

| 数据情况 | `payout_M.status` | 说明 |
|----------|-------------------|------|
| 3 年均值 + latest signal 都存在 | `degraded` | 本 Spec 未抽取承诺支付率，因此承诺上限未应用；未来承诺值可用后才可能为 `complete` |
| 只存在 3 年均值 | `degraded` | latest signal 缺失，但仍可 decisionable |
| 只存在 latest signal | `degraded` | 3 年均值缺失，但仍可 decisionable |
| 两者都缺失 | `non_decisionable` | R/GG 也无法决策 |

`payout_M` 的 `substitution` 应包含：

- `payout_3y_avg`
- `latest_signal`
- `commitment_ratio = null`
- `commitment_constraint_applied = false`
- `selected = max(available inputs)`

不输出 `audit_payout_3y_avg`、`audit_latest_signal` 之类审计 FormulaResult。

### 5.2 R

目标公式：

```text
R = (net_profit * payout_M * (1 - tax_rate) + buyback_3y_avg) / market_cap * 100
```

输入口径：

- `net_profit`：latest report snapshot。
- `payout_M`：5.1 的结果。
- `tax_rate`：holding channel tax rate，沿用现有 market adapter。
- `buyback_3y_avg`：报告侧 `buyback_amount` 3 年均值。
- `market_cap`：市场侧市值。

若 `buyback_3y_avg` 缺失，R 用 0 代入，`status=degraded`，`missing_inputs` 包含 `buyback_amount_3y_avg`。这是可决策降级，不是 `non_decisionable`。

### 5.3 GG

目标公式保持与 R 对齐的税务和回购口径：

```text
GG = (owner_earnings * payout_M * (1 - tax_rate) + buyback_3y_avg) / market_cap * 100
```

`owner_earnings` 沿用现有近似实现，不在本 Spec 追踪 bottom-up AA 现金流量表明细。

`buyback_3y_avg` 缺失时规则同 R：用 0 代入并降级。

### 5.4 HH

HH 使用 R 和 GG 的差异。由于 R/GG 的回购项相同，回购缺失在差值中抵消：

```text
HH = R - GG
```

因此当唯一问题是 `buyback_3y_avg` 缺失时：

- R/GG 可以带 `buyback_amount_3y_avg` missing/degraded。
- HH 不应额外传播 `buyback_amount_3y_avg` missing。
- HH 的状态应由其真正依赖的差值输入决定，避免制造虚假的 HH 阻断。

## 6. 市场 action 降级策略

市场 action 数据仍保留：

- `dividend_records`
- `buyback_records`
- market-side action caveats

但以下市场 action 字段不再作为核心公式输入：

- `avg_payout_ratio_3y`
- `dividend_avg_payout_ratio_3y`
- market-side `buyback_amount`

### 6.1 caveat 分类

新增逻辑概念：

- **material/status-affecting caveats**：核心输入质量问题，会影响 `facts.status` 或 `signals.status`。
- **context-only caveats**：只提示上下文信息，不影响核心模型状态。

市场 action 缺失/不可用的 caveats 归为 context-only，因为本 Spec 后核心公式不再读取市场 action 分红/回购。

实施时优先在 `market_adapter` 源头分类并据此计算 `TurtleMarketFacts.status`。不要只靠下游字符串过滤吞掉 caveat；如果短期需要 helper，也应显式枚举已知 context-only action caveats，并用测试锁定。

### 6.2 overall status 聚合

`compute_turtle_signals` 的最终状态不能再简单依赖：

- `facts.status == degraded`
- `bool(_combined_caveats(facts))`
- 任意结果 degraded

需要改为核心、物料级聚合：

```python
CORE_RESULT_KEYS = {
    "payout_M",
    "R",
    "GG",
    "HH",
    "net_cash_ratio",
    "ev_switch",
    "cash_protection",
    "owner_earnings",
}
```

聚合规则：

- unsupported market/report 仍为 `unsupported`。
- 核心输入或核心公式 `non_decisionable` 时，整体为 `non_decisionable`。
- 核心输入或核心公式有 material degradation 时，整体为 `degraded`。
- context-only caveats 仍展示，但不改变整体状态。
- 非核心/展示字段不参与整体状态。

## 7. Prompt 与报告说明

`build_turtle_decision_prompt` 和纯文本 rehydration 路径需要体现：

1. 分红和回购税务不对称：
   - 分红按 holding channel 扣税。
   - 注销型回购对继续持有股东无即时税务事件，R/GG 中 `+ buyback_3y_avg` 不扣税。
2. 回购输入限制：
   - `repurchase_of_stock` 被用作报告侧回购金额输入。
   - 当前 payload 未验证股份注销进度；若回购未注销，O 可能高估股东回报。
3. 承诺支付率限制：
   - 本 Spec 未抽取 commitment ratio。
   - `payout_M` 使用 `max(payout_3y_avg, latest_signal)`，未应用承诺上限。

## 8. 兼容性

### 8.1 `payout_anchor` rename

新 payload：

- 输出 `results["payout_M"]`。
- 不输出 `results["payout_anchor"]`。

旧 payload：

- 前端 `TurtlePayloadPanel.vue` 按 `Object.entries(results)` 泛化展示，仍可显示旧 `payout_anchor`。
- `_plain_turtle_report_prompt` 按完整 FormulaResult schema 泛化 rehydrate，旧 key 不需要迁移。

测试与 fixture 需要全部迁移到 `payout_M`。

### 8.2 proxy 字段

旧 `dividend_payout_ratio_proxy_single_year` 保留为 display-only，不改语义，避免破坏已有数据视图或历史 payload。

新计算只读 `dividend_payout_ratio_current_year`。这避免把旧 proxy 悄悄升级成 reliable 导致含义不清。

### 8.3 前端

不做结构性前端改动。已有四个 Turtle tab 泛化读取 facts/results：

- facts rows 读取 fields。
- calculation rows 读取 results。
- status tab 汇总 status/caveats/missing inputs。

需要注意：因为不输出审计 FormulaResult，status tab 不会被中间审计值污染。

## 9. FX 影响

公式不再使用 market-side action buyback/payout 后，FX 收集规则也要同步：

- 报告侧 `buyback_amount` 参与 R/GG，若与目标口径币种不同，应进入 `collect_fx_currencies`。
- 只有 market-side `buyback_amount` 时，不应触发公式 FX。
- `repurchase_of_stock -> buyback_amount` alias 后，FX 逻辑只看 Turtle canonical `buyback_amount`。

这避免“市场 action 已经不参与公式，但仍因其币种缺 FX 导致 payload 降级”的错误。

## 10. 测试计划

### 10.1 report adapter

- `repurchase_of_stock` 被映射为 `buyback_amount`。
- 最新期和 historical 每一期都派生 `dividend_payout_ratio_current_year`。
- `dividend_payout_ratio_current_year` 为 reliable，旧 `dividend_payout_ratio_proxy_single_year` 仍为 display-only。
- `HK$` / `HKD` 通过 `normalize_currency(...)` 视为同币。
- `net_profit <= 0` 时不派生 payout ratio，并给 caveat。
- `dividends_paid` 不可靠时不派生 payout ratio。
- `dividends_paid` 为负数时按 `abs(...)` 计算。

### 10.2 calculations

- `payout_M` 同时有 3 年均值和 latest signal 时取最大值。
- 只有 3 年均值时 `payout_M` degraded decisionable。
- 只有 latest signal 时 `payout_M` degraded decisionable。
- 两者都缺失时 `payout_M` non_decisionable。
- 3 年均值是逐年 ratio 平均，不是 ratio-of-means。
- R/GG 使用报告侧 `buyback_amount` 3 年均值。
- 报告侧 buyback 缺失时 R/GG 用 0 代入并 degraded，missing input 为 `buyback_amount_3y_avg`。
- 只有 buyback 缺失时 HH 不传播 `buyback_amount_3y_avg` missing。
- market-side payout/buyback 存在时不会被公式读取。
- market action 缺失 caveat 为 context-only，不降级核心 signals。
- `results` 不再包含 `payout_anchor`，包含 `payout_M`。

### 10.3 payload FX

- 报告侧 `buyback_amount` 币种需要 FX 时被收集。
- 只有 market-side buyback 币种时不触发 FX。
- `repurchase_of_stock` alias 后，FX 测试覆盖 canonical `buyback_amount`。

### 10.4 prompt / rehydration / frontend

- prompt 包含分红扣税、回购不扣税说明。
- prompt 包含回购注销进度未验证 caveat。
- prompt 包含承诺支付率未应用说明。
- 纯文本 rehydration 能读取新 `payout_M`。
- 旧 payload 的 `payout_anchor` 仍可泛化 rehydrate。
- 前端运行 `npm run type-check` / `npm run build`，不要求结构性改动。

## 11. 实施顺序建议

1. 先改 `report_adapter`：新增 reliable current-year payout，保留旧 proxy，增加 buyback alias。
2. 改 calculation input resolver：封装 payout/buyback resolved structs。
3. 改 `payout_M` 与 R/GG/HH 公式，去掉 market action fallback。
4. 改 caveat/status 分类，确保 context-only market action caveats 不降级核心模型。
5. 改 FX currency collection，确保市场 action buyback 不触发公式 FX。
6. 改 prompt 文案。
7. 迁移测试和 fixtures。

## 12. 验收标准

- 新 payload 中 `results` 有 `payout_M`，没有 `payout_anchor`。
- `payout_M.substitution` 能解释 3 年均值、latest signal、承诺缺失和最终选择值。
- 缺少市场分红/回购 action 数据时，payload 仍展示 context caveat，但核心公式不因此降级。
- 缺少报告侧回购 3 年均值时，R/GG degraded，HH 不因该项额外 missing。
- market-side payout/buyback 无法改变 R/GG/HH 数值。
- report-side `repurchase_of_stock` 能进入 O 的 3 年均值与 FX 收集。
- 测试覆盖 3 年 ratio 平均口径，防止误改成 ratio-of-means。
