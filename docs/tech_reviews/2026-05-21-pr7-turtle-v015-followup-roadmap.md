# Turtle v0.15 Follow-up Roadmap

- **创建日期**：2026-05-21
- **工作分支**：`fix/turtle-v015-review-followups`
- **问题来源**：
  - `docs/tech_reviews/2026-05-21-pr7-turtle-v015-value-analyst-review.md`（综合评审）
  - `docs/tech_reviews/2026-05-21-pr7-turtle-calculation-and-source-review.md`（计算与数据源评审）

> 本文档协调 PR #7 评审的所有 follow-up 工作。两份评审共识别 13+ 条问题，跨越代码正确性、金融模型口径、数据通道、UI 可观测性。本文档把它们拆分为 4 个独立 spec，每个独立走 `spec → plan → 实施` 循环。

---

## 1. 拆分理由

强行打包成单一 spec 会造成：

- spec 文档过长、决策点过多 → 难以审阅
- plan 任务跨越多种风险等级 → 难以独立验证
- 模型变更（A.1 / A.2）一旦改了就影响所有下游测试基线，必须先 freeze 再改其他

因此按"风险隔离 + 单一关注点"原则拆分。

## 2. Spec 拆分

| Spec | 主题 | 覆盖评审条目 | 风险 | 规模 | 依赖 |
|------|------|--------------|------|------|------|
| **Spec 1** | correctness-fixes | 综合 2.1 / 2.2 / 2.3；计算 A.4 / A.5 / A.6 / B.1 + B.1 附（DEFAULT_CHANNEL_CAVEAT）/ B.3；D 章 backend 透传 + AgentState schema + 持久化空内容短路 | 低 | 中 | 无 |
| **Spec 3** | data-channels | 计算 A.3 跨币 FX；B.2 market source provider；B.4 FX metadata；**multi-period extraction（新增，来自 Spec 2 brainstorming 揭示的上游 M / O 需要 3 期数据）**；**承诺支付率字段（新增，extractor 扩展或 dividend_policy_text 二次提取）** | 中（新数据通道 + 跨期契约） | 中-大 | 与 Spec 1 无强依赖，可并行 |
| **Spec 2** | model-recalibration | 计算 A.1 时间口径 + M 完整算法（`max(min(3y avg, 承诺), 新信号DPS)`）；A.2 税务口径 prompt 文档化；A.7 payout_anchor 重命名；buyback O 切到 extractor 3y 均值 | 中（改变数值） | 中（公式实施 + fixture migration） | **依赖 Spec 3 multi-period 数据通道**；调整后 Spec 2 不能与 Spec 3 并行 |
| **Spec 4** | turtle-data-view-frontend | D 章 frontend tab（数据 / 计算 / 状态 Tab） | 中（proprietary、UX 设计） | 大 | **依赖 Spec 1 的 backend 透传**完成 |

**已并入相邻 spec 的化妆性条目**：

- A.6 `abs(capex)` 文档化 → 并入 Spec 1（更新 prompt 说明）
- B.5 sources list 过长 → 并入 Spec 1 或 Spec 4（dedup 在 formatting.py 或前端展示层处理）

## 3. 推荐实施顺序

```
        ✅ Spec 1 (correctness-fixes, merged #8)
                │
        ┌───────┴───────┐
        ▼               ▼
    🟡 Spec 3       ⬜ Spec 4
    (data-channels)  (frontend tab — Spec 1 后立即可启动)
        │
        ▼
    ⬜ Spec 2 (model-recalibration — 依赖 Spec 3 multi-period)
```

**关键路径变更**（Path C 决策，2026-05-22）：

- 原计划 Spec 2 / 3 可并行；上游对比 subagent 发现 Spec 2 的 R/GG 公式需要 multi-period 数据（M = max(min(3y avg, 承诺), 新信号), O = 3y 平均），multi-period extraction 自然归属 Spec 3 的数据通道工作
- **新顺序：Spec 1 → Spec 3 → Spec 2**（公式实施依赖数据就位）
- Spec 4（frontend tab）与 Spec 3 互相独立，可并行启动

单线推进时按以下顺序：

1. **Spec 1**（correctness-fixes）✅ —— 已完成
2. **Spec 3**（data-channels）—— **当前进行**；建立 multi-period 数据通道 + FX 跨币支持
3. **Spec 2**（model-recalibration）—— Spec 3 完成后实施；R/GG 公式完整对齐上游
4. **Spec 4**（turtle-data-view-frontend）—— 用户感知最强；可与 Spec 3 并行启动

## 4. 状态追踪

**状态图例**：⬜ 未开始 | 🟡 brainstorming 中 | 🟢 spec 已通过 | 🔵 plan 已通过 | 🟠 实施中 | ✅ 已完成（merged）

| Spec | 状态 | Spec 文档 | Plan 文档 | PR | 备注 |
|------|------|----------|-----------|-----|------|
| Spec 1：correctness-fixes | ✅ | `docs/superpowers/specs/2026-05-21-turtle-correctness-fixes-design.md` | `docs/superpowers/plans/2026-05-21-turtle-correctness-fixes.md` | [#8](https://github.com/xinghuolk/TradingAgents-CN/pull/8) | merged 2026-05-22；30 commits / 379 tests green |
| Spec 3：data-channels | 🟡 | — | — | — | brainstorming 启动；范围扩张含 multi-period + 承诺支付率；分支 `feat/turtle-spec3-data-channels` |
| Spec 2：model-recalibration | ⬜ | — | — | — | **暂缓**，等 Spec 3 multi-period 就位后实施；brainstorming learnings 见 §5 |
| Spec 4：turtle-data-view-frontend | ⬜ | — | — | — | 阻塞于 Spec 1 backend 部分 |

文档路径约定：

- Spec：`docs/superpowers/specs/2026-05-XX-turtle-<topic>-design.md`
- Plan：`docs/superpowers/plans/2026-05-XX-turtle-<topic>.md`

每完成一个 spec / plan / PR，回填本表。

## 5. Spec 范围明细

> 详细 brainstorming 与设计决策在各 spec 自己的文档里。本节只列条目映射，便于复核。

### Spec 1：correctness-fixes

> 决策已定，详见 `docs/superpowers/specs/2026-05-21-turtle-correctness-fixes-design.md`。本节是范围索引，不再列备选方案。

修复项（10 项）：

- **综合 2.1 redaction**（`decision.py`）—— 完全删除 `_safe_non_decision_text` 与 `_SOURCE_TEXT_REDACTIONS`；护栏移到 `build_turtle_decision_prompt` 提示词
- **综合 2.2 facts.status 硬编码 "complete"**（`turtle_analysis_tool.py`）—— Adapter-emitted：`TurtleReportFacts` / `TurtleMarketFacts` 各自携带 status，工具层用 `merge_status` 聚合
- **综合 2.3 tool signature drift**—— `turtle_analysis_tool.py` 的 `company_name` 类型对齐为 `str = ""`
- **计算 A.4 三处死代码**（`calculations.py` line 344-346 / 370-372 / 411-412）—— 删除不可达分支；附带删相邻 `!= 0` 冗余检查
- **计算 A.5 ev_switch / cash_protection 的 degraded 与 value=None 矛盾**—— 删除会输出 "status=degraded、value=None" 的误导性分支（Fail-fast 下 `net_cash_ratio` 不做单边降级）
- **计算 A.6 `abs(capex)` 文档化**—— 在 `build_turtle_decision_prompt` 的输出结构第 2 项加一行说明
- **计算 B.1 `tax_rate` 默认渠道下仍 reliable**（`market_adapter.py`）—— 显式 channel 时 reliable，默认 channel 时降级为 display_only
- **计算 B.1 附：DEFAULT_CHANNEL_CAVEAT 无条件追加**（`market_adapter.py:245`）—— 删除该无条件 `_append_caveat`，避免 market.status 永远 degraded
- **计算 B.3 单年度 payout proxy 名字撞车 bug**（`report_adapter.py`）—— 重命名为 `dividend_payout_ratio_proxy_single_year` + 降级 display_only；附带消除 report-side proxy 与 market-side 真 3y 数据**写同一 key、`_field` report 优先静默覆盖**的撞车 bug
- **D 章 backend 透传 `value_turtle_payload`**—— `AgentState` TypedDict 加字段、`propagation.py` InitialState 加 `""`、`value_analyst_node` 所有写 `value_report` 的 return 路径都带 payload（unsupported 返 `""`）、`simple_analysis_service.py` 持久化层加"内容非空"短路 + 新增 `value_turtle_payload.json` 配置

### Spec 3：data-channels（**当前进行中**，Path C 决策后扩张范围）

修复项 + 新增：

- **计算 A.3 跨币 FX 通道未打通**—— 选 FX provider；写入 `facts.report.metadata["fx_rates"]`；明确 pair 方向约定（`HKD:CNY` = 1 HKD 兑 X CNY 等）
- **计算 B.2 市场 source_reference 缺 provider**—— `market_adapter` 携带 `info["source"]` + timestamp 进 source_reference
- **计算 B.4 FX metadata 缺 provider/timestamp**—— FX 信息同样携带来源与时间戳
- **multi-period extraction（Path C 新增）**—— FinancialReportClient 跨期调用：3 期 period_end 串行 / 并发 + 跨期失败覆盖（"3 期里 2 期 reliable"行为）+ 缓存策略 + 跨期单位/货币一致性校验
- **承诺支付率字段（Path C 新增）**—— extractor 字段表中无对应字段；需选其一：(a) 扩展 extractor `dividend_commitment_ratio` 字段（上游协调）；(b) 从已有 `dividend_policy_text` LLM 二次提取；(c) 暂时不抓，Spec 2 公式里降级为"无承诺值时跳过 M 算法的 min(...)"

### Spec 2：model-recalibration（**暂缓**，等 Spec 3 完成）

修复项（**与 Path C 前的初版差异较大，因为 brainstorming + 上游对比揭示了更精细的公式**）：

- **计算 A.1 时间口径完整对齐上游**：
  - R = `(C × M × (1 - Q%) + O) / market_cap × 100`
  - C = 当年归母净利润（snapshot，与上游因子 2 一致）
  - **M = max(min(近 3 年支付率均值, 承诺支付率), 新信号 DPS 调整值)** —— 完整算法，依赖 Spec 3 的 multi-period payout 数据 + 承诺支付率字段
  - **O = 注销型回购年均金额（过去 3 年）** —— 依赖 Spec 3 multi-period buyback 数据
  - Q = 税率（按 holding_channel 查表，Spec 1 已就位）
- **计算 A.2 分红 / 回购税务口径不对称**—— Q2=A2-1：prompt 文档化"注销型回购对继续持有股东无即时税务事件，因此 `+ buyback` 不扣税；与上游'注销型回购不做税务折扣'明示一致"
- **计算 A.7 `payout_anchor` 别名问题**—— Q3 sub：保留在 `results` dict 并 rename 为 `payout_M` 或 `payout_anchor_value`（具体留 Spec 2 brainstorming 时再定，需要反映 M = max(min(...), ...) 的最终值）

#### Path C brainstorming learnings（保存自 2026-05-22 暂缓前的工作）

来自 `tradingagents/.../turtle_framework/龟龟投资策略_v0.15/phase3_分析与报告.md` 与 subagent 对比验证：

| Brainstorming Q | 决策 | 对齐上游？ | 备注 |
|----|----|---|----|
| Q1：模型口径 | A. 当期 snapshot | ⚠️ 部分 | R 的 C 是 snapshot 对齐；但 M、O 是 3y 平均，**不是纯 snapshot**——上游是有意的时间口径混用 |
| Q2：税务口径 | A2-1. 文档化不对称 | ✅ | 上游原文"注销型回购不做税务折扣"完全对齐 |
| Q3：payout PRIMARY 来源 | A3-1. report-side 升 PRIMARY | ✅ 方向 | 上游明确"payout 来自年报附注同币种"；但简化为 current_year = dividends/profit 未实现完整 M = max(min(3y avg, 承诺), 新信号) |
| Q4：buyback 数据源 | A4-4. extractor `repurchase_of_stock` | ✅ 数据源对齐 | 数据源对齐；但简化为当年值，**未实现 O = 3y 平均** |
| Q5：akshare records 处置 | A5-1. 保留为 display_only | ✅ | 上游对回购历史用于定性分析（价值陷阱排查） |

#### 已知次要偏离（Spec 2 文档化或留未来 spec）

- **GG 公式分子口径**：上游因子 3 精算 GG 分子是 bottom-up 现金流量表逐行追踪的 3y 均值（AA）；当前实现是 `OCF - abs(Capex)` 单年近似——本质上是因子 2 的近似 GG，不是上游因子 3。Spec 2 文档化此简化；完整 AA 追踪需要更复杂的现金流逐行解析，留**未来 Spec**（可能 Spec 5）
- **net_cash_ratio 分子口径**：上游用"广义净现金"（含定期存款、理财、其他流动性等价物）；当前用狭义 `cash - interest_bearing_debt`。对持有大量定期存款的公司（如部分物业管理）影响 ev_switch / cash_protection 偏保守。Spec 2 评估是否在范围（如果 extractor 能拿到 wealth_management / time_deposits 字段，可顺手扩；否则文档化偏离）

### Spec 4：turtle-data-view-frontend

修复项：

- **D 章 frontend tab**：报告 / 数据 / 计算 / 状态四个 Tab
- 数据 Tab：TurtleFacts 字段表（按 report / market 分组、可点击 PDF 页码定位）
- 计算 Tab：TurtleComputedSignals 公式表
- 状态条：facts.status + signals.status 高亮
- 与 Spec 1 backend 透传配套；前端属 proprietary `frontend/`

## 6. 使用本路线图

1. 每开始一个 spec 的 brainstorming，把状态表更新为 🟡，写入 Spec 文档路径
2. brainstorming 结束写出 spec 时，更新为 🟢
3. writing-plans 完成时，更新为 🔵 并写入 Plan 文档路径
4. 实施开始更新为 🟠，PR merged 后更新为 ✅ 并填 PR 链接
5. 任何 spec 之间发现新的依赖或共享设计决策，在此文档"备注"列记录

## 7. 当前进度

- **当前焦点**：Spec 1 已 merge（PR #8）。**路线调整（Path C，2026-05-22）**：subagent 对比上游文档发现 Spec 2 R/GG 公式需要 multi-period 数据，把 multi-period extraction 推给 Spec 3 数据通道工作。Spec 2 暂缓，先做 Spec 3。工作分支 `feat/turtle-spec3-data-channels`。
- **已就绪**：Spec 1 在 main；roadmap 重组为 Spec 1 ✅ → Spec 3 🟡（当前）→ Spec 2 ⬜（暂缓）→ Spec 4 ⬜（与 Spec 3 可并行）
- **下一步**：Spec 3 brainstorming（FX + multi-period + 承诺支付率）→ spec → plan → 实施
