# 价值投资分析隔离（PR 2）

- 日期：2026-06-04
- 范围：单个 PR
- 涉及边界：`tradingagents/agents/`（Apache）

## 背景

价值投资分析由 Value Analyst 节点产出，写入共享 `AgentState` 的字段：

- `value_report`（人读报告）
- `value_turtle_payload`（`{facts, signals}` 原始 JSON）
- `value_tool_call_count`（工具循环计数）

定义见 `tradingagents/agents/utils/agent_states.py:67-78`，初始化见
`tradingagents/graph/propagation.py:52-54`。

**当前问题**：8 个下游 agent 都会 `state.get("value_report", "")` 并把它拼进自己的 prompt，
等于让其他分析师/决策者「知道」了价值投资的结论：

- `tradingagents/agents/researchers/bull_researcher.py`
- `tradingagents/agents/researchers/bear_researcher.py`
- `tradingagents/agents/trader/trader.py`
- `tradingagents/agents/risk_mgmt/conservative_debator.py`
- `tradingagents/agents/risk_mgmt/aggresive_debator.py`
- `tradingagents/agents/risk_mgmt/neutral_debator.py`
- `tradingagents/agents/managers/research_manager.py`
- `tradingagents/agents/managers/risk_manager.py`

「过程」泄漏方面：LangGraph 共享 `MessagesState`，但每个分析师后都有
`Msg Clear <Name>` 节点（`create_msg_delete`，`agent_utils.py:26-39`）清空消息，且所有下游 agent
**只读命名 state 字段、不读原始 `messages`**。所以过程已隔离，唯一泄漏点是上述 `value_report` 注入。

## 目标

完全隔离：任何下游 agent 都看不到价值投资的结果与过程；价值投资分析依旧作为
**独立板块** 展示给用户。

## 设计

**做法（最小改动，确认采用「继续跑、仅断开下游读取」方案）**：

从上述 8 个文件中删除两段代码：

```python
value_report = state.get("value_report", "")
value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```

以及 prompt 拼接里的 `{value_report_section}`（各文件具体变量名/位置以实际代码为准）。

**保持不变**：

- Value Analyst 节点继续在主图中运行，`value_report` / `value_turtle_payload` 仍写入 `final_state`。
- 用户侧展示链路 **不经过下游 agent**，直接从 `final_state` 取 `value_report`：
  - `app/services/analysis_service.py`（约 357-369 行，进 `detailed_analysis`）
  - `app/routers/analysis.py`（约 418-422 行，report_fields 含 `value_report`）
  - `app/services/simple_analysis_service.py`（`value_report.md` / 标题「价值投资分析」）
  - 因此删除下游读取后，用户仍能看到价值投资板块。
- `Msg Clear Value` 等消息清理与图结构 **不动**。

**计划阶段需核对**：确认 `value_turtle_payload` 没有任何下游 agent 读取（探索显示仅 Value Analyst 自身在工具循环中使用）。若发现下游读取，一并断开。

## 非目标

- 不改图结构、不把 value 拆成独立并行链。
- 不改 Value Analyst 自身逻辑。
- 不改前端展示（价值投资板块本就独立展示，无需改动）。

## 数据流（隔离后）

```
Value Analyst → 写 value_report / value_turtle_payload → final_state
   │（下游 agent 不再读取）
   └────────────────────────────────► 最终输出链路直接取 value_report → 展示给用户

Bull/Bear/Trader/Risk×3/两个 Manager：prompt 中不再含价值投资内容
```

## 测试

- 单测：构造含非空 `value_report` 的 `state`，分别调用 8 个 agent 的 prompt 构造，
  断言生成的 prompt/上下文 **不包含** `value_report` 内容（如「价值投资分析」标记或报告片段）。
- 回归断言：最终结果组装仍包含 `value_report`（验证用户侧展示未被破坏）。
- 若现有测试套件中有覆盖这些 agent 的用例，确保不回归。

## 风险

- 8 个文件中变量命名/拼接方式可能略有差异，需逐个核对，避免删错或残留 `{value_report_section}` 占位导致 KeyError。
- 确保删除后 prompt 字符串拼接仍合法（无悬空换行/格式问题，但不影响功能）。
