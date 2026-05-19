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
