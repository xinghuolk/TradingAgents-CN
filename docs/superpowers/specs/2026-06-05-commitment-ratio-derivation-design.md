# 在分析层派生 commitment_ratio 并应用承诺上限

- 日期：2026-06-05
- 范围：单个 PR
- 边界：`tradingagents/dataflows/value_investment/turtle/`（Apache 2.0）

## 背景

价值投资（Turtle）模型中，派息率信号的完整形态是：

```
payout_M = max( min(payout_3y_avg, commitment_ratio), latest_signal )
```

`commitment_ratio` 是对 3 年平均派息率的**上限（承诺上限）**。Turtle Spec 2
（`docs/superpowers/specs/2026-05-26-turtle-model-recalibration-design.md` §3.1）当时**未实现**它，
留作 "future hook"，并把"承诺未应用"标记为 context-only caveat（不降级）。其代价是
`payout_M`（及下游 `R`/`GG`）存在**系统性偏高**（去掉上限等价于 `min(x, +∞)=x`）。

代码现状（`tradingagents/dataflows/value_investment/turtle/calculations.py`）：
- `PayoutInputs.commitment_ratio` 恒为 `None`（`:480`）。
- `_resolve_payout_inputs` 计算 `applied_value = max(payout_3y_avg, latest_signal)`（`:466-467`），
  并追加 `COMMITMENT_CONTEXT_CAVEAT`（`:464`）。
- `payout_M` 的 `formula`/`substitution` 写死 `commitment cap not applied` /
  `commitment_ratio=null, commitment_constraint_applied=false`（`:572-583`）。
- `decision.py:73-77` 的 prompt caveat 声明 commitment 未抽取、payout_M 可能偏高。

**职责澄清**：`commitment_ratio` 不是上游 `financial-report-llm-extractor` 的原始抽取字段，
而是**本分析层基于历年派息率的派生/聚合**。本 PR 让本层自己派生它并应用承诺上限。
（`buyback_amount_3y_avg` 已在本层派生 `_money_hm_report_3y_avg`，不在本 PR 范围；其降级源于
上游缺少逐年 `repurchase_of_stock`/`buyback_amount`，属上游抽取，不在此处理。）

## 目标

在 `calculations.py` 内派生 `commitment_ratio = min(历史年可靠 dividend_payout_ratio_current_year，
排除最新年)`，并按完整形态应用承诺上限，从而消除 `payout_M` 的上偏；数据不足时无损回退到现状。

## 派生与语义（已与用户确认）

### commitment_ratio 派生
- 取值：`commitment_ratio = min(可靠的逐年 dividend_payout_ratio_current_year)`，
  周期集合**仅历史年**：`facts.report.historical.values()`（**排除** `facts.report` 最新年），
  仅纳入 `reliability == "reliable"` 且为有限数值的年份。
- **为什么排除最新年**：`latest_signal` 就是最新年的派息率，若把它纳入 min，则
  `commitment_ratio ≤ latest_signal` 恒成立，整条公式退化为 `payout_M ≡ latest_signal`，派生形同虚设。
  只对历史年取 min，cap 才能真正约束被高年份抬高的均值，同时 `latest_signal` 仍可上调。
- 阈值：**要求 ≥ 2 个历史可靠年份**。<2 → `commitment_ratio = None`（回退，见下）。
  （即至少需要"最新年 + 2 个历史年" = 3 年数据，cap 才生效。）
- 周期不足提示：`_number_report_min` **不**自行发"commitment computed from N/3 periods" caveat。
  周期覆盖度已由现有 `payout_3y_avg` 的 `..._3y_avg computed from N/3 periods` caveat 体现（沿用不改）。
  （注：该 3y_avg 周期 caveat 是**动态字符串、不在 `CONTEXT_ONLY_CAVEATS`、按 material 处理**，
  因此 2/3 周期时 `payout_M` 今天就已 `degraded`；这点不变——见下方"状态"说明。）
- `missing_inputs`：commitment **从不阻断决策**。`_number_report_min` 在 <2 历史可靠年份时返回
  `(None, sources)`——**不贡献任何 missing 标签**，不得返回会进入 payout 状态判定的缺失项。
- provenance：复用这些历史年份派息率的 `source_reference` 作为 sources（合并去重）。

### 应用承诺上限
- **可派生（≥2 历史年）且 payout_3y_avg 存在**：
  `applied_value = max( min(payout_3y_avg, commitment_ratio), latest_signal )`，
  对 None/非有限值做与现有相同的过滤（只在有限值间取 max）。
- **语义（非退化）**：`commitment_ratio` 取自历史年（不含最新年），与 `payout_3y_avg=mean(最新+历史)`
  并非同源，故 `min(payout_3y_avg, commitment_ratio)` 是一次**真正的取小**。当某高年份把均值抬高、
  且最新年不足以救回时，cap 把均值压到历史底线，从而消除上偏。
  示例：latest=0.4、历史=[0.5,0.9] → 今天 `max(avg=0.6, 0.4)=0.6`；
  应用后 `max(min(0.6, 历史最低=0.5), 0.4)=0.5`（压低）。
- **不可派生（<2 历史可靠年份）**：回退到现状
  `applied_value = max(payout_3y_avg, latest_signal)`，保留 `COMMITMENT_CONTEXT_CAVEAT`
  （context-only，不降级）。与现有行为完全一致。

### 状态与 caveat
- 应用上限是**精化**，**永不**因此降级 `payout_M`；现有降级规则（avg/latest 缺失或存在
  material caveat）不变。
- 应用上限时：**移除** `COMMITMENT_CONTEXT_CAVEAT`（它声称"未应用"），改为追加一个**固定字面量**
  context-only caveat，新增常量
  `COMMITMENT_APPLIED_CONTEXT_CAVEAT = "commitment cap applied; commitment_ratio = min of historical payout ratios"`，
  并把它加入 `CONTEXT_ONLY_CAVEATS` 集合（`calculations.py:35-46`）。
  **关键**：`CONTEXT_ONLY_CAVEATS` 是精确匹配集合（`_is_context_only_caveat` = `caveat in 集合`），
  因此该 caveat **必须是不含动态数值的固定字符串**，否则会被判为 material 而降级。
  commitment_ratio 的具体数值与周期数放进 **substitution 字符串**（见下），不放 caveat。
- **应用上限不能"反降级"**：2/3 周期时，`payout_3y_avg` 的动态周期 caveat 已使 `payout_M`
  `degraded`；应用承诺上限不会、也不应把它改回 `complete`。"永不降级"指的是"不因应用上限而**新增**降级"。
  仅当 3/3 周期（无周期 caveat）且 avg/latest 齐全时，应用上限的 `payout_M.status == complete`。
- 未应用（回退）时：`COMMITMENT_CONTEXT_CAVEAT` 原样保留。

### caveats 列表的双写（实现要点）
`_resolve_payout_inputs` 既会 mutate 外层共享的 `caveats` 列表（最终进入
`TurtleComputedSignals.caveats`），又会返回自己的 `PayoutInputs.caveats`。两者都要按分支一致处理：
- **必须在 `_append_caveat(caveats, COMMITMENT_CONTEXT_CAVEAT)` 之前分支**：
  - 应用分支：**不要**把 `COMMITMENT_CONTEXT_CAVEAT` 写入外层 `caveats`；改为写入
    `COMMITMENT_APPLIED_CONTEXT_CAVEAT`；`PayoutInputs.caveats` 的 `_merge_sources(...)` 末项
    也相应换成 `COMMITMENT_APPLIED_CONTEXT_CAVEAT`。
  - 回退分支：维持现状，两处都用 `COMMITMENT_CONTEXT_CAVEAT`。

### formula / substitution 字符串
- `formula` 改为完整形态：`payout_M = max(min(payout_3y_avg, commitment_ratio), latest_signal)`。
- `substitution`：
  - 应用：`payout_M = max(min(payout_3y_avg=X, commitment_ratio=Y), latest_signal=Z) = V; commitment_constraint_applied=true`
  - 回退：保留现有 `... commitment_ratio=null, commitment_constraint_applied=false`。
- `PayoutInputs.commitment_ratio` 填入派生值或 None。

### decision.py prompt caveat（`:73-77`）
- 改为：commitment_ratio 现由本层派生（历年派息率最小值）并作为承诺上限应用；
  仅在可靠年份 <2 无法派生时回退为 `max(payout_3y_avg, latest_signal)`，此时 payout_M / R/GG
  可能偏高（保留原偏置说明，仅用于回退分支）。

## 下游影响

`R` / `GG` 通过 `payout_inputs.applied_value`（即 `payout`）消费派息率，应用上限后 `payout` 下降
→ `R`/`GG` 更保守。**无需改动 R/GG 代码**，只是输入变化。这正是消除上偏的目的。

## 架构与实现位置（Approach A）

跨周期聚合都在 `calculations.py`（`_number_report_3y_avg`、`_money_hm_report_3y_avg`），因此
`commitment_ratio` 也放这里——**不**放 `report_adapter.py`（那里是逐周期字段派生，如
`interest_bearing_debt`，不适合跨周期 min）。

- 新增 `_number_report_min(facts, name)`：镜像 `_number_report_3y_avg` 的"收集可靠逐年值"逻辑，
  但**只遍历 `facts.report.historical.values()`（不含 `facts.report`）**，返回 `(min_value | None, sources)`；
  阈值 **≥2 历史可靠年**，**不**发任何周期 caveat、**不**返回 missing 标签。
- 改 `_resolve_payout_inputs`：派生 commitment_ratio、按上文应用上限、调整 caveat/状态/sources。
- 改 `compute_turtle_signals` 中 `payout_M` 的 `formula`/`substitution` 构造（`:571-590` 一带）。
- 改 `decision.py` 的 prompt caveat。
- 把新 context caveat 字面量加入 `CONTEXT_ONLY_CAVEATS`。

## 非目标

- 不抽取/派生 `buyback_amount_3y_avg`（已存在）或逐年 buyback（属上游）。
- 不抽取 `buyback_cancellation_progress`、`commitment_ratio` 的"政策声明"文本。
- 不改 R/GG/owner_earnings/net_cash_ratio 公式。
- 不改 `report_adapter.py`。

## 测试（扩展现有 turtle 测试套件）

1. **应用上限生效（cap 压低）**：latest=0.4、历史=[0.5,0.9]（2 历史年），断言
   `payout_M == 0.5`（= `max(min(avg=0.6, 历史最低=0.5), latest=0.4)`，低于今天的 0.6），
   substitution 含 `commitment_constraint_applied=true`，`COMMITMENT_CONTEXT_CAVEAT` 不出现，
   `COMMITMENT_APPLIED_CONTEXT_CAVEAT` 出现，`payout_M.status == complete`（3 年齐全、无周期 caveat、
   固定字面量 caveat 未被判 material）。
2. **latest 更高时不被压低**：latest=0.8、历史=[0.4,0.6] → `min(avg, 历史最低=0.4)=0.4`，
   `payout_M == max(0.4, 0.8) == 0.8`（应用分支，但 latest 占优）。
3. **回退（历史年 <2）**：只有 1 个历史年（如 `report_history(0.4, None)`）→ `commitment_ratio=None`，
   走回退分支，`payout_M == max(payout_3y_avg, latest)`（现状），substitution 含
   `commitment_ratio=null`，`COMMITMENT_CONTEXT_CAVEAT` 保留，status 与今天一致。
4. **substitution 字符串**：应用/回退两分支字符串内容正确（应用含 `commitment_constraint_applied=true`
   且含 `commitment_ratio=<数值>`；回退含 `commitment_ratio=null, commitment_constraint_applied=false`）。
5. **下游传导**：用第 1 题 fixture，断言应用上限使 `R`/`GG` 较"未应用对照（latest=0.4 但无历史 floor）"更低。
6. **全相等年份**：历史年与最新年派息率全相等时，`min(avg, 历史最低)==avg`，`payout_M` 数值与回退一致
   （但走应用分支，substitution 标 applied）。
7. **不降级**：第 1 题场景 `payout_M.status == complete`。

### 需更新的既有测试（已逐一核对）
- `tests/unit/test_turtle_calculations.py`：
  - `base_facts()` 的历史是 `report_history(0.4, 0.6)`（**2 个历史年**）→ 进入**应用分支**。
  - **会失败需改**：仅 **:121** `assert "commitment_ratio=null" in payout_M.substitution`
    （应用分支里是 `commitment_ratio=0.4...`）。改为断言 `commitment_constraint_applied=true` /
    `commitment_ratio=0.4`。
  - **仍通过（核对即可，勿改）**：:119/:120（payout_M==0.5、status complete，因 latest=0.5>commitment=0.4，
    值不变）、:122 `) = 0.5;`、:123-126（R/GG 不变）、:141-143、:179、:193-196
    （`report_history(0.4, None)` 只有 1 历史年 → 回退分支，断言维持现状）。
- `tests/unit/test_turtle_decision.py`：:62-83 的 formula/substitution/caveats 是**测试自造的 fixture 入参**
  （不调用 compute），断言只检查 prompt 文案（:87-94）。只要 `decision.py` 改写后仍包含
  `commitment_ratio`、`payout_M`、`可能偏高` 等词，即**保持通过**，无需改该测试。
- `tests/unit/test_turtle_value_analyst_integration.py`：:612-635 同为 fixture 自造 + `startswith("payout_M = max")`，
  新 substitution 仍以 `payout_M = max` 开头，**保持通过**，无需改。
- `tests/unit/test_turtle_value_analyst_integration.py`：约 :620-621（写死的 `formula`/substitution）、
  :635（`startswith("payout_M = max")` 仍成立，确认即可）。

实现计划必须先判定这些 fixture 用了几个可靠年份，再决定它们落在应用分支还是回退分支，并相应更新断言。

## 风险

- 周期 caveat 字面量必须精确加入 `CONTEXT_ONLY_CAVEATS`，否则会误判为 material 而降级。
- `substitution` 是字符串契约（已有测试断言其内容），改动需同步更新相关断言。
- 语义变化（payout_M 锁到最低年份）会改变历史可比性——属预期，已与用户确认。
