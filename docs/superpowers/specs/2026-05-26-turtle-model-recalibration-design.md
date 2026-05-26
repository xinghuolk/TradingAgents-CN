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
- 承诺支付率本 Spec 不抽取；保留未来 hook，缺失时不执行 `min(3y avg, commitment)` 约束。承诺未应用归类为 **context-only caveat（不降级核心状态）**，方向性偏置在 caveat/prompt 说明（见 §3.1）。
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
- 不实现 DPS/EPS 数据通道；"新信号 DPS 调整值"本 Spec 用最新年 `abs(dividends_paid) / net_profit` 表示。
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

当 `commitment_ratio` 缺失时，`payout_M` 仍可 decisionable。**承诺未应用归类为 context-only caveat（不降级核心状态）**：数据本身没有质量问题，只是本 Spec 未把承诺上限纳入范围。因此当 3 年均值和 latest signal 数据齐全时，`payout_M.status = complete`；`degraded` 仅保留给真实数据缺口（见 §6.1 caveat 分类）。

注意承诺上限未应用的**方向性偏置**：完整形态里 `min(payout_3y_avg, commitment_ratio)` 是对支付率取上界，去掉该上界等价于 `min(x, +∞) = x`，因此 `payout_M` 单调不减、可能**偏高**，进而使 R/GG 收益率偏乐观。该偏置在 caveat 与 prompt（§7）中说明，但不改变状态。

### 3.2 最新年信号

"新信号 DPS 调整值"不引入 DPS/EPS 新字段。本 Spec 用最新年报告侧可靠值：

```text
latest_signal = abs(dividends_paid) / net_profit
```

派生条件：

- `dividends_paid` 与 `net_profit` 都是 report-side reliable。
- 两者币种经 `normalize_currency(...)` 后一致。
- `net_profit > 0`。
- `dividends_paid` 可为负数，按现金流出语义取绝对值。

**已知语义限制（记录在 caveat/prompt，不阻断）**：

- 上游"新信号 DPS 调整值"本是**前瞻**的 DPS 步进信号；本 Spec 用**回看**的最新年 `dividends_paid / net_profit` 代理，把前瞻项塌缩成了均值的一个历史成员。
- `latest_signal` 同时也是 `payout_3y_avg` 里的最新一年（见 §4.2）。`payout_M = max(payout_3y_avg, latest_signal)` 在支付率上行趋势下会直接退化为"只取最新年"，3 年平滑被绕过；且 `max(均值, 其成员) ≥ 均值` 恒成立，存在**向上偏置**。这与承诺上限未应用的偏置同向（§3.1）。
- 本 Spec 不改变上游"近 3 年均值"口径来消除该偏置（§4.2 仍取含最新年的 3 年均值），仅在 prompt（§7）中说明 DPS 代理与方向性偏置。

### 3.3 内部数据结构

使用方案 2：在 `calculations.py` 内增加内部 resolved input structs。它们只服务计算层，不进入 `facts.py`，也不改变 API payload schema。

`sources` 用现有 `FormulaResult.sources` 的类型 `list[str]`（**代码库中没有 `TurtleSourceReference` 类型**，不要引入）。数值类型沿用计算层现有的 `float`（`_money_hm` / `_number_report_3y_avg` 等均返回 `float`），不引入 `Decimal`。

建议结构：

```python
@dataclass(frozen=True)
class PayoutInputs:
    payout_3y_avg: float | None        # 逐年 ratio 的均值（含最新年），ratio 与币种无关
    latest_signal: float | None        # 最新年 dividends_paid/net_profit
    commitment_ratio: float | None     # 本 Spec 恒为 None（future hook）
    applied_value: float | None         # 最终 M = max(可用输入)
    sources: list[str]
    missing_inputs: list[str]
    caveats: list[str]
    status: TurtleStatus

@dataclass(frozen=True)
class BuybackInputs:
    # 报告侧 buyback_amount 的逐年原始值（MoneyAmount，带币种），转币延后到各公式。
    # 不在此预转单一币种——R 用 r_target_currency、GG 用 owner_target_currency，
    # 单一预转值无法同时满足两者（见 §5.4 共同币种前提）。
    periods: list[MoneyAmount]
    sources: list[str]
    missing_inputs: list[str]           # 缺失时含 "buyback_amount_3y_avg"
    caveats: list[str]
    status: TurtleStatus
```

两个 struct 的 `sources` 都用 `list[str]`（同 `FormulaResult.sources`）。

这些 structs 的价值是把"字段读取 + 多期聚合 + fallback/降级规则"集中在一个边界内，避免 R/GG/HH 分支各自散落判断。

**接线**：

- 新增 resolver 函数 `_resolve_payout_inputs(facts, caveats)` 与 `_resolve_buyback_inputs(facts, caveats)`，在 `compute_turtle_signals` 中各调用一次，结果在 R/GG/HH 间共享。
- `PayoutInputs` 是 ratio（无量纲），R 与 GG 共享同一实例。
- buyback 的逐年取均值仍由现有 `_money_hm_report_3y_avg(facts, "buyback_amount", caveats, <target_currency>)` 完成——R/GG 各自带自己的 target currency 调用一次。`BuybackInputs` **不另起一套平均机制**：它只承载 resolver 一次性算出的 `status` / `missing_inputs` / `caveats` / `sources`（供 §6.2 聚合与审计），`periods` 仅在需要展示/校验原始逐年值时使用；真正进 R/GG 公式的均值由上述 helper 按各自币种现算。这样既满足 §5.4 的"同一 target currency 归一"前提（HH 抵消），又避免 struct 与 helper 重复计算。

## 4. 报告侧数据适配

### 4.1 新增可靠 payout 当前年字段

`report_adapter.py` 新增：

- `CURRENT_YEAR_PAYOUT_FIELD = "dividend_payout_ratio_current_year"`
- `CURRENT_YEAR_PAYOUT_CAVEAT`：说明它由报告侧分红和净利润派生，不是承诺支付率。

派生函数可以复用现有 `_derive_report_payout_proxy` 的骨架，但输出字段和 reliability 不同：

- `dividend_payout_ratio_current_year`：`reliable`，供计算层读取。
- `dividend_payout_ratio_proxy_single_year`：继续 `display_only`，保留 UI/兼容上下文。

历史期也必须派生 `dividend_payout_ratio_current_year`。**实现要点**：在 `build_report_facts_from_extraction` 内派生该字段（`_derive_report_payout_proxy` 当前就在此函数内、且该函数对 latest + 每个 historical 期各跑一次），使最新期与每个 historical 期**自动**都带上自己的当前年 ratio。计算层再用 `_number_report_3y_avg(facts, "dividend_payout_ratio_current_year", ...)` 对逐年 ratio 取均值。

⚠️ 不要只为最新期派生该字段：若只给最新期，`_number_report_3y_avg` 跨期取均值时可用期数会跌破 ≥2 阈值（§4.2），导致 `payout_3y_avg` 缺失、整体被动降级。

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

原因是上游模型要求"近 3 年支付率均值"，并且逐年 ratio 能保留每年利润波动对分红政策的影响。

**最少期数与分母策略（对齐已有 helper，不要写死 3 期）**：

- `_number_report_3y_avg` 现有口径：**可用期数 ≥ 2 才计算**，分母 = 可用期数（mean over available），不足 2 期返回 `None`、missing input 记 `dividend_payout_ratio_current_year_3y_avg`。
- 可用期数为 2 时追加 caveat `"dividend_payout_ratio_current_year_3y_avg computed from 2/3 periods"`。**该 caveat 归类为 material/status-affecting**（核心输入质量信号，与市场 action caveat 不同；见 §6.1）。
- 因此"3y avg"在数据不足时实际可能是 2 期均值；spec 不写死 `mean(r24,r23,r22)` 三项。

**亏损年的上偏（记录在 prompt，不阻断）**：某年 `net_profit ≤ 0` 时该年不派生 ratio（§10.1），被排除出均值。但亏损年常仍发分红（动用留存），其真实 payout 经济意义上 >100% 或无定义；把它排除会使尾随 payout 看起来比实际更可持续，对周期/亏损公司**系统性高估 M**。该限制在 §7 prompt 说明。

### 4.3 回购字段 alias

`TURTLE_FIELD_ALIASES` 现有结构是 `extractor_field_id -> canonical_str`（如 `"capital_expenditures": "capex"`，经 `.get(field_id, field_id)` 使用）。因此新增条目方向应为：

```python
"repurchase_of_stock": "buyback_amount"
```

（注意：不是 `"buyback_amount": (...)`，那会把 key/value 方向和类型都写反。）

若上游 extractor 字段名是 `repurchase_of_stock`，Turtle report facts 对外仍统一输出 canonical `buyback_amount`。这让计算层只读 Turtle canonical field，不依赖 extractor 原始字段名。当前 `repurchase_of_stock` 尚未作为 extractor 字段接线（`FIELD_TO_KEY` 与 `TURTLE_FIELD_ALIASES` 均无此项），本 alias 即为接线点。

`repurchase_of_stock` 代表数量化回购金额，但并不证明股份已经注销。本 Spec 只把它用于 O 的数量输入，prompt/caveat 必须说明注销进度未验证。

### 4.4 币种归一

派生 payout ratio 时不能用 raw uppercase 比较币种。应使用已有 `normalize_currency(...)`，避免 `HK$`、`HKD` 等等价写法误判不一致。

## 5. 计算层设计

### 5.1 `payout_M`

计算步骤：

1. 读取 `dividend_payout_ratio_current_year` 的 report-side 3 年均值（`_number_report_3y_avg`，≥2 期口径，见 §4.2）。
2. 读取 latest report 的 `dividend_payout_ratio_current_year` 作为 latest signal。
3. 承诺支付率本 Spec 恒为空，记录 future hook caveat（**context-only**，见 §3.1/§6.1）。
4. 若 3 年均值和 latest signal 至少一个存在，取最大值。
5. 若两者都缺失，`payout_M` 为 `non_decisionable`。

状态规则（承诺未应用为 context-only，因此数据齐全时可达 `complete`；`degraded` 仅由真实数据缺口/material caveat 触发）：

| 数据情况 | `payout_M.status` | 说明 |
|----------|-------------------|------|
| 3y 均值（3/3 期）+ latest signal 都存在 | `complete` | 承诺未应用为 context-only caveat，不降级；数据齐全 |
| 3y 均值仅 2/3 期 + latest signal | `degraded` | `..._3y_avg computed from 2/3 periods` 为 **material** caveat（§4.2） |
| 只存在 3y 均值（latest signal 缺失） | `degraded` | latest signal 缺失为真实数据缺口，仍可 decisionable |
| 只存在 latest signal（3y 均值 <2 期缺失，如新上市/仅 1 期） | `degraded` | 3y 均值缺失为真实数据缺口，仍可 decisionable |
| 两者都缺失 | `non_decisionable` | R/GG 也无法决策 |

`payout_M` 的 `substitution` 仍是**字符串**（`FormulaResult.substitution` 类型为 `str`，被 `formatting.py` 整体 JSON dump、`value_analyst._formula_result_from_payload` rehydration 读取、前端 `el-table-column prop="substitution"` 按字符串消费——**不改类型**。注意 `decision.py` 并不读取 `substitution`，故无需顾及它）。把审计信息编码进可读字符串，例如：

```text
payout_M = max(payout_3y_avg=0.45, latest_signal=0.50) = 0.50; commitment_ratio=null, commitment_constraint_applied=false
```

不输出 `audit_payout_3y_avg`、`audit_latest_signal` 之类审计 FormulaResult。

### 5.2 R

目标公式（`buyback_amount_3y_avg` 为本 Spec 统一命名，下同；避免 `buyback_3y_avg` / `buyback_3y_avg_hm` 等多种拼写——missing-input 串、HH 抵消断言、验收均依赖该 canonical 名）：

```text
R = (net_profit * payout_M * (1 - tax_rate) + buyback_amount_3y_avg) / market_cap * 100
```

输入口径：

- `net_profit`：latest report snapshot。
- `payout_M`：5.1 的结果。
- `tax_rate`：holding channel tax rate，沿用现有 market adapter。
- `buyback_amount_3y_avg`：报告侧 `buyback_amount` 3 年均值，转到 R 的 `r_target_currency` 后取均值。
- `market_cap`：市场侧市值，归一到同一 `r_target_currency`。

若 `buyback_amount_3y_avg` 缺失，R 用 0 代入，`status=degraded`，`missing_inputs` 包含 `buyback_amount_3y_avg`。这是可决策降级，不是 `non_decisionable`。沿用现有 `_buyback_input` 的 0-代入逻辑与可审计 substitution（substitution 字符串显示 `+ 0`），但 **caveat 串与 missing key 都改为 3y-avg 口径**：caveat `"buyback_amount_3y_avg missing; treated as 0 for degraded calculation"`，missing input `"buyback_amount_3y_avg"`（旧实现用的是 `"buyback_amount"`，需一并迁移，见 §10.2）。

**时间口径说明（上游有意混用，记录在 §7 prompt）**：R 的分红腿是 `最新年 net_profit × 多期派生的 payout_M`（point-in-time × multi-period），buyback 腿是 `3 年均值`（smoothed）。两腿对单个异常年份的敏感度不同；这是上游模型的有意设计，不在本 Spec 把 `net_profit` 也归一成 3 年均值。

### 5.3 GG

目标公式保持与 R 对齐的税务和回购口径：

```text
GG = (owner_earnings * payout_M * (1 - tax_rate) + buyback_amount_3y_avg) / market_cap * 100
```

`owner_earnings` 沿用现有近似实现，不在本 Spec 追踪 bottom-up AA 现金流量表明细。

`buyback_amount_3y_avg` 缺失时规则同 R：用 0 代入并降级（同样的 caveat 串与 missing key）。注意 GG 用 `owner_target_currency` 归一 buyback/market_cap，可能与 R 的 `r_target_currency` 不同——这是 §5.4 HH 抵消前提要处理的点。

### 5.4 HH

HH 使用 R 和 GG 的差异：

```text
HH = R - GG = (net_profit - owner_earnings) * payout_M * (1 - tax_rate) / market_cap * 100
```

**抵消的前提（必须满足，否则 HH 数值错误而非仅状态错误）**：buyback 项 `+ buyback_amount_3y_avg / market_cap` 只有在 **R 和 GG 使用同一 buyback 值且同一 market_cap 币种归一**时才在差值里精确抵消。当前代码 R 用 `r_target_currency`、GG 用 `owner_target_currency`（`calculations.py:386,405`），若两者不同则 `mcap_R ≠ mcap_GG`，buyback 项不抵消。**实现约束**：HH 计算路径必须让 R、GG 的 `buyback_amount_3y_avg` 与 `market_cap` 归一到**同一 target currency**；缺失时双方都 0-代入，差值天然抵消。

因此关于 HH 状态：

- **正面规则**：`HH.status` = 对其真正依赖的差值输入 `{net_profit, owner_earnings, payout_M, tax_rate, market_cap}` 的状态聚合，**显式排除 buyback 降级标志**。
- 当唯一问题是 `buyback_amount_3y_avg` 缺失时：R/GG 可带 `buyback_amount_3y_avg` missing/degraded，但 HH **不传播** `buyback_amount_3y_avg` missing，也不因此降级——避免制造虚假的 HH 阻断。
- 这是"只排 buyback"的显式规则，而非"它们抵消"的隐含推论；不要让 HH 整体继承 R/GG 状态。

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

**实现机制（三处都要顾及）**：

1. `market_adapter.build_market_facts` 目前在 `caveats` 非空时即把 `TurtleMarketFacts.status` 设为 `degraded`（`market_adapter.py:350-358`）。需改为：context-only caveats **不计入** market facts 自身的 status 派生（这些 caveat 仍发出、仍展示，但 market status 不因它们降级）。
2. **report 半同样几乎永远 degraded（关键，原设计遗漏）**：`build_report_facts_from_extraction` 在「任何 caveat 或任何非 reliable 字段」时即 `status=degraded`（`report_adapter.py:311-318`），而 `_derive_report_payout_proxy` 成功时**无条件**追加 `PAYOUT_PROXY_CAVEAT`（`report_adapter.py:252`）、且 `dividend_payout_ratio_proxy_single_year` 是 `display_only`（非 reliable）——因此 `report.status` 本来就**几乎永远 degraded**。又因 `facts.status = merge_status(report.status, market.status)`（`turtle_analysis_tool.py:116`），仅修 market 半**不足以**让整体可达 `complete`。
3. `compute_turtle_signals` 的整体聚合（§6.2）**不再读取 `facts.status`（report 与 market 两半都不读）**，也不再因 `bool(_combined_caveats)` 一律降级，改为只看核心 result 的 material 状态。`facts.status`（facts/状态 tab 的展示值）可继续因 display_only proxy 而 degraded——这是与核心信号状态分离的展示面，不阻断 `signals.status` 达到 `complete`。

不要只靠下游字符串过滤吞掉 caveat；分类在**源头(创建处)**完成，每条 caveat 创建时即带 material/context-only 归属，并用测试锁定。

**已知 caveat 分类账（实现与测试以此为准；新增 caveat 必须在创建处显式归类）**：

| caveat 串 | 来源 | 分类 |
|-----------|------|------|
| `"dividend data missing"` | `market_adapter.py:284` | context-only |
| `"avg_payout_ratio_3y missing"` | `market_adapter.py:302` | context-only |
| `"dividend records missing"` | `market_adapter.py:311` | context-only |
| `"buyback data missing"` | `market_adapter.py:314` | context-only |
| `"buyback_amount missing"` / `"buyback_amount invalid"` | `market_adapter.py:335-338` | context-only（市场侧，已不入公式） |
| 承诺支付率未应用（future hook，§3.1） | calculations 计算层 | context-only |
| `repurchase_of_stock` 注销进度未验证（§4.3/§7） | calculations / prompt | context-only |
| `"dividend_payout_ratio_current_year_3y_avg computed from 2/3 periods"` | `_number_report_3y_avg`（§4.2） | **material** |
| `"buyback_amount_3y_avg missing; treated as 0 for degraded calculation"` | `_buyback_input`（§5.2） | **material** |
| `rf_rate` 缺失/非法 | `market_adapter.py:274-278` | **material**（核心公式输入） |
| `tax_rate` unknown / uses default | `market_adapter.py:248-255` | **material**（核心公式输入） |
| 市场 buyback 存在但报告侧缺失、被排除出公式（§9） | calculations FX/公式层 | context-only |

注意 `tax_rate`/`rf_rate` 等是核心公式输入，**仍为 material**——只有"分红/回购 action"类市场 caveat 才降为 context-only。

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

# 只有这些"决策性核心公式" non_decisionable 才阻断整体；
# 其它核心结果（net_cash_ratio/ev_switch/cash_protection/owner_earnings）
# non_decisionable 时仅降级，保留 Spec 2 前 R/GG-only 的阻断范围。
DECISION_BLOCKING_KEYS = {"payout_M", "R", "GG"}
```

聚合规则：

- 市场/报告 **unsupported**（不支持的市场类型等）仍为 `unsupported`——这是"是否支持"的判定，与 `facts.status==degraded` 无关。
- **不读取 `facts.status`（report/market 两半都不读，见 §6.1 第 2 点）**；整体状态只由 `CORE_RESULT_KEYS` 的 result 状态决定。
- `DECISION_BLOCKING_KEYS`（`payout_M`/`R`/`GG`）中任一 `non_decisionable` 时，整体为 `non_decisionable`。其余核心结果（辅助指标）`non_decisionable` **不阻断**，仅按下一条降级——这与旧实现"只有 R/GG 阻断"一致（额外加上 `payout_M`）。
- 任一核心结果 `degraded` 或 `non_decisionable`（含上一条的辅助指标），或存在 **material** caveat 时，整体为 `degraded`。
- context-only caveats 仍展示，但不改变整体状态。
- 非核心/展示字段不参与整体状态。
- 以上全部满足且无 material degradation 时，整体为 `complete`。**承诺未应用是 context-only（§3.1/§6.1），因此数据齐全时整体可达 `complete`**——不再像旧实现那样"只要有任意 caveat 就 degraded"。

**现存测试需调整**：

- `test_compute_turtle_signals_degrades_when_caveats_exist_with_complete_critical_results`（`tests/unit/test_turtle_calculations.py:243`）目前用 `caveats=["rf_rate missing"]`。注意 `rf_rate` 在分类账里是 **material**，所以新规则下它**仍应 degraded**——该用例**不会被新规则破坏**，但应改名/补强以明确"material caveat → degraded"，并**新增一个对应用例**：注入 **context-only** caveat（如 `"dividend data missing"`）且核心 result complete 时，整体 `signals.status` 保持 `complete`（这才是验证 §6.2 解耦的关键用例）。
- `test_payload_fx_failure_degrades_report_status` 等涉及核心输入的用例保持 degraded。

## 7. Prompt 与报告说明

`build_turtle_decision_prompt`（`decision.py`）和纯文本 rehydration 路径 `_plain_turtle_report_prompt`（注意它在 `tradingagents/agents/analysts/value_analyst.py`，不在 turtle 包内）需要体现：

1. 分红和回购税务不对称：
   - 分红按 holding channel 扣税。
   - 注销型回购对继续持有股东无即时税务事件，R/GG 中 `+ buyback_amount_3y_avg` 不扣税。
2. 回购输入限制：
   - `repurchase_of_stock` 被用作报告侧回购金额输入。
   - 当前 payload 未验证股份注销进度；若回购未注销，O 可能高估股东回报。
3. 承诺支付率限制：
   - 本 Spec 未抽取 commitment ratio。
   - `payout_M` 使用 `max(payout_3y_avg, latest_signal)`，未应用承诺上限。
4. `payout_M` 的方向性偏置（汇总 §3.1/§3.2）：
   - 承诺上限未应用、且 latest signal 用回看的最新年 ratio 代理前瞻 DPS、且 latest signal 同时是 3y 均值成员——三者同向使 `payout_M` 可能**偏高**，R/GG 收益率偏乐观。
   - 亏损年（`net_profit ≤ 0`）被排除出 3y 均值，对周期/亏损公司进一步高估 payout。

## 8. 兼容性

### 8.1 `payout_anchor` rename

新 payload：

- 输出 `results["payout_M"]`。
- 不输出 `results["payout_anchor"]`。

旧 payload：

- 前端 `TurtlePayloadPanel.vue` 按 `Object.entries(results)` 泛化展示（`signalRows`），仍可显示旧 `payout_anchor`。
- `_plain_turtle_report_prompt` 按完整 FormulaResult schema 泛化 rehydrate（遍历 `raw_results.items()`），旧 key 不需要迁移。

**兼容性已核验**：`payout_anchor` 在代码库中仅被 `tests/unit/test_turtle_calculations.py` 按精确 key 引用；前端、rehydration、`formatting.py`（整体 JSON dump）、所有持久化 payload 测试都按泛化方式读取。因此 rename 对消费侧安全。MongoDB **存量 payload 读时兼容**：rehydration 按存在的 key 重建 `FormulaResult`，旧 `payout_anchor` result 仍能泛化 rehydrate——"不迁移历史 payload"是**读安全**的，不仅是新算路径安全。

测试与 fixture 中**新计算路径**的断言全部迁移到 `payout_M`；同时**保留**至少一个"旧 `payout_anchor` payload 仍可 rehydrate"的回归用例（§10.4）。

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

**实现要点（否则上述不变量会静默失效）**：`collect_fx_currencies`（`calculations.py:99-115`）当前对每个字段 report-first、再 fallback 到 `market.fields.get(name)`；`buyback_amount` 在 `FX_RELEVANT_MONEY_FIELDS` 中，所以当 report 侧无 buyback、市场侧有时，fallback **仍会**采集市场 buyback 币种。需二选一：

1. `market_adapter` 不再发出 `buyback_amount`（彻底从 market facts 移除）；或
2. `collect_fx_currencies` 对 `buyback_amount` **不 fallback 到 market**（只看报告侧 canonical）。

推荐 (2)，保留市场 buyback 作为 context 展示但不入 FX/公式。

**避免静默数值变化**：旧模型里市场侧 buyback 参与公式且 gate FX；新模型把它从公式剔除后，若 report 侧无 buyback，R/GG 的 buyback 项从真实值变 0、FX gate 消失，**数值会变但只有通用 missing 降级 caveat**。因此当"市场侧存在 buyback 记录但报告侧缺失"时，额外发一条 **context-only caveat**：说明市场 action buyback 存在但已被有意排除出 R/GG，以免用户对比历史 run 时看到无解释的数值变化。

这避免"市场 action 已经不参与公式，但仍因其币种缺 FX 导致 payload 降级"的错误。

## 10. 测试计划

### 10.1 report adapter

- `repurchase_of_stock` 被映射为 canonical `buyback_amount`（alias 方向 `extractor_id -> canonical`）。
- 最新期和 historical 每一期都派生 `dividend_payout_ratio_current_year`（断言在 `build_report_facts_from_extraction` 输出的每个 period 上都存在）。
- `dividend_payout_ratio_current_year` 为 reliable，旧 `dividend_payout_ratio_proxy_single_year` 仍为 display-only。
- `HK$` / `HKD` 通过 `normalize_currency(...)` 视为同币。
- `net_profit <= 0` 时**新 reliable 字段** `dividend_payout_ratio_current_year` 不派生并给 caveat（不只是旧 proxy）。
- `dividends_paid` 不可靠时不派生 payout ratio。
- `dividends_paid` 为负数时按 `abs(...)` 计算。

### 10.2 calculations

- `payout_M` 同时有 3 年均值（3/3 期）和 latest signal 时取最大值，且 **status = `complete`**（承诺未应用为 context-only，不降级）。
- 3 年均值仅 2/3 期时 `payout_M` degraded（`..._3y_avg computed from 2/3 periods` 为 material caveat）。
- 只有 3 年均值（latest signal 缺失）时 `payout_M` degraded decisionable。
- 只有 latest signal（3y 均值 <2 期，新上市/仅 1 期）时 `payout_M` degraded decisionable。
- 两者都缺失时 `payout_M` non_decisionable。
- 3 年均值是逐年 ratio 平均，不是 ratio-of-means。
- **新 reliable 字段 `dividend_payout_ratio_current_year` 经 `_number_report_3y_avg` 跨期平均得出 `payout_3y_avg`**（断言均值确实来自逐期字段，而非单期）。
- R/GG 使用报告侧 `buyback_amount` 3 年均值（`buyback_amount_3y_avg`）。
- 报告侧 buyback 缺失时 R/GG 用 0 代入并 degraded，missing input 为 `buyback_amount_3y_avg`，caveat 为 `"buyback_amount_3y_avg missing; treated as 0 for degraded calculation"`（断言旧串 `"buyback_amount missing; ..."` 与旧 key `"buyback_amount"` 已迁移）。
- 只有 buyback 缺失时 HH 不传播 `buyback_amount_3y_avg` missing，HH 状态不因此降级（正面断言 HH 未被阻断）。
- R 与 GG 的 buyback/market_cap 归一到同一 target currency（HH 抵消前提）；跨币种 fixture 下 HH 值正确。
- market-side payout/buyback 存在时不会被公式读取（公式数值不受其影响）。
- **仅有 context-only 市场 action caveat 时，整体 `signals.status` 保持不降级（complete 或仅由真实核心缺口决定）**——这是 §6.2 聚合改写的核心回归用例。
- 改写 `test_compute_turtle_signals_degrades_when_caveats_exist_with_complete_critical_results`（`test_turtle_calculations.py:243`）：拆成 material caveat → degraded、context-only caveat → 不降级 两个用例。
- `results` 不再包含 `payout_anchor`，包含 `payout_M`。

### 10.3 payload FX

- 报告侧 `buyback_amount` 币种需要 FX 时被收集。
- 只有 market-side buyback 币种时不触发 FX。
- **报告侧无 buyback、市场侧有 buyback 且币种不同时，不触发公式 FX**（pin `collect_fx_currencies` 不再 fallback 到市场 buyback 的行为）。
- 此时发出"市场 buyback 存在但已被排除"的 context-only caveat。
- `repurchase_of_stock` alias 后，FX 测试覆盖 canonical `buyback_amount`。

### 10.4 prompt / rehydration / frontend

- prompt 包含分红扣税、回购不扣税说明。
- prompt 包含回购注销进度未验证 caveat。
- prompt 包含承诺支付率未应用说明。
- prompt 包含 `payout_M` 方向性偏置/亏损年排除说明（§7.4）。
- 纯文本 rehydration（`value_analyst._plain_turtle_report_prompt`）能读取新 `payout_M`。
- 旧 payload 的 `payout_anchor` 仍可泛化 rehydrate（保留为回归用例）。
- 前端运行 `npm run type-check` / `npm run build`。因 substitution 保持字符串、results 泛化展示（`Object.entries`），rename 对前端不可见，**type-check/build 即足够，无需额外 render 断言**（若将来改 substitution 为 dict 才需补 render 断言）。

## 11. 实施顺序建议

1. 先改 `report_adapter`：新增 reliable current-year payout，保留旧 proxy，增加 buyback alias。
2. 改 calculation input resolver：封装 payout/buyback resolved structs。
3. 改 `payout_M` 与 R/GG/HH 公式，去掉 market action fallback。
4. 改 caveat/status 分类（§6 共三处）：(a) 每条 caveat 在创建处带 material/context-only 归属；(b) `market_adapter` 自身 status 不被 context-only caveat 降级；(c) `compute_turtle_signals` 聚合改为只读 `CORE_RESULT_KEYS` 的 material 状态、**不再读 `facts.status`（report/market 两半都不读）**，确保 display_only proxy 不阻断 `complete`。
5. 改 FX currency collection，确保市场 action buyback 不触发公式 FX。
6. 改 prompt 文案。
7. 迁移测试和 fixtures。

## 12. 验收标准

- 新 payload 中 `results` 有 `payout_M`，没有 `payout_anchor`。
- `payout_M.substitution` 为**字符串**，可读出 3 年均值、latest signal、`commitment_ratio=null`、`commitment_constraint_applied=false` 和最终选择值。
- 数据齐全（3/3 期 + latest signal）时 `payout_M.status = complete`，整体 `signals.status` 可达 `complete`（承诺未应用不再强制降级）。
- 缺少市场分红/回购 action 数据时，payload 仍展示 context-only caveat，但核心公式与整体状态不因此降级。
- 缺少报告侧回购 3 年均值时，R/GG degraded（missing key `buyback_amount_3y_avg`），HH 不因该项额外 missing、不降级。
- R 与 GG 的 buyback/market_cap 归一到同一 target currency，HH = R − GG 在跨币种场景下数值正确。
- market-side payout/buyback 无法改变 R/GG/HH 数值。
- report-side `repurchase_of_stock` 能进入 O 的 3 年均值与 FX 收集；market-only buyback 不触发公式 FX。
- 测试覆盖 3 年 ratio 平均口径（含 2/3 期降级），防止误改成 ratio-of-means。

## 13. 设计评审修订记录（2026-05-26 multi-agent review）

本节记录评审后对 spec 的修订，便于 plan 阶段追溯。

**已定的设计取舍**：

- **承诺未应用 = context-only**（非 material）。`payout_M`/整体状态在数据齐全时可达 `complete`；`degraded` 仅由真实数据缺口/material caveat 触发；方向性偏置写入 caveat/prompt。（§1/§3.1/§5.1/§6.2）
- **`substitution` 保持 `str`**，审计字段编码进可读字符串，不改类型、不动前端列与 rehydration。（§5.1/§12）

**已修正的事实/落地问题**：

- 移除不存在的 `TurtleSourceReference`，`sources` 用 `list[str]`；数值用 `float`。（§3.3）
- `TURTLE_FIELD_ALIASES` 条目方向修正为 `"repurchase_of_stock": "buyback_amount"`。（§4.3）
- 统一 buyback 3 年均值命名为 `buyback_amount_3y_avg`（missing key / caveat 串 / HH 断言 / 验收一致）。（§5.2–5.4/§10/§12）
- buyback 缺失 caveat 串与 missing key 从 `"buyback_amount ..."` 迁移到 `"buyback_amount_3y_avg ..."`。（§5.2/§10.2）
- `_plain_turtle_report_prompt` 位置标注为 `agents/analysts/value_analyst.py`。（§7/§8.1）

**已补全的设计/约束**：

- "3y avg" 对齐 helper 的 **≥2 期**口径、分母=可用期数、2/3 期 material caveat、亏损年排除上偏。（§4.2/§5.1）
- 新 reliable 字段在 `build_report_facts_from_extraction` 内 per-period 派生。（§4.1）
- `PayoutInputs`/`BuybackInputs` 的 resolver→公式接线、buyback 转币延后到各公式。（§3.3）
- HH 抵消的**共同 target currency 前提** + HH 状态的正面聚合规则（仅排除 buyback 标志）。（§5.4）
- 市场 action caveat 的**确切字符串分类账** + market_adapter 自身 status 不再被 context-only caveat 降级。（§6.1）
- FX：`collect_fx_currencies` 对 `buyback_amount` 不再 fallback 到市场 + 市场 buyback 被排除时发 context-only caveat。（§9）

**已补全的测试覆盖**：context-only caveat 不降级整体（§6.2 核心回归）、单期/2-3 期降级、新字段跨期均值、caveat 串迁移、HH 跨币种正确、market-only buyback 不触发 FX、改写 `test_turtle_calculations.py:243`。（§10）

**遗留为已知限制（记录不修复）**：M 的方向性上偏（承诺上限缺失 + latest signal 回看代理 + 亏损年排除）；R 分红腿/buyback 腿的时间口径混用（上游有意）。

### 13.1 第二轮（changes-review）修订

对上述改动再做一轮 multi-agent review 后的补正：

- **（关键）report 半状态解耦**：`facts.status = merge_status(report.status, market.status)`，而 report 半因 display_only proxy + 无条件 `PAYOUT_PROXY_CAVEAT` 本就几乎永远 degraded（`report_adapter.py:311-318/252`）。仅修 market 半不足以让整体可达 `complete`。已在 §6.1/§6.2 明确：`compute_turtle_signals` 聚合**不读 `facts.status`（两半都不读）**，`facts.status` 展示面可继续 degraded 而不阻断 `signals.status`。（§6.1 第 2 点 / §6.2）
- 修正 `substitution` 消费者描述：`decision.py` 并不读取 `substitution`，实际消费者是 `formatting.py` dump、`value_analyst` rehydration、前端列。（§5.1）
- 修正 `test_turtle_calculations.py:243` 框定：该用例注入的是 **material** caveat（`rf_rate missing`），新规则下仍应 degraded、不会被破坏；真正需要**新增**的是「context-only caveat + 核心 complete → 整体 complete」用例。（§6.2/§10.2）
- `BuybackInputs` 补回 `sources` 字段；厘清 `periods` 与 `_money_hm_report_3y_avg` 非两套机制。（§3.3）
- §9 新增的「市场 buyback 被排除」context-only caveat 已补进 §6.1 分类账；修正 `tax_rate`/`rf_rate` 行号。（§6.1）
- §11 step 4 拆解为三处 status 改动。pinned caveat 字符串引号统一为直引号。

### 13.2 第三轮（plan-review）修订

- **non_decisionable 升级范围收窄**：新增 `DECISION_BLOCKING_KEYS = {payout_M, R, GG}`。只有这些决策性核心公式 `non_decisionable` 才阻断整体；`net_cash_ratio`/`ev_switch`/`cash_protection`/`owner_earnings` 等辅助核心指标 `non_decisionable` 仅降级（保留 Spec 2 前 R/GG-only 阻断范围，避免辅助指标因跨币缺 FX 而阻断本可计算的 R/GG 估值）。（§6.2）
- material caveat 时机：新聚合在末尾读全量 caveats，计算期 material caveat（如 `Unsupported money unit`）现在也会降级——这是相对旧 `has_input_caveats`（开头 snapshot）的有意修正。（§6.2）
