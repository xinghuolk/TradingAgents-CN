# 价值投资分析隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 8 个下游 agent 完全看不到价值投资分析（`value_report`）的结果与过程，同时价值投资报告仍作为独立板块出现在最终输出中。

**Architecture:** 价值分析师（Value Analyst）继续在主图运行并写入 `value_report` / `value_turtle_payload`；从 8 个下游 agent 中删除对 `value_report` 的全部引用（声明、debug 日志、长度求和、`curr_situation` 拼接、prompt 注入）。最终输出链路直接从 `final_state` 取 `value_report`（不经过这 8 个 agent），故展示不受影响。

**Tech Stack:** Python（`tradingagents/`，Apache 2.0）、pytest。

参考 spec：`docs/superpowers/specs/2026-06-04-value-investing-isolation-design.md`

**关键约束：**
- 每个文件里对 `value_report` / `value_report_section` 的 **全部** 引用都要删，否则运行时 `NameError`。
- `trader.py` 和 `risk_manager.py` 的 `value_report` **不在 prompt 里**，而是经 `curr_situation` 喂给 `memory.get_memories(...)`——必须从 `curr_situation` 移除。
- **不要改** `tradingagents/graph/conditional_logic.py`（其中读 `value_report` 是 Value 节点自身的终止路由）。

---

## File Structure

- Create: `tests/test_value_investing_isolation.py` — 行为测试（哨兵不泄漏）+ 静态测试（源码无残留引用）
- Modify（各删除全部 `value_report` 引用）：
  - `tradingagents/agents/researchers/bull_researcher.py`
  - `tradingagents/agents/researchers/bear_researcher.py`
  - `tradingagents/agents/trader/trader.py`
  - `tradingagents/agents/risk_mgmt/conservative_debator.py`（`create_safe_debator`）
  - `tradingagents/agents/risk_mgmt/aggresive_debator.py`（`create_risky_debator`）
  - `tradingagents/agents/risk_mgmt/neutral_debator.py`
  - `tradingagents/agents/managers/research_manager.py`
  - `tradingagents/agents/managers/risk_manager.py`

---

### Task 1: 写隔离测试（先失败）

**Files:**
- Create: `tests/test_value_investing_isolation.py`

- [ ] **Step 1: 写测试**

创建 `tests/test_value_investing_isolation.py`：

```python
"""价值投资分析隔离测试：

1. 行为测试 —— 给定一个带哨兵的 value_report，运行每个下游 agent 节点，
   断言哨兵既不出现在传给 LLM 的内容里，也不出现在传给 memory 的检索查询里。
2. 静态测试 —— 8 个 agent 源文件中不应再出现 `value_report` 字样（防止漏删引用导致 NameError）。
"""
from pathlib import Path

SENTINEL = "价值哨兵_VALUE_SENTINEL_XYZ"

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENT_FILES = [
    "tradingagents/agents/researchers/bull_researcher.py",
    "tradingagents/agents/researchers/bear_researcher.py",
    "tradingagents/agents/trader/trader.py",
    "tradingagents/agents/risk_mgmt/conservative_debator.py",
    "tradingagents/agents/risk_mgmt/aggresive_debator.py",
    "tradingagents/agents/risk_mgmt/neutral_debator.py",
    "tradingagents/agents/managers/research_manager.py",
    "tradingagents/agents/managers/risk_manager.py",
]


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.response_metadata = {}


class FakeLLM:
    """记录每次 invoke 的入参（可能是字符串或消息列表）。"""

    def __init__(self):
        self.inputs = []

    def invoke(self, payload):
        self.inputs.append(payload)
        # 内容需足够长，满足 risk_manager 的 >10 字符判定
        return FakeResponse("最终交易建议: **持有**，理由稳健充分，控制风险。")


class FakeMemory:
    """记录每次检索查询。"""

    def __init__(self):
        self.queries = []

    def get_memories(self, query, n_matches=2):
        self.queries.append(query)
        return []


def _seen_text(llm: FakeLLM, mem: FakeMemory) -> str:
    parts = [str(x) for x in llm.inputs] + [str(q) for q in mem.queries]
    return "\n".join(parts)


def _base_state(ticker="AAPL"):
    """用美股代码避免触发联网的中文公司名查询。"""
    return {
        "company_of_interest": ticker,
        "market_report": "MARKET_REPORT_CONTENT",
        "sentiment_report": "SENTIMENT_REPORT_CONTENT",
        "news_report": "NEWS_REPORT_CONTENT",
        "fundamentals_report": "FUNDAMENTALS_REPORT_CONTENT",
        "value_report": SENTINEL,
        "investment_plan": "INVESTMENT_PLAN_CONTENT",
        "trader_investment_plan": "TRADER_PLAN_CONTENT",
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        },
        "risk_debate_state": {
            "history": "",
            "risky_history": "",
            "safe_history": "",
            "neutral_history": "",
            "current_risky_response": "",
            "current_safe_response": "",
            "current_neutral_response": "",
            "count": 0,
        },
    }


def test_bull_researcher_isolated():
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
    llm, mem = FakeLLM(), FakeMemory()
    create_bull_researcher(llm, mem)(_base_state())
    assert SENTINEL not in _seen_text(llm, mem)


def test_bear_researcher_isolated():
    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
    llm, mem = FakeLLM(), FakeMemory()
    create_bear_researcher(llm, mem)(_base_state())
    assert SENTINEL not in _seen_text(llm, mem)


def test_trader_isolated():
    from tradingagents.agents.trader.trader import create_trader
    llm, mem = FakeLLM(), FakeMemory()
    create_trader(llm, mem)(_base_state(), "Trader")
    assert SENTINEL not in _seen_text(llm, mem)


def test_conservative_debator_isolated():
    from tradingagents.agents.risk_mgmt.conservative_debator import create_safe_debator
    llm, mem = FakeLLM(), FakeMemory()
    create_safe_debator(llm)(_base_state())
    assert SENTINEL not in _seen_text(llm, mem)


def test_aggressive_debator_isolated():
    from tradingagents.agents.risk_mgmt.aggresive_debator import create_risky_debator
    llm, mem = FakeLLM(), FakeMemory()
    create_risky_debator(llm)(_base_state())
    assert SENTINEL not in _seen_text(llm, mem)


def test_neutral_debator_isolated():
    from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
    llm, mem = FakeLLM(), FakeMemory()
    create_neutral_debator(llm)(_base_state())
    assert SENTINEL not in _seen_text(llm, mem)


def test_research_manager_isolated():
    from tradingagents.agents.managers.research_manager import create_research_manager
    llm, mem = FakeLLM(), FakeMemory()
    create_research_manager(llm, mem)(_base_state())
    assert SENTINEL not in _seen_text(llm, mem)


def test_risk_manager_isolated():
    from tradingagents.agents.managers.risk_manager import create_risk_manager
    llm, mem = FakeLLM(), FakeMemory()
    create_risk_manager(llm, mem)(_base_state())
    assert SENTINEL not in _seen_text(llm, mem)


def test_no_value_report_references_left():
    """8 个 agent 源文件中不应再出现 value_report 字样。"""
    offenders = []
    for rel in AGENT_FILES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if "value_report" in text:
            offenders.append(rel)
    assert offenders == [], f"以下文件仍残留 value_report 引用: {offenders}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_value_investing_isolation.py -v`
Expected: 多数行为测试 FAIL（哨兵泄漏，例如 bull/bear/research_manager 经 prompt + memory，trader/risk_manager 经 memory，风险辩论员经 prompt），且 `test_no_value_report_references_left` FAIL（8 个文件都含 `value_report`）。

- [ ] **Step 3: 提交（仅测试，红灯）**

```bash
git add tests/test_value_investing_isolation.py
git commit -m "test: add failing value-investing isolation tests"
```

---

### Task 2: bull_researcher.py

**Files:**
- Modify: `tradingagents/agents/researchers/bull_researcher.py`

- [ ] **Step 1: 删除变量声明（24-25 行）**

替换：
```python
        fundamentals_report = state["fundamentals_report"]
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```
为：
```python
        fundamentals_report = state["fundamentals_report"]
```

- [ ] **Step 2: 删除 debug 日志（86 行）**

删除整行：
```python
        logger.debug(f"🐂 [DEBUG] - 价值投资报告长度: {len(value_report)}")
```

- [ ] **Step 3: 从 curr_situation 移除注入**

替换：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```
为：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}"
        )
```

- [ ] **Step 4: 从 prompt 移除注入（126 行）**

替换：
```python
公司基本面报告：{fundamentals_report}
{value_report_section}
辩论对话历史：{history}
```
为：
```python
公司基本面报告：{fundamentals_report}
辩论对话历史：{history}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_value_investing_isolation.py::test_bull_researcher_isolated -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add tradingagents/agents/researchers/bull_researcher.py
git commit -m "feat(value-isolation): stop bull researcher from reading value_report"
```

---

### Task 3: bear_researcher.py

**Files:**
- Modify: `tradingagents/agents/researchers/bear_researcher.py`

- [ ] **Step 1: 删除变量声明（22-23 行）**

替换：
```python
        fundamentals_report = state["fundamentals_report"]
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```
为：
```python
        fundamentals_report = state["fundamentals_report"]
```

- [ ] **Step 2: 从 curr_situation 移除注入**

替换：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```
为：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}"
        )
```

- [ ] **Step 3: 从 prompt 移除注入（116 行）**

替换：
```python
公司基本面报告：{fundamentals_report}
{value_report_section}
辩论对话历史：{history}
```
为：
```python
公司基本面报告：{fundamentals_report}
辩论对话历史：{history}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_value_investing_isolation.py::test_bear_researcher_isolated -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tradingagents/agents/researchers/bear_researcher.py
git commit -m "feat(value-isolation): stop bear researcher from reading value_report"
```

---

### Task 4: trader.py

**Files:**
- Modify: `tradingagents/agents/trader/trader.py`

注意：此文件 `value_report` 不进 prompt，泄漏在 `curr_situation → memory.get_memories`。

- [ ] **Step 1: 删除变量声明（18-19 行）**

替换：
```python
        fundamentals_report = state["fundamentals_report"]
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```
为：
```python
        fundamentals_report = state["fundamentals_report"]
```

- [ ] **Step 2: 删除 debug 日志（37 行）**

删除整行：
```python
        logger.debug(f"💰 [DEBUG] 价值投资报告长度: {len(value_report)}")
```

- [ ] **Step 3: 从 curr_situation 移除注入**

替换：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```
为：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}"
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_value_investing_isolation.py::test_trader_isolated -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tradingagents/agents/trader/trader.py
git commit -m "feat(value-isolation): stop trader from feeding value_report to memory"
```

---

### Task 5: conservative_debator.py（create_safe_debator）

**Files:**
- Modify: `tradingagents/agents/risk_mgmt/conservative_debator.py`

- [ ] **Step 1: 删除变量声明（24-25 行）**

替换：
```python
        fundamentals_report = state["fundamentals_report"]
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```
为：
```python
        fundamentals_report = state["fundamentals_report"]
```

- [ ] **Step 2: 删除日志行（35 行）**

删除整行：
```python
        logger.info(f"  - value_report: {len(value_report):,} 字符")
```

- [ ] **Step 3: 从 total_length 移除（39 行）**

替换：
```python
        total_length = (len(market_research_report) + len(sentiment_report) +
                       len(news_report) + len(fundamentals_report) + len(value_report) +
                       len(trader_decision) + len(history) +
                       len(current_risky_response) + len(current_neutral_response))
```
为：
```python
        total_length = (len(market_research_report) + len(sentiment_report) +
                       len(news_report) + len(fundamentals_report) +
                       len(trader_decision) + len(history) +
                       len(current_risky_response) + len(current_neutral_response))
```

- [ ] **Step 4: 从 prompt 移除注入（54 行）**

替换：
```python
公司基本面报告：{fundamentals_report}
{value_report_section}
以下是当前对话历史：{history} 以下是激进分析师的最后回应：{current_risky_response} 以下是中性分析师的最后回应：{current_neutral_response}。如果其他观点没有回应，请不要虚构，只需提出您的观点。
```
为：
```python
公司基本面报告：{fundamentals_report}
以下是当前对话历史：{history} 以下是激进分析师的最后回应：{current_risky_response} 以下是中性分析师的最后回应：{current_neutral_response}。如果其他观点没有回应，请不要虚构，只需提出您的观点。
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_value_investing_isolation.py::test_conservative_debator_isolated -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add tradingagents/agents/risk_mgmt/conservative_debator.py
git commit -m "feat(value-isolation): stop safe debator from reading value_report"
```

---

### Task 6: aggresive_debator.py（create_risky_debator）

**Files:**
- Modify: `tradingagents/agents/risk_mgmt/aggresive_debator.py`

- [ ] **Step 1: 删除变量声明（23-24 行）**

替换：
```python
        fundamentals_report = state["fundamentals_report"]
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```
为：
```python
        fundamentals_report = state["fundamentals_report"]
```

- [ ] **Step 2: 删除日志行（34 行）**

删除整行：
```python
        logger.info(f"  - value_report: {len(value_report):,} 字符")
```

- [ ] **Step 3: 从 total_length 移除（38 行）**

替换：
```python
        total_length = (len(market_research_report) + len(sentiment_report) +
                       len(news_report) + len(fundamentals_report) + len(value_report) +
                       len(trader_decision) + len(history) +
                       len(current_safe_response) + len(current_neutral_response))
```
为：
```python
        total_length = (len(market_research_report) + len(sentiment_report) +
                       len(news_report) + len(fundamentals_report) +
                       len(trader_decision) + len(history) +
                       len(current_safe_response) + len(current_neutral_response))
```

- [ ] **Step 4: 从 prompt 移除注入（53 行）**

替换：
```python
公司基本面报告：{fundamentals_report}
{value_report_section}
以下是当前对话历史：{history} 以下是保守分析师的最后论点：{current_safe_response} 以下是中性分析师的最后论点：{current_neutral_response}。如果其他观点没有回应，请不要虚构，只需提出您的观点。
```
为：
```python
公司基本面报告：{fundamentals_report}
以下是当前对话历史：{history} 以下是保守分析师的最后论点：{current_safe_response} 以下是中性分析师的最后论点：{current_neutral_response}。如果其他观点没有回应，请不要虚构，只需提出您的观点。
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_value_investing_isolation.py::test_aggressive_debator_isolated -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add tradingagents/agents/risk_mgmt/aggresive_debator.py
git commit -m "feat(value-isolation): stop risky debator from reading value_report"
```

---

### Task 7: neutral_debator.py

**Files:**
- Modify: `tradingagents/agents/risk_mgmt/neutral_debator.py`

- [ ] **Step 1: 删除变量声明（23-24 行）**

替换：
```python
        fundamentals_report = state["fundamentals_report"]
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```
为：
```python
        fundamentals_report = state["fundamentals_report"]
```

- [ ] **Step 2: 删除日志行（34 行）**

删除整行：
```python
        logger.info(f"  - value_report: {len(value_report):,} 字符 (~{len(value_report)//4:,} tokens)")
```

- [ ] **Step 3: 从 total_prompt_length 移除（42 行）**

替换：
```python
        total_prompt_length = (len(market_research_report) + len(sentiment_report) +
                              len(news_report) + len(fundamentals_report) + len(value_report) +
                              len(trader_decision) + len(history) +
                              len(current_risky_response) + len(current_safe_response))
```
为：
```python
        total_prompt_length = (len(market_research_report) + len(sentiment_report) +
                              len(news_report) + len(fundamentals_report) +
                              len(trader_decision) + len(history) +
                              len(current_risky_response) + len(current_safe_response))
```

- [ ] **Step 4: 从 prompt 移除注入（57 行）**

替换：
```python
公司基本面报告：{fundamentals_report}
{value_report_section}
以下是当前对话历史：{history} 以下是激进分析师的最后回应：{current_risky_response} 以下是安全分析师的最后回应：{current_safe_response}。如果其他观点没有回应，请不要虚构，只需提出您的观点。
```
为：
```python
公司基本面报告：{fundamentals_report}
以下是当前对话历史：{history} 以下是激进分析师的最后回应：{current_risky_response} 以下是安全分析师的最后回应：{current_safe_response}。如果其他观点没有回应，请不要虚构，只需提出您的观点。
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_value_investing_isolation.py::test_neutral_debator_isolated -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add tradingagents/agents/risk_mgmt/neutral_debator.py
git commit -m "feat(value-isolation): stop neutral debator from reading value_report"
```

---

### Task 8: research_manager.py

**Files:**
- Modify: `tradingagents/agents/managers/research_manager.py`

- [ ] **Step 1: 删除变量声明（17-18 行）**

替换：
```python
        fundamentals_report = state["fundamentals_report"]
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
```
为：
```python
        fundamentals_report = state["fundamentals_report"]
```

- [ ] **Step 2: 从 curr_situation 移除注入**

替换：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```
为：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}"
        )
```

- [ ] **Step 3: 从 prompt 移除注入（68-72 行附近）**

替换：
```python
基本面分析：{fundamentals_report}

{value_report_section}

以下是辩论：
```
为：
```python
基本面分析：{fundamentals_report}

以下是辩论：
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_value_investing_isolation.py::test_research_manager_isolated -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tradingagents/agents/managers/research_manager.py
git commit -m "feat(value-isolation): stop research manager from reading value_report"
```

---

### Task 9: risk_manager.py

**Files:**
- Modify: `tradingagents/agents/managers/risk_manager.py`

注意：此文件 `value_report` 不进 prompt，泄漏在 `curr_situation → memory.get_memories`。

- [ ] **Step 1: 删除变量声明（21-22 行）**

替换：
```python
        sentiment_report = state["sentiment_report"]
        value_report = state.get("value_report", "")
        value_report_section = f"\n价值投资分析：{value_report}" if value_report else ""
        trader_plan = state["investment_plan"]
```
为：
```python
        sentiment_report = state["sentiment_report"]
        trader_plan = state["investment_plan"]
```

- [ ] **Step 2: 从 curr_situation 移除注入**

替换：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}{value_report_section}"
        )
```
为：
```python
        curr_situation = (
            f"{market_research_report}\n\n{sentiment_report}\n\n{news_report}"
            f"\n\n{fundamentals_report}"
        )
```

- [ ] **Step 3: 运行测试确认通过**

Run: `pytest tests/test_value_investing_isolation.py::test_risk_manager_isolated -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add tradingagents/agents/managers/risk_manager.py
git commit -m "feat(value-isolation): stop risk manager from feeding value_report to memory"
```

---

### Task 10: 全量隔离测试 + 最终输出回归确认

**Files:**
- 无修改（验证）

- [ ] **Step 1: 跑完整隔离测试（含静态检查）**

Run: `pytest tests/test_value_investing_isolation.py -v`
Expected: 9 个用例全 PASS（8 个行为 + `test_no_value_report_references_left`）。

- [ ] **Step 2: 确认最终输出仍保留 value_report（不经 8 个 agent）**

Run:
```bash
grep -n "value_report" tradingagents/graph/trading_graph.py
grep -rn "value_report" app/routers/analysis.py app/services/simple_analysis_service.py
```
Expected: 仍能看到 `trading_graph.py` 把 `final_state.get("value_report", "")` 写进返回结果，且 `analysis.py` / `simple_analysis_service.py` 仍把 `value_report` 列入 report_fields / 映射为 `value_report.md`。这些不在本 PR 改动范围，说明用户侧展示未受影响。

- [ ] **Step 3: 确认未误改 conditional_logic.py**

Run: `git diff --name-only main -- tradingagents/graph/conditional_logic.py`
Expected: 空输出（该文件未被改动）。

- [ ] **Step 4: 跑相关既有测试，确认无回归**

Run: `pytest tests/ -k "researcher or trader or risk or manager or value" -v`
Expected: 通过（若无匹配用例则为 0 selected，也可接受）。
