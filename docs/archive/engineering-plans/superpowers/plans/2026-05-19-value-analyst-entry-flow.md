# Value Analyst Entry Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing `value` analyst selectable and runnable as an opt-in entry across graph, API reports, frontend selection, CLI selection, and focused tests.

**Architecture:** Reuse the existing `value` analyst and `get_value_investment_analysis` tool. Add the missing graph routing/state/report plumbing, expose the analyst in shared UI/CLI entry points, and keep defaults unchanged so existing analysis behavior does not change unless `value` is explicitly selected.

**Tech Stack:** Python, pytest, LangGraph, LangChain messages/tools, FastAPI/Pydantic models, Vue 3, TypeScript, Element Plus.

---

## Files

- Modify: `tradingagents/graph/conditional_logic.py`
- Modify: `tradingagents/graph/propagation.py`
- Modify: `tradingagents/graph/setup.py`
- Modify: `tradingagents/agents/researchers/bull_researcher.py`
- Modify: `tradingagents/agents/researchers/bear_researcher.py`
- Modify: `tradingagents/agents/managers/research_manager.py`
- Modify: `tradingagents/agents/managers/risk_manager.py`
- Modify: `tradingagents/agents/trader/trader.py`
- Modify: `tradingagents/agents/risk_mgmt/aggresive_debator.py`
- Modify: `tradingagents/agents/risk_mgmt/conservative_debator.py`
- Modify: `tradingagents/agents/risk_mgmt/neutral_debator.py`
- Modify: `app/routers/analysis.py`
- Modify: `app/services/simple_analysis_service.py`
- Modify: `frontend/src/constants/analysts.ts`
- Modify: `frontend/src/views/Analysis/SingleAnalysis.vue`
- Modify: `frontend/src/views/Reports/ReportDetail.vue`
- Modify: `cli/models.py`
- Modify: `cli/utils.py`
- Modify: `cli/main.py`
- Create: `tests/unit/test_value_analyst_entry.py`
- Create: `tests/unit/test_value_report_context.py`
- Create: `tests/unit/test_value_analyst_surface.py`

## Task 1: Add Failing Graph Entry Tests

**Files:**
- Create: `tests/unit/test_value_analyst_entry.py`

- [ ] **Step 1: Write failing tests for value routing and initial state**

Create `tests/unit/test_value_analyst_entry.py`:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import GraphSetup


@tool
def dummy_value_tool(ticker: str, market: str = "A") -> str:
    """Return a deterministic value analysis result for graph tests."""
    return f"value analysis for {ticker} in {market}"


def test_should_continue_value_routes_to_tool_when_tool_call_exists():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_value_investment_analysis",
                        "args": {"ticker": "000001", "market": "A"},
                        "id": "call_value",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "value_report": "",
        "value_tool_call_count": 0,
    }

    assert ConditionalLogic().should_continue_value(state) == "tools_value"


def test_should_continue_value_stops_when_report_exists():
    state = {
        "messages": [HumanMessage(content="value analyst finished")],
        "value_report": "x" * 120,
        "value_tool_call_count": 0,
    }

    assert ConditionalLogic().should_continue_value(state) == "Msg Clear Value"


def test_should_continue_value_stops_at_tool_call_cap():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_value_investment_analysis",
                        "args": {"ticker": "000001", "market": "A"},
                        "id": "call_value",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "value_report": "",
        "value_tool_call_count": 1,
    }

    assert ConditionalLogic().should_continue_value(state) == "Msg Clear Value"


def test_initial_state_includes_value_report_fields():
    state = Propagator().create_initial_state("000001", "2026-05-19")

    assert state["value_report"] == ""
    assert state["value_tool_call_count"] == 0


def test_graph_setup_accepts_value_only_selection():
    logic = ConditionalLogic()
    setup = GraphSetup(
        quick_thinking_llm=MagicMock(),
        deep_thinking_llm=MagicMock(),
        toolkit=SimpleNamespace(),
        tool_nodes={"value": ToolNode([dummy_value_tool])},
        bull_memory=None,
        bear_memory=None,
        trader_memory=None,
        invest_judge_memory=None,
        risk_manager_memory=None,
        conditional_logic=logic,
        config={"llm_provider": "test"},
    )

    graph = setup.setup_graph(["value"])
    graph_nodes = set(graph.get_graph().nodes.keys())

    assert "Value Analyst" in graph_nodes
    assert "tools_value" in graph_nodes
    assert "Msg Clear Value" in graph_nodes
```

- [ ] **Step 2: Run tests and verify expected failures**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_value_analyst_entry.py -q
```

Expected: failures mentioning missing `should_continue_value`, missing `value_report`, or graph setup failing for value.

## Task 2: Implement Graph Routing and Initial State

**Files:**
- Modify: `tradingagents/graph/conditional_logic.py`
- Modify: `tradingagents/graph/propagation.py`
- Modify: `tradingagents/graph/setup.py`
- Test: `tests/unit/test_value_analyst_entry.py`

- [ ] **Step 1: Add value routing logic**

In `tradingagents/graph/conditional_logic.py`, add this method after `should_continue_fundamentals`:

```python
    def should_continue_value(self, state: AgentState):
        """Determine if value investment analysis should continue."""
        from tradingagents.utils.logging_init import get_logger
        logger = get_logger("agents")

        messages = state["messages"]
        last_message = messages[-1]

        tool_call_count = state.get("value_tool_call_count", 0)
        max_tool_calls = 1
        value_report = state.get("value_report", "")

        logger.info("🔀 [条件判断] should_continue_value")
        logger.info(f"🔀 [条件判断] - 消息数量: {len(messages)}")
        logger.info(f"🔀 [条件判断] - 报告长度: {len(value_report)}")
        logger.info(f"🔧 [死循环修复] - 工具调用次数: {tool_call_count}/{max_tool_calls}")

        if value_report and len(value_report) > 100:
            logger.info("🔀 [条件判断] ✅ 价值投资报告已完成，返回: Msg Clear Value")
            return "Msg Clear Value"

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            if tool_call_count >= max_tool_calls:
                logger.warning(
                    f"🔧 [死循环修复] 价值投资工具调用次数已达上限({tool_call_count}/{max_tool_calls})，强制结束"
                )
                return "Msg Clear Value"

            logger.info("🔀 [条件判断] 🔧 检测到价值投资工具调用，返回: tools_value")
            return "tools_value"

        logger.info("🔀 [条件判断] ✅ 无tool_calls，返回: Msg Clear Value")
        return "Msg Clear Value"
```

- [ ] **Step 2: Initialize value state fields**

In `tradingagents/graph/propagation.py`, add these keys to the dictionary returned by `create_initial_state()`:

```python
            "value_report": "",
            "value_tool_call_count": 0,
```

Place them next to the existing analyst report fields:

```python
            "market_report": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
            "value_report": "",
            "value_tool_call_count": 0,
```

- [ ] **Step 3: Update graph setup documentation**

In `tradingagents/graph/setup.py`, update the `selected_analysts` docstring list to include:

```python
                - "value": Value investment analyst
```

Do not change the default list.

- [ ] **Step 4: Run graph entry tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_value_analyst_entry.py -q
```

Expected: all tests in `tests/unit/test_value_analyst_entry.py` pass.

- [ ] **Step 5: Commit graph entry changes**

Run:

```bash
git add tradingagents/graph/conditional_logic.py tradingagents/graph/propagation.py tradingagents/graph/setup.py tests/unit/test_value_analyst_entry.py
git commit -m "feat: wire value analyst graph entry"
```

## Task 3: Include Value Report in Downstream Agent Context

**Files:**
- Create: `tests/unit/test_value_report_context.py`
- Modify: `tradingagents/agents/researchers/bull_researcher.py`
- Modify: `tradingagents/agents/researchers/bear_researcher.py`
- Modify: `tradingagents/agents/managers/research_manager.py`
- Modify: `tradingagents/agents/managers/risk_manager.py`
- Modify: `tradingagents/agents/trader/trader.py`
- Modify: `tradingagents/agents/risk_mgmt/aggresive_debator.py`
- Modify: `tradingagents/agents/risk_mgmt/conservative_debator.py`
- Modify: `tradingagents/agents/risk_mgmt/neutral_debator.py`

- [ ] **Step 1: Write a focused context regression test**

Create `tests/unit/test_value_report_context.py`:

```python
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher


class CapturingLLM:
    def __init__(self):
        self.prompt = None

    def invoke(self, prompt):
        self.prompt = prompt
        return "bull response"


def test_bull_researcher_prompt_includes_value_report(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.utils.stock_utils.StockUtils.get_market_info",
        lambda ticker: {
            "is_china": True,
            "is_hk": False,
            "is_us": False,
            "market_name": "中国A股",
            "currency_name": "人民币",
            "currency_symbol": "¥",
        },
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.interface.get_china_stock_info_unified",
        lambda ticker: "股票名称: 平安银行\n",
    )

    llm = CapturingLLM()
    node = create_bull_researcher(llm, memory=None)

    node(
        {
            "company_of_interest": "000001",
            "market_report": "market report",
            "sentiment_report": "sentiment report",
            "news_report": "news report",
            "fundamentals_report": "fundamentals report",
            "value_report": "penetrating yield and cash health report",
            "investment_debate_state": {
                "history": "",
                "bull_history": "",
                "bear_history": "",
                "current_response": "",
                "count": 0,
            },
        }
    )

    assert "价值投资分析" in llm.prompt
    assert "penetrating yield and cash health report" in llm.prompt
```

- [ ] **Step 2: Run the context test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_value_report_context.py -q
```

Expected: failure because the bull researcher prompt does not include `value_report`.

- [ ] **Step 3: Add value report to bull researcher**

In `tradingagents/agents/researchers/bull_researcher.py`, after reading `fundamentals_report`, add:

```python
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```

Replace `curr_situation` with:

```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```

In the prompt resources section, add after `公司基本面报告：{fundamentals_report}`:

```python
价值投资分析：{value_report}
```

- [ ] **Step 4: Add value report to bear researcher**

In `tradingagents/agents/researchers/bear_researcher.py`, after reading `fundamentals_report`, add:

```python
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```

Replace `curr_situation` with:

```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```

In the prompt resources section, add after `公司基本面报告：{fundamentals_report}`:

```python
价值投资分析：{value_report}
```

- [ ] **Step 5: Add value report to research manager**

In `tradingagents/agents/managers/research_manager.py`, after reading `fundamentals_report`, add:

```python
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```

Replace `curr_situation` with:

```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```

In the comprehensive report prompt section, add after `基本面分析：{fundamentals_report}`:

```python
价值投资分析：{value_report}
```

- [ ] **Step 6: Add value report to trader and risk agents**

For each file below, read `value_report = state.get("value_report", "")`, create `value_report_section`, and append it to the existing `curr_situation` string:

```python
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```

Apply this to:

- `tradingagents/agents/trader/trader.py`
- `tradingagents/agents/managers/risk_manager.py`
- `tradingagents/agents/risk_mgmt/aggresive_debator.py`
- `tradingagents/agents/risk_mgmt/conservative_debator.py`
- `tradingagents/agents/risk_mgmt/neutral_debator.py`

Where the prompt has a visible resource list, add:

```python
价值投资分析：{value_report}
```

- [ ] **Step 7: Run context tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_value_report_context.py -q
```

Expected: pass.

- [ ] **Step 8: Commit downstream context changes**

Run:

```bash
git add tradingagents/agents/researchers/bull_researcher.py tradingagents/agents/researchers/bear_researcher.py tradingagents/agents/managers/research_manager.py tradingagents/agents/managers/risk_manager.py tradingagents/agents/trader/trader.py tradingagents/agents/risk_mgmt/aggresive_debator.py tradingagents/agents/risk_mgmt/conservative_debator.py tradingagents/agents/risk_mgmt/neutral_debator.py tests/unit/test_value_report_context.py
git commit -m "feat: include value report in downstream context"
```

## Task 4: Include Value Reports in API Extraction and Persistence

**Files:**
- Modify: `app/routers/analysis.py`
- Modify: `app/services/simple_analysis_service.py`

- [ ] **Step 1: Add `value_report` to API report field extraction**

In `app/routers/analysis.py`, update each literal report field list that currently contains:

```python
[
    'market_report',
    'sentiment_report',
    'news_report',
    'fundamentals_report',
    'investment_plan',
    'trader_investment_plan',
    'final_trade_decision'
]
```

to include `value_report` after `fundamentals_report`:

```python
[
    'market_report',
    'sentiment_report',
    'news_report',
    'fundamentals_report',
    'value_report',
    'investment_plan',
    'trader_investment_plan',
    'final_trade_decision'
]
```

Also update summary fallback loops from:

```python
for k in ['market_report', 'fundamentals_report', 'sentiment_report', 'news_report']:
```

to:

```python
for k in ['market_report', 'fundamentals_report', 'value_report', 'sentiment_report', 'news_report']:
```

- [ ] **Step 2: Add `value_report` to simple analysis service report fields**

In `app/services/simple_analysis_service.py`, use `rg -n "market_report|fundamentals_report|report_fields|state_key" app/services/simple_analysis_service.py` to find all report maps and field lists.

For literal field lists, add:

```python
'value_report',
```

after `'fundamentals_report'`.

For report file metadata near the existing `market_report` metadata, add:

```python
                'value_report': {
                    'filename': 'value_report.md',
                    'title': '价值投资分析',
                    'state_key': 'value_report'
                },
```

- [ ] **Step 3: Run backend import checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_api_analysis.py -q
```

Expected: pass or skip according to existing environment markers. No import error should occur.

- [ ] **Step 4: Commit API report changes**

Run:

```bash
git add app/routers/analysis.py app/services/simple_analysis_service.py
git commit -m "feat: expose value report in analysis results"
```

## Task 5: Add Frontend Value Analyst Entry and Display Mapping

**Files:**
- Modify: `frontend/src/constants/analysts.ts`
- Modify: `frontend/src/views/Analysis/SingleAnalysis.vue`
- Modify: `frontend/src/views/Reports/ReportDetail.vue`

- [ ] **Step 1: Add frontend analyst constant**

In `frontend/src/constants/analysts.ts`, add this object after the fundamentals analyst:

```typescript
  {
    id: 'value',
    name: '价值投资分析师',
    description: '基于穿透收益率、分红回购和现金健康的实验性价值指标分析',
    icon: 'Money'
  },
```

Update `ANALYST_NAME_TO_ID_MAP`:

```typescript
export const ANALYST_NAME_TO_ID_MAP: Record<string, string> = {
  '市场分析师': 'market',
  '基本面分析师': 'fundamentals',
  '价值投资分析师': 'value',
  '新闻分析师': 'news',
  '社媒分析师': 'social'
}
```

Leave `DEFAULT_ANALYSTS` unchanged:

```typescript
export const DEFAULT_ANALYSTS = ['市场分析师', '基本面分析师']
```

- [ ] **Step 2: Add single-analysis report mapping**

In `frontend/src/views/Analysis/SingleAnalysis.vue`, update `reportMappings` to add `value_report` after `fundamentals_report`:

```typescript
    { key: 'value_report', title: '💎 价值投资分析', category: '分析师团队' },
```

- [ ] **Step 3: Add saved report detail mapping**

In `frontend/src/views/Reports/ReportDetail.vue`, update the report-title map to include:

```typescript
    value_report: '💎 价值投资分析',
```

- [ ] **Step 4: Run frontend type check**

Run:

```bash
cd frontend
npm run type-check
```

Expected: type check passes.

- [ ] **Step 5: Commit frontend entry changes**

Run:

```bash
git add frontend/src/constants/analysts.ts frontend/src/views/Analysis/SingleAnalysis.vue frontend/src/views/Reports/ReportDetail.vue
git commit -m "feat: expose value analyst in frontend"
```

## Task 6: Add CLI Value Analyst Selection and Output

**Files:**
- Modify: `cli/models.py`
- Modify: `cli/utils.py`
- Modify: `cli/main.py`
- Create: `tests/unit/test_value_analyst_surface.py`

- [ ] **Step 1: Add CLI enum value**

In `cli/models.py`, update `AnalystType`:

```python
class AnalystType(str, Enum):
    MARKET = "market"
    SOCIAL = "social"
    NEWS = "news"
    FUNDAMENTALS = "fundamentals"
    VALUE = "value"
```

- [ ] **Step 2: Add interactive CLI choice**

In `cli/utils.py`, update `ANALYST_ORDER`:

```python
ANALYST_ORDER = [
    ("市场分析师 | Market Analyst", AnalystType.MARKET),
    ("社交媒体分析师 | Social Media Analyst", AnalystType.SOCIAL),
    ("新闻分析师 | News Analyst", AnalystType.NEWS),
    ("基本面分析师 | Fundamentals Analyst", AnalystType.FUNDAMENTALS),
    ("价值投资分析师 | Value Analyst", AnalystType.VALUE),
]
```

Keep the A-share social filter unchanged. Do not filter out `AnalystType.VALUE`.

- [ ] **Step 3: Add value report to CLI report buffers**

In `cli/main.py`, add `value_report` wherever report sections are initialized or ordered.

When initializing report sections, include:

```python
            "value_report": None,
```

When mapping display names, include:

```python
                "value_report": "Value Analysis",
```

When rendering final markdown sections, add:

```python
            if self.report_sections["value_report"]:
                sections.append(
                    f"### Value Analysis\n{self.report_sections['value_report']}"
                )
```

- [ ] **Step 4: Add streaming status for value report**

In `cli/main.py`, update `analysis_steps`:

```python
            "value_report": "💎 价值投资分析师",
```

Add a streaming handler beside the existing `fundamentals_report` block:

```python
                if "value_report" in chunk and chunk["value_report"]:
                    if "value_report" not in completed_analysts:
                        ui.show_success("💎 价值投资分析完成")
                        completed_analysts.add("value_report")
                        logger.info(f"首次显示价值投资分析完成提示，已完成分析师: {completed_analysts}")
                    else:
                        logger.debug(f"跳过重复的价值投资分析完成提示，已完成分析师: {completed_analysts}")

                    message_buffer.update_report_section(
                        "value_report", chunk["value_report"]
                    )
                    message_buffer.update_agent_status("Value Analyst", "completed")
```

- [ ] **Step 5: Display final value report in CLI summary**

In the final report display area where `market_report`, `sentiment_report`, `news_report`, and `fundamentals_report` are rendered, add:

```python
    if final_state.get("value_report"):
        console.print(Panel(
            Markdown(final_state["value_report"]),
            title="💎 价值投资分析",
            border_style="magenta"
        ))
```

- [ ] **Step 6: Add CLI and frontend surface regression tests**

Create `tests/unit/test_value_analyst_surface.py`:

```python
from pathlib import Path

from cli.models import AnalystType
from cli.utils import ANALYST_ORDER


def test_cli_value_analyst_is_selectable():
    assert AnalystType.VALUE.value == "value"
    assert ("价值投资分析师 | Value Analyst", AnalystType.VALUE) in ANALYST_ORDER


def test_frontend_value_analyst_constant_is_mapped():
    constants = Path("frontend/src/constants/analysts.ts").read_text(encoding="utf-8")

    assert "id: 'value'" in constants
    assert "name: '价值投资分析师'" in constants
    assert "'价值投资分析师': 'value'" in constants
    assert "DEFAULT_ANALYSTS = ['市场分析师', '基本面分析师']" in constants
```

- [ ] **Step 7: Run CLI-focused and surface tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_value_analyst_surface.py tests/test_cli_fix.py tests/test_cli_hk.py -q
```

Expected: pass or skip according to existing environment assumptions. No enum/import error should occur.

- [ ] **Step 8: Commit CLI and surface test changes**

Run:

```bash
git add cli/models.py cli/utils.py cli/main.py tests/unit/test_value_analyst_surface.py
git commit -m "feat: expose value analyst in cli"
```

## Task 7: Final Verification

**Files:**
- Verify all files changed by Tasks 1-6

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_value_analyst_entry.py tests/unit/test_value_report_context.py -q
```

Expected: pass.

- [ ] **Step 2: Run surface regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_value_analyst_surface.py -q
```

Expected: pass.

- [ ] **Step 3: Run graph/config regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_conditional_logic_config.py tests/unit/test_trading_graph_init_refactor.py -q
```

Expected: pass.

- [ ] **Step 4: Run frontend type check**

Run:

```bash
cd frontend
npm run type-check
```

Expected: pass.

- [ ] **Step 5: Inspect defaults**

Run:

```bash
rg -n "DEFAULT_ANALYSTS|selected_analysts: List\\[str\\]|selected_analysts=\\[|default_factory=lambda" frontend/src/constants/analysts.ts app/models/analysis.py tradingagents/graph/setup.py tradingagents/graph/trading_graph.py
```

Expected:

- `frontend/src/constants/analysts.ts` keeps `DEFAULT_ANALYSTS = ['市场分析师', '基本面分析师']`.
- `app/models/analysis.py` default selected analysts do not include `value`.
- `tradingagents/graph/setup.py` and `tradingagents/graph/trading_graph.py` default selected analysts do not include `value`.

- [ ] **Step 6: Check changed files**

Run:

```bash
git status --short
```

Expected: only intentional implementation files remain changed after the task commits, plus any pre-existing untracked `.codex/` directory.
