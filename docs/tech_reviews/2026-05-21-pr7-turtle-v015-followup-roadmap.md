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
| **Spec 1** | correctness-fixes | 综合 2.1 / 2.2 / 2.3；计算 A.4 / A.5 / B.1 / B.3；D 章 backend 透传 | 低 | 中 | 无 |
| **Spec 2** | model-recalibration | 计算 A.1 时间口径；A.2 税务口径；A.7 payout_anchor 重命名 | 中（改变数值） | 小（设计决策为主） | 与 Spec 1 无强依赖，可并行 |
| **Spec 3** | cross-currency-fx | 计算 A.3 跨币 FX；B.2 market source provider；B.4 FX metadata | 中（新数据通道） | 中 | 与 Spec 1 / 2 无强依赖，可并行 |
| **Spec 4** | turtle-data-view-frontend | D 章 frontend tab（数据 / 计算 / 状态 Tab） | 中（proprietary、UX 设计） | 大 | **依赖 Spec 1 的 backend 透传**完成 |

**已并入相邻 spec 的化妆性条目**：

- A.6 `abs(capex)` 文档化 → 并入 Spec 1（更新 prompt 说明）
- B.5 sources list 过长 → 并入 Spec 1 或 Spec 4（dedup 在 formatting.py 或前端展示层处理）

## 3. 推荐实施顺序

```
            ┌── Spec 1 (correctness) ──────┐
            │                                │
            ├── Spec 2 (model)               │
start  ─────┤      （可并行）                ├── Spec 4 (frontend tab)
            ├── Spec 3 (FX)                  │       依赖 Spec 1 backend 透传
            │      （可并行）                │
            └────────────────────────────────┘
```

**关键路径**：Spec 1 → Spec 4（backend 透传必须先就位）。

**Spec 2 / 3** 与 Spec 1 无强依赖，单线推进时按以下顺序：

1. **Spec 1**（correctness-fixes）：风险最低、影响面广、能让"事实先行"设计真正生效
2. **Spec 2**（model-recalibration）：口径决策影响所有 R/GG 数字基线，越早决定下游测试越稳
3. **Spec 3**（cross-currency-fx）：影响 H 股 + CNY 报表公司是否可用，业务紧迫性强
4. **Spec 4**（turtle-data-view-frontend）：用户感知最强，但必须等 Spec 1 backend 透传完成

## 4. 状态追踪

**状态图例**：⬜ 未开始 | 🟡 brainstorming 中 | 🟢 spec 已通过 | 🔵 plan 已通过 | 🟠 实施中 | ✅ 已完成（merged）

| Spec | 状态 | Spec 文档 | Plan 文档 | PR | 备注 |
|------|------|----------|-----------|-----|------|
| Spec 1：correctness-fixes | ⬜ | — | — | — | 等待用户选定首先开始 |
| Spec 2：model-recalibration | ⬜ | — | — | — | |
| Spec 3：cross-currency-fx | ⬜ | — | — | — | |
| Spec 4：turtle-data-view-frontend | ⬜ | — | — | — | 阻塞于 Spec 1 backend 部分 |

文档路径约定：

- Spec：`docs/superpowers/specs/2026-05-XX-turtle-<topic>-design.md`
- Plan：`docs/superpowers/plans/2026-05-XX-turtle-<topic>.md`

每完成一个 spec / plan / PR，回填本表。

## 5. Spec 范围明细

> 详细 brainstorming 与设计决策在各 spec 自己的文档里。本节只列条目映射，便于复核。

### Spec 1：correctness-fixes

修复项：

- **综合 2.1 redaction over-broad bug**（`decision.py`）—— identifier 与 enum 不应被脱敏，只保留 caveat / source label 自由文本的脱敏
- **综合 2.2 facts.status 硬编码 "complete"**（`turtle_analysis_tool.py`）—— 根据 caveat / 关键字段缺失推导真实 status
- **综合 2.3 tool signature drift**（`agent_utils.py` 与 `turtle_analysis_tool.py`）—— 对齐 `company_name` 类型
- **计算 A.4 三处死代码**（`calculations.py` line 344-346 / 370-372 / 411-412）—— 删除不可达分支
- **计算 A.5 ev_switch / cash_protection 的 degraded 不可达**—— 要么让 net_cash_ratio 支持单边降级，要么删 degraded 分支
- **计算 B.1 `tax_rate` 默认渠道下仍 reliable**（`market_adapter.py`）—— 默认 holding_channel 时降级为 `display_only`
- **计算 B.3 单年度 payout proxy 假装 3y**（`report_adapter.py`）—— 改名或降级为 `display_only`
- **D 章 backend 透传 `value_turtle_payload`**（`value_analyst.py`、`propagation.py`、`trading_graph.py`、`simple_analysis_service.py`）—— 把 JSON payload 一并落盘
- **A.6 `abs(capex)` 文档化**（`decision.py` prompt 旁补一行）

### Spec 2：model-recalibration

修复项：

- **计算 A.1 时间口径不一致**—— 要么引入 `avg_net_profit_3y` / `avg_owner_earnings_3y`，要么用当期 payout 替换 3y 平均；需要设计决策
- **计算 A.2 分红 / 回购税务口径不对称**—— 引入 `q_buyback` 或在 prompt 中明示假设
- **计算 A.7 `payout_anchor` 别名问题**—— 重命名为 `payout_anchor_passthrough` 或从 results 中移除

### Spec 3：cross-currency-fx

修复项：

- **计算 A.3 跨币 FX 通道未打通**—— 选 FX provider；写入 `facts.report.metadata["fx_rates"]`；明确 pair 方向约定
- **计算 B.2 市场 source_reference 缺 provider**—— `market_adapter` 携带 `info["source"]` + timestamp 进 source_reference
- **计算 B.4 FX metadata 缺 provider/timestamp**—— FX 信息同样携带来源与时间戳

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

- **当前焦点**：等待用户选定首先开始的 spec（推荐 Spec 1）
- **已就绪**：评审文档 + 路线图、`fix/turtle-v015-review-followups` 分支
