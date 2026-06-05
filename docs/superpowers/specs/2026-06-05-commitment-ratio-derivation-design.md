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

在 `calculations.py` 内派生 `commitment_ratio = min(历年可靠 dividend_payout_ratio_current_year)`，
并按完整形态应用承诺上限，从而消除 `payout_M` 的上偏；数据不足时无损回退到现状。

## 派生与语义（已与用户确认）

### commitment_ratio 派生
- 取值：`commitment_ratio = min(可靠的逐年 dividend_payout_ratio_current_year)`，
  周期集合与 `payout_3y_avg` 相同：`[facts.report, *facts.report.historical.values()]`，
  仅纳入 `reliability == "reliable"` 且为有限数值的年份。
- 阈值：**要求 ≥ 2 个可靠年份**（与 3y_avg 同阈值）。<2 → `commitment_ratio = None`（回退，见下）。
- 周期不足提示：`_number_report_min` **不**自行发"commitment computed from N/3 periods" caveat。
  周期覆盖度已由现有 `payout_3y_avg` 的 `..._3y_avg computed from N/3 periods` caveat 体现（沿用不改）。
  （注：该 3y_avg 周期 caveat 是**动态字符串、不在 `CONTEXT_ONLY_CAVEATS`、按 material 处理**，
  因此 2/3 周期时 `payout_M` 今天就已 `degraded`；这点不变——见下方"状态"说明。）
- `missing_inputs`：commitment **从不阻断决策**。`_number_report_min` 在 <2 可靠年份时返回
  `(None, sources, [])`——**空 missing_inputs**，不得返回任何会进入 payout 状态判定的缺失标签。
- provenance：复用这些年份派息率的 `source_reference` 作为 sources（与 avg_sources 同源，合并去重）。

### 应用承诺上限
- **可派生且 payout_3y_avg 存在**：
  `applied_value = max( min(payout_3y_avg, commitment_ratio), latest_signal )`，
  对 None/非有限值做与现有相同的过滤（只在有限值间取 max）。
- **数学结论（已确认是预期语义）**：`min(series) ≤ mean(series)` 恒成立且二者同源，故
  `min(payout_3y_avg, commitment_ratio) ≡ commitment_ratio`，即
  **`payout_M = max(历年最低派息率, latest_signal)`**——3y_avg 总被锁到最低年份。
  这是有意的保守口径：只信任已验证的派息底线，消除上偏。
- **不可派生（<2 可靠年份，算不出 3y_avg）**：回退到现状
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

- 新增 `_number_report_min(facts, name, caveats)`：镜像 `_number_report_3y_avg` 的"收集可靠逐年值"
  逻辑，返回 `(min_value | None, sources, missing_inputs)`，同样 ≥2 阈值、<3 加周期 caveat。
- 改 `_resolve_payout_inputs`：派生 commitment_ratio、按上文应用上限、调整 caveat/状态/sources。
- 改 `compute_turtle_signals` 中 `payout_M` 的 `formula`/`substitution` 构造（`:565-590` 一带）。
- 改 `decision.py` 的 prompt caveat。
- 把新 context caveat 字面量加入 `CONTEXT_ONLY_CAVEATS`。

## 非目标

- 不抽取/派生 `buyback_amount_3y_avg`（已存在）或逐年 buyback（属上游）。
- 不抽取 `buyback_cancellation_progress`、`commitment_ratio` 的"政策声明"文本。
- 不改 R/GG/owner_earnings/net_cash_ratio 公式。
- 不改 `report_adapter.py`。

## 测试（扩展现有 turtle 测试套件）

1. **应用上限（3/3 周期）**：构造 **3 个**可靠年份且 latest < 最低年份的场景，断言
   `payout_M == max(min_year, latest)`，substitution 含 `commitment_constraint_applied=true`，
   `COMMITMENT_CONTEXT_CAVEAT` 不再出现，`COMMITMENT_APPLIED_CONTEXT_CAVEAT` 出现，且
   `payout_M.status == complete`（验证固定字面量 caveat 未被判为 material，未引入新降级）。
   附加：2/3 周期场景断言应用上限生效但 `payout_M.status == degraded`（降级来自既有周期 caveat，
   非来自承诺）。
2. **latest 更高**：latest_signal > 最低年份时，`payout_M == latest_signal`。
3. **回退**：仅 1 个可靠年份 → `commitment_ratio=None`，`payout_M == max(payout_3y_avg, latest)`
   （现状），`COMMITMENT_CONTEXT_CAVEAT` 保留，状态不变。
4. **substitution 字符串**：应用/回退两种分支的字符串内容正确（含 `commitment_constraint_applied`
   true/false 与数值）。
5. **下游传导**：应用上限使 `payout` 下降 → `R`/`GG` 数值相应下降（与未应用对照）。
6. **全相等年份**：各年派息率相等时 min==avg，`payout_M` 数值与回退一致（但走的是应用分支）。
7. **不降级**：3/3 周期、输入齐全时，应用上限的 `payout_M.status` 仍为 `complete`。

### 需更新的既有测试（实现时同步改）
substitution / formula / caveat 是字符串契约，下列断言会随本改动失效，需更新为新分支预期：
- `tests/unit/test_turtle_calculations.py`：约 :121-122、:141-143（`commitment_ratio=null`、
  `) = 0.5;`、`) = 0.8;` 等 substitution 断言——按 3/3 周期会走应用分支，需改为 applied 预期）。
- `tests/unit/test_turtle_decision.py`：约 :66（fixture 里写死的
  `commitment_ratio=null, commitment_constraint_applied=false`）、:81（断言 `COMMITMENT_CONTEXT_CAVEAT`
  文案——若 fixture ≥2 可靠年份则应改为 `COMMITMENT_APPLIED_CONTEXT_CAVEAT`）。
- `tests/unit/test_turtle_value_analyst_integration.py`：约 :620-621（写死的 `formula`/substitution）、
  :635（`startswith("payout_M = max")` 仍成立，确认即可）。

实现计划必须先判定这些 fixture 用了几个可靠年份，再决定它们落在应用分支还是回退分支，并相应更新断言。

## 风险

- 周期 caveat 字面量必须精确加入 `CONTEXT_ONLY_CAVEATS`，否则会误判为 material 而降级。
- `substitution` 是字符串契约（已有测试断言其内容），改动需同步更新相关断言。
- 语义变化（payout_M 锁到最低年份）会改变历史可比性——属预期，已与用户确认。
