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

每个文件都有这两行：

```python
value_report = state.get("value_report", "")
value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```

但 **只删这两行会触发 NameError**——多数文件还在别处引用 `value_report` / `value_report_section`
（debug 日志的 `len(value_report)`、prompt 长度求和、以及拼进 `curr_situation`）。必须把每个文件里
**所有** 对这两个变量的引用一并移除。逐文件位置（行号以 review 时为准，实现时再核对）：

| 文件 | `value_report=` / `section=` | 还需清理的引用 | 泄漏注入点 |
|------|------------------------------|----------------|------------|
| `researchers/bull_researcher.py` | L24 / L25 | L86 日志 `len(value_report)`、L93 `curr_situation` | **prompt L126** + curr_situation |
| `researchers/bear_researcher.py` | L22 / L23 | L81 `curr_situation` | **prompt L116** + curr_situation |
| `trader/trader.py` | L18 / L19 | L37 日志、L42 `curr_situation` | **仅 curr_situation→memory**（prompt 不含） |
| `risk_mgmt/conservative_debator.py` | L24 / L25 | L35 日志、L39 长度求和 | **prompt L54** |
| `risk_mgmt/aggresive_debator.py` | L23 / L24 | L34 日志、L38 长度求和 | **prompt L53** |
| `risk_mgmt/neutral_debator.py` | L23 / L24 | L34 日志、L42 长度求和 | **prompt L57** |
| `managers/research_manager.py` | L17 / L18 | L24 `curr_situation` | **prompt L70** + curr_situation |
| `managers/risk_manager.py` | L21 / L22 | L27 `curr_situation` | **仅 curr_situation→memory**（prompt 不含） |

**关键差异**：`trader.py` 和 `risk_manager.py` 的 `value_report` **从未进 LLM prompt**，而是拼进
`curr_situation` 再喂给 `memory.get_memories(curr_situation)` 做相似案例检索——真正的泄漏在这条
memory 检索路径上。这两个文件必须从 `curr_situation` 里移除价值投资片段（否则隔离不彻底）。

**不要触碰** `tradingagents/graph/conditional_logic.py`（`should_continue_value` 里读
`value_report` / `value_tool_call_count` 是 Value 节点 **自己的终止路由**，删了会破坏图）。

**保持不变**：

- Value Analyst 节点继续在主图中运行，`value_report` / `value_turtle_payload` 仍写入 `final_state`。
- 用户侧展示链路 **不经过下游 agent**，直接从 `final_state` 取 `value_report`。源头是
  `tradingagents/graph/trading_graph.py:894`（`final_state.get("value_report","")` 直接拷进返回结果），
  随后由 app 层提取：
  - `app/routers/analysis.py`（report_fields 含 `value_report`，约 420 行；533 行用于兜底摘要）
  - `app/services/simple_analysis_service.py`（report_fields 约 1671/2550 行；2989-2992 映射为
    `value_report.md` / 标题「价值投资分析」）
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

- 单测：构造含非空 `value_report` 的 `state`，分别调用 8 个 agent。
  - 对 6 个把 value 注入 prompt 的 agent（bull/bear、3 个风险辩论员、research_manager）：
    断言生成的 prompt **不包含** 价值投资内容（「价值投资分析」标记或报告片段）。
  - 对 `trader.py` / `risk_manager.py`：prompt 本就不含 value，单纯断言 prompt 会 **误以为通过**。
    需断言传给 `memory.get_memories(...)` 的 `curr_situation` 不含价值投资片段（mock memory 或
    捕获入参），否则测不到真正的泄漏路径。
  - 冒烟：8 个 agent 在 `value_report` 非空时都能正常运行、不抛 `NameError`（防止漏删引用）。
- 回归断言：最终结果组装仍包含 `value_report`（验证用户侧展示未被破坏）。
- 若现有测试套件中有覆盖这些 agent 的用例，确保不回归。

## 风险

- 漏删引用会触发 **NameError**（f-string 里残留 `{value_report_section}` 是 NameError，不是 KeyError）。
  必须按上表清掉每个文件里的 **全部** 引用（声明 + 日志 + 长度求和 + curr_situation）。
- `trader.py` / `risk_manager.py` 的泄漏在 memory 检索而非 prompt，易被「只看 prompt」的测试漏掉。
- 删除后确保 prompt / `curr_situation` 字符串拼接仍合法（无悬空换行，虽不影响功能）。
- 不要误删 `conditional_logic.py` 中 Value 节点自己的路由读取。
- `value_report.md` 等输出文件名常量、`format_value_report_source_note` 等是无关命名巧合，不在改动范围。
