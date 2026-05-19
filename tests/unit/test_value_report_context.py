from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.managers.research_manager import create_research_manager


class CapturingLLM:
    def __init__(self):
        self.prompt = None

    def invoke(self, prompt):
        self.prompt = prompt
        return "bull response"


def test_bull_researcher_prompt_includes_value_report(monkeypatch):
    _patch_stock_lookup(monkeypatch)

    llm = CapturingLLM()
    node = create_bull_researcher(llm, memory=None)

    node(_base_researcher_state(value_report="penetrating yield and cash health report"))

    assert "价值投资分析" in llm.prompt
    assert "penetrating yield and cash health report" in llm.prompt


def test_bull_researcher_prompt_omits_empty_value_report_label(monkeypatch):
    _patch_stock_lookup(monkeypatch)

    for value_state in ({}, {"value_report": ""}):
        llm = CapturingLLM()
        node = create_bull_researcher(llm, memory=None)

        state = _base_researcher_state()
        state.update(value_state)
        node(state)

        assert "价值投资分析：" not in llm.prompt


def test_research_manager_prompt_includes_value_report():
    llm = CapturingLLM()
    node = create_research_manager(llm, memory=None)

    node(_base_researcher_state(value_report="discounted cash flow margin of safety"))

    assert "价值投资分析" in llm.prompt
    assert "discounted cash flow margin of safety" in llm.prompt


def _patch_stock_lookup(monkeypatch):
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


def _base_researcher_state(value_report=None):
    state = {
        "company_of_interest": "000001",
        "market_report": "market report",
        "sentiment_report": "sentiment report",
        "news_report": "news report",
        "fundamentals_report": "fundamentals report",
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        },
    }
    if value_report is not None:
        state["value_report"] = value_report
    return state
