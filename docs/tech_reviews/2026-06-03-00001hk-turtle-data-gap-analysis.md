# 00001.HK Turtle 数据缺口分析留档

日期：2026-06-03  
范围：价值投资分析 / Turtle v0.15 / 00001.HK  
结论权限：不可决策报告。由于当次结果中 `TurtleComputedSignals.status = non_decisionable`，不得形成最终投资判断、买入/卖出/持有建议或估值结论。

## 1. 总体状态

当次分析的关键状态：

- `TurtleFacts.status`: `degraded`
- `TurtleComputedSignals.status`: `non_decisionable`

这不是单纯的展示数据不足，而是 Turtle 核心公式输入存在不可计算或不可靠字段。计算层中 `payout_M`、`R`、`GG` 属于决策阻断项；任一关键输入缺失或不可用，都可能把整体信号状态拉到 `non_decisionable`。

## 2. 数据缺口分级

| 数据缺口 | 重要性 | 主要影响 | PDF + LLM 修复后可恢复性 |
| --- | --- | --- | --- |
| `net_profit` 只能 `display_only` | 核心 | 直接阻断 `R`，并连带阻断 `HH`；也会影响分红率派生 | 大概率可恢复。PDF 可定位后，LLM 可重新抽取带币种、单位、页码证据的净利润；但字段必须通过可靠性策略，且 LLM 模型需在允许列表内 |
| `dividend_payout_ratio_current_year` 缺失 | 核心 | `payout_M` 无法形成，进而阻断 `R/GG/HH` | 大概率可恢复。该字段可由 `dividends_paid / net_profit` 派生，前提是两者均为可靠 money 字段 |
| `dividend_payout_ratio_current_year_3y_avg` 缺失 | 重要 | 影响 `payout_M` 三年口径；若只有当年值，最多为 degraded；若当年值也缺失，则 non_decisionable | 可恢复。需要至少两个年度的可靠分红率；PDF + LLM 可补历史年报字段 |
| `interest_bearing_debt` 缺失 | 重要 | 阻断 `net_cash_ratio / ev_switch / cash_protection`，影响现金保护和企业价值切换判断 | 大概率可恢复。可直接抽取有息负债，或由 `st_borr + lt_borr + bond_payable` 派生 |
| `bond_payable` 不可用或不可靠 | 重要 | 是 `interest_bearing_debt` 派生失败的具体原因之一；缺失会避免静默低估有息负债 | 大概率可恢复。年报债务附注通常可提供，但取决于 PDF 表格结构和证据可验证性 |
| `buyback_amount_3y_avg` 缺失 | 次要到中等 | 代码会按 0 做 degraded 计算；不会单独导致 non_decisionable，但会低估回购贡献 | 部分可恢复。LLM 可抽回购金额；但回购注销进度/可计入性仍可能保留 caveat |

## 3. 决策阻断主因

当次不可决策的主线是：

1. `net_profit` 不可靠：字段存在但只能展示，不能进入计算。
2. 分红率缺失：`dividend_payout_ratio_current_year` 和三年平均口径均不可用。
3. 由于 `payout_M` 无法形成，`R/GG/HH` 的核心公式链条被阻断。

`interest_bearing_debt` 也很重要，但它主要影响现金保护相关结果，不是当前 `signals.status = non_decisionable` 的直接主因。`buyback_amount_3y_avg` 的缺失会降低结果质量，但当前计算层允许按 0 做降级计算，因此优先级低于净利润和分红率。

## 4. PDF 与 LLM 修复后的预期

PDF resolver 修复后，解决的是 LLM 补充抽取无法读取年报 PDF 的入口问题。它是必要条件，但不是充分条件。

修复后仍需同时满足：

- 当前分析请求必须注入项目侧 deep-role LLM 配置。
- `llm_config_path` 应由项目配置桥接生成，而不是手工改成 extractor 根目录相对路径。
- 抽取使用的 LLM 模型必须进入 `FINANCIAL_REPORT_ALLOW_LLM_MODELS` 或等价允许列表，否则 LLM 字段仍只能展示，不能参与 Turtle 计算。
- 抽取字段必须带可靠的币种、单位、数值和证据页码，否则仍会被降级为 `display_only`。

如果上述条件成立，最可能恢复的字段顺序为：

1. `net_profit`
2. `dividends_paid`
3. `dividend_payout_ratio_current_year`
4. 历史期 `dividend_payout_ratio_current_year`
5. `bond_payable` / `interest_bearing_debt`
6. `buyback_amount`

## 5. 后续验证重点

后续重新跑 00001.HK 分析时，应重点检查：

- `report.fields.net_profit.reliability` 是否从 `display_only` 变为 `reliable`。
- `report.fields.dividend_payout_ratio_current_year` 是否出现。
- `report.historical` 是否至少保留两个可用年度，并能形成 `dividend_payout_ratio_current_year_3y_avg`。
- `signals.results.payout_M.status` 是否不再是 `non_decisionable`。
- `signals.results.R.status` 和 `signals.results.GG.status` 是否至少达到 `degraded`，理想情况下达到 `complete`。
- `signals.status` 是否从 `non_decisionable` 下降级恢复为 `degraded` 或进一步恢复为 `complete`。

在 `signals.status` 未恢复前，输出仍必须保持不可决策口径。
