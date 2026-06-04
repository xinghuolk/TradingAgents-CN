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
    create_trader(llm, mem)(_base_state())
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
