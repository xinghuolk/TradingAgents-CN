# Spec 4：Turtle Data View Frontend 设计

> 状态：设计已与用户确认（2026-05-25）。下一步转 writing-plans。
> 工作分支：`feat/turtle-spec4-data-view-frontend`（基于 `main` 创建）。
> 路线图：`docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md` Spec 4。

## 1. 背景与目标

当前 `value_report` 已经由 Turtle v0.15 价值分析流程生成；前端仍沿用“价值投资分析”作为面向用户的标签名。Spec 4 不新增一个并列的 Turtle 分析模块，而是把现有“价值投资分析”报告升级为结构化视图：同一入口中同时展示报告正文、TurtleFacts 数据、TurtleComputedSignals 计算结果和状态/降级原因。

目标：

1. 在 `ReportDetail.vue` 和 `SingleAnalysis.vue` 两个入口复用同一个前端组件。
2. 后端只做最小 API 透传，把已有 `value_turtle_payload` 原样暴露给前端。
3. 前端在“价值投资分析”内部提供 `报告 / 数据 / 计算 / 状态` 四个子 Tab。
4. 对旧报告、非 Turtle 报告、空 payload、坏 payload 安静降级，不破坏现有 markdown 报告体验。

## 2. 范围

### 2.1 范围内

- `/api/reports/{id}/detail` 返回 canonical `value_turtle_payload` 字段。
- `/api/analysis/tasks/{id}/result` 返回 canonical `value_turtle_payload` 字段。
- 新增共享前端组件 `TurtlePayloadPanel.vue`（建议放在 `frontend/src/components/Analysis/`）。
- `ReportDetail.vue` 的 `value_report` 模块使用 `TurtlePayloadPanel` 渲染。
- `SingleAnalysis.vue` 的 `value_report` 报告内容使用同一组件渲染。
- `数据` Tab 展示当前期 `facts.report.fields` / `facts.market.fields`，并以可折叠区域展示 `facts.report.historical`。
- `计算` Tab 展示 `signals.results` 公式表。
- `状态` Tab 展示 facts/signals/report/market status、caveats、missing inputs。
- 从 `source_reference` 中识别 `p.<number>` 页码，渲染成可点击 chip；首版没有 PDF URL 时点击显示“当前报告暂未提供原文定位链接”。

### 2.2 范围外

- 不改“价值投资分析”这个顶层 UI 名称。
- 不新增后端 UI DTO，不在后端重排 facts/signals。
- 不实现真实 PDF 打开和跳页定位；缺少 PDF URL/page mapping 时只提供提示。
- 不改变 Turtle payload schema，不调整 Turtle 计算口径。
- 不重构其他报告模块的 tab/rendering 逻辑。

## 3. 核心设计决策

1. **共享组件优先**：`ReportDetail` 和 `SingleAnalysis` 必须复用同一个 `TurtlePayloadPanel`，避免两套解析/展示逻辑漂移。
2. **后端最小透传**：API 返回 raw `{facts, signals}` JSON 字符串；解析、分组、展示属于前端组件职责。
3. **语义绑定 value_report**：`value_report` 是 Turtle 价值分析正文；结构化数据视图只在该报告内部出现，不作为顶层并列报告模块。
4. **无 payload 安静退化**：没有有效 payload 时只显示原 markdown，不显示 `报告 / 数据 / 计算 / 状态` 子 Tab。
5. **历史期轻量呈现**：当前期 facts 默认展示；historical facts 放在可折叠“历史期间”区域，不做首版完整多期对比表。
6. **PDF 定位预留而不扩大范围**：页码 chip 先形成交互入口，真实定位留给后续 spec/API 数据完善。

## 4. API 边界

Canonical 响应字段：

```json
{
  "value_turtle_payload": "{\"facts\": {...}, \"signals\": {...}}"
}
```

后端可新增一个小 helper，按优先级从以下位置提取非空字符串：

1. `result.value_turtle_payload`
2. `state.value_turtle_payload`
3. `reports.value_turtle_payload`

`/api/reports/{id}/detail` 需要覆盖两条数据路径：

- `analysis_reports` 命中时，从 report document 及其 `reports` 中提取。
- `analysis_tasks.result` 兜底时，从 task result 及其 `state/reports` 中提取。

`/api/analysis/tasks/{id}/result` 需要在最终 `final_result_data` 中保留 `value_turtle_payload`，不要被 reports 清洗逻辑吞掉。

接口不解析 JSON，不因 payload 非法而报错；响应始终包含 `value_turtle_payload` 字符串字段，空或缺失 payload 返回 `""`，前端统一按无有效 payload 处理。

## 5. 前端组件设计

### 5.1 `TurtlePayloadPanel.vue`

Props：

```ts
interface Props {
  valueReport?: string
  valueTurtlePayload?: string
}
```

行为：

- `valueTurtlePayload` 缺失、空白或 JSON 解析失败：只渲染 `valueReport` markdown。
- payload 可解析：渲染 Element Plus 子 tabs：
  - `报告`
  - `数据`
  - `计算`
  - `状态`
- `报告` Tab 永远渲染 `valueReport` markdown，即使 payload 的局部字段缺失。

建议拆出纯函数，便于单测：

- `parseTurtlePayload(raw: string): ParsedTurtlePayload | null`
- `formatFactValue(value: unknown): string`
- `extractPageRefs(sourceReference: string): number[]`
- `statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info'`
- `reliabilityTagType(reliability: string): 'success' | 'warning' | 'info'`

### 5.2 数据 Tab

按来源分组：

- Report facts：`facts.report.fields`
- Market facts：`facts.market.fields`
- Historical report facts：`facts.report.historical`

字段表列：

- 字段名
- 值（MoneyAmount 展示 value + currency + unit）
- reliability
- source_label
- source_reference（页码 chip + 原始来源文本）
- caveat

历史期间以 collapse/accordion 展示，每个 period 一个面板。首版不做跨期对比表。

### 5.3 计算 Tab

读取 `signals.results`，每个 formula result 展示：

- name
- status
- formula
- substitution
- value
- unit
- sources
- missing_inputs

`missing_inputs` 非空时突出显示，但不把整个页面当错误页。

### 5.4 状态 Tab

展示：

- `facts.status`
- `facts.report.status`
- `facts.market.status`
- `signals.status`
- `facts.caveats`
- `facts.report.caveats`
- `facts.market.caveats`
- `signals.caveats`
- 从 `signals.results[*].missing_inputs` 聚合出的缺失项摘要

状态颜色：

- `complete` → success
- `degraded` → warning
- `non_decisionable` → danger
- `unsupported` → info

## 6. 页面集成

### 6.1 `ReportDetail.vue`

在模块 tab 渲染中识别 `moduleName === 'value_report'`：

- 使用 `TurtlePayloadPanel`。
- `valueReport = report.reports.value_report`。
- `valueTurtlePayload = report.value_turtle_payload`，必要时 fallback `report.reports.value_turtle_payload`。

其他模块沿用现有 markdown/json 渲染。

### 6.2 `SingleAnalysis.vue`

保留现有 `getAnalysisReports()` 排序和“价值投资分析”标题。内容区渲染时识别 value report：

- 使用 `TurtlePayloadPanel`。
- `valueReport = report.content`。
- `valueTurtlePayload = analysisResults.value_turtle_payload`，必要时 fallback `analysisResults.reports.value_turtle_payload`。

避免把 `value_turtle_payload` 当成普通报告 tab 加入 `getAnalysisReports()`。

## 7. 兼容与错误处理

- 旧报告/非 Turtle 报告：无 payload，只显示原 markdown。
- payload JSON 解析失败：只显示原 markdown，并 `console.warn` 记录错误。
- payload 可解析但局部字段缺失：显示可用部分，缺失区域显示空状态。
- 页码 chip 点击但缺 PDF URL：`ElMessage.info('当前报告暂未提供原文定位链接')`。
- 不在普通 UI 暴露原始 payload 文本；排查通过控制台和 API response。

## 8. 测试策略

### 8.1 后端

Focused pytest 覆盖：

- reports detail 从顶层 `value_turtle_payload` 返回 canonical 字段。
- reports detail 从 `state.value_turtle_payload` 返回 canonical 字段。
- reports detail 从 `reports.value_turtle_payload` 返回 canonical 字段。
- analysis task result 返回 canonical `value_turtle_payload`。
- 空字符串/纯空白 payload 不误判为有效 payload。

### 8.2 前端

优先给纯解析/格式化函数加单测：

- valid payload → parsed result。
- invalid JSON / 空白 → `null`。
- MoneyAmount value 格式化。
- historical periods 转为可展示列表。
- status/reliability tag 映射。
- `source_reference` 页码识别：`net_profit p.7` → `[7]`。

如果当前测试栈支持 Vue component test，补 `TurtlePayloadPanel` 组件测试：

- 有 payload 时显示 `报告 / 数据 / 计算 / 状态` 子 Tab。
- 无 payload 时只显示 markdown，不显示子 Tab。
- 坏 payload 时只显示 markdown。
- 页码 chip 点击触发无 PDF URL 提示。

### 8.3 手动验收

- 已完成 Turtle 分析在 `ReportDetail` 的“价值投资分析”中显示四个子 Tab。
- 同一分析在 `SingleAnalysis` 完成页显示一致的四个子 Tab。
- 旧报告或无 payload 报告仍只显示原 markdown。
- `数据` Tab 当前期字段可读，历史期间可折叠查看。
- `计算` Tab 能看到公式、代入、结果、缺失项。
- `状态` Tab 能看到 status 和 caveats。
- 点击页码 chip 时出现“当前报告暂未提供原文定位链接”提示。

## 9. 实施风险

- 当前后端多个路径会清洗 `reports` 为字符串，`value_turtle_payload` 不能只依赖 `reports` 字段传递；需要 canonical 字段避免被普通报告渲染吞掉。
- `SingleAnalysis.vue` 体量较大，集成时应尽量把逻辑放到共享组件和小 helper 中，减少页面内新增复杂度。
- Payload schema 是 additive 演进，前端解析必须宽松，不能因为新增/缺失可选字段导致整个报告不可见。

## 10. 后续 plan 入口

Implementation plan 应按以下任务拆分：

1. 后端 payload extraction helper + 两个 API 返回字段。
2. 前端 payload parser/format helper + 单测。
3. `TurtlePayloadPanel.vue` 组件。
4. `ReportDetail.vue` 集成。
5. `SingleAnalysis.vue` 集成。
6. focused backend/frontend verification + 手动验收。
