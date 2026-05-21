import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tradingagents.agents.analysts.value_analyst import create_value_analyst
from tradingagents.agents.utils.agent_utils import Toolkit
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleFactValue,
    TurtleMarketFacts,
    TurtleReportFacts,
)
from tradingagents.tools.turtle_analysis_tool import prepare_turtle_analysis_payload


def _money_fact(name: str, value: float) -> TurtleFactValue:
    return TurtleFactValue(
        name=name,
        value=MoneyAmount(
            value=value,
            currency="CNY",
            unit="hundred_million",
            source_label="fixture",
            source_reference=f"fixture.{name}",
        ),
        source_label="fixture",
        source_reference=f"fixture.{name}",
    )


def _number_fact(name: str, value: float) -> TurtleFactValue:
    return TurtleFactValue(
        name=name,
        value=value,
        source_label="fixture",
        source_reference=f"fixture.{name}",
    )


def test_prepare_turtle_analysis_payload_returns_facts_and_non_decisionable_signals(monkeypatch):
    from tradingagents.tools import turtle_analysis_tool

    monkeypatch.setattr(
        turtle_analysis_tool,
        "get_turtle_report_facts",
        lambda *, ticker, market, trade_date: TurtleReportFacts(
            fields={
                "net_profit": _money_fact("net_profit", 100),
                "operating_cash_flow": _money_fact("operating_cash_flow", 120),
                "capex": _money_fact("capex", 20),
                "cash": _money_fact("cash", 500),
                "interest_bearing_debt": _money_fact("interest_bearing_debt", 50),
            },
            caveats=["report caveat"],
        ),
    )
    monkeypatch.setattr(
        turtle_analysis_tool,
        "get_turtle_market_facts",
        lambda *, ticker, market, holding_channel: TurtleMarketFacts(
            fields={
                "buyback_amount": _money_fact("buyback_amount", 10),
                "avg_payout_ratio_3y": _number_fact("avg_payout_ratio_3y", 0.5),
                "tax_rate": _number_fact("tax_rate", 0.2),
            },
            caveats=["market_cap missing"],
        ),
    )

    payload = turtle_analysis_tool.prepare_turtle_analysis_payload(
        ticker="600519",
        market="A",
        trade_date="2026-05-19",
        company_name="贵州茅台",
    )

    data = json.loads(payload)
    assert set(data) == {"facts", "signals"}
    assert data["facts"]["status"] == "complete"
    assert data["facts"]["caveats"] == ["report caveat", "market_cap missing"]
    assert data["signals"]["status"] == "non_decisionable"
    assert "market_cap" in data["signals"]["results"]["R"]["missing_inputs"]


class RecordingLLM:
    def __init__(self):
        self.bound_tools = []
        self.plain_invocations = []

    def bind_tools(self, tools):
        self.bound_tools.append(tools)
        return self

    def invoke(self, prompt):
        self.plain_invocations.append(prompt)
        return AIMessage(content="plain turtle report")


class ToolCallPromptRecordingLLM:
    def __init__(self):
        self.bound_tools = []
        self.tool_call_prompts = []

    def bind_tools(self, tools):
        self.bound_tools.append(tools)

        def invoke_bound(prompt):
            self.tool_call_prompts.append(prompt)
            return AIMessage(content="bound turtle prompt")

        return invoke_bound


def test_value_analyst_tool_call_prompt_uses_turtle_v015_framing(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.agents.analysts.value_analyst._get_company_name",
        lambda ticker, market_info: "贵州茅台",
    )

    llm = ToolCallPromptRecordingLLM()
    toolkit = SimpleNamespace(prepare_turtle_analysis=lambda **kwargs: "{}")
    analyst = create_value_analyst(llm, toolkit)

    analyst(
        {
            "messages": [HumanMessage(content="analyze 600519")],
            "trade_date": "2026-05-19",
            "company_of_interest": "600519",
            "value_tool_call_count": 0,
        }
    )

    assert len(llm.bound_tools) == 1
    assert len(llm.tool_call_prompts) == 1
    prompt_text = str(llm.tool_call_prompts[0])
    assert "Turtle v0.15" in prompt_text
    assert "TurtleFacts" in prompt_text
    assert "TurtleComputedSignals" in prompt_text
    assert "M / R / GG / HH" in prompt_text
    assert "5维健康评分" not in prompt_text
    assert "健康标签" not in prompt_text
    assert "适合长期持有" not in prompt_text


def test_value_analyst_uses_plain_llm_after_prepare_turtle_analysis_tool_message(monkeypatch):
    from tradingagents.tools import turtle_analysis_tool

    monkeypatch.setattr(
        turtle_analysis_tool,
        "get_turtle_report_facts",
        lambda *, ticker, market, trade_date: TurtleReportFacts(
            fields={
                "net_profit": _money_fact("net_profit", 100),
                "operating_cash_flow": _money_fact("operating_cash_flow", 120),
                "capex": _money_fact("capex", 20),
                "cash": _money_fact("cash", 500),
                "interest_bearing_debt": _money_fact("interest_bearing_debt", 50),
            },
        ),
    )
    monkeypatch.setattr(
        turtle_analysis_tool,
        "get_turtle_market_facts",
        lambda *, ticker, market, holding_channel: TurtleMarketFacts(
            fields={
                "market_cap": _money_fact("market_cap", 1000),
                "buyback_amount": _money_fact("buyback_amount", 10),
                "avg_payout_ratio_3y": _number_fact("avg_payout_ratio_3y", 0.5),
                "tax_rate": _number_fact("tax_rate", 0.2),
            },
        ),
    )
    payload = turtle_analysis_tool.prepare_turtle_analysis_payload(
        ticker="600519",
        market="A",
        trade_date="2026-05-19",
        company_name="贵州茅台",
    )

    monkeypatch.setattr(
        "tradingagents.agents.analysts.value_analyst._get_company_name",
        lambda ticker, market_info: "贵州茅台",
    )

    llm = RecordingLLM()
    toolkit = SimpleNamespace(prepare_turtle_analysis=lambda **kwargs: "{}")
    analyst = create_value_analyst(llm, toolkit)

    result = analyst(
        {
            "messages": [
                HumanMessage(content="analyze 600519"),
                ToolMessage(
                    content=payload,
                    name="prepare_turtle_analysis",
                    tool_call_id="call_turtle",
                ),
            ],
            "trade_date": "2026-05-19",
            "company_of_interest": "600519",
            "value_tool_call_count": 0,
        }
    )

    assert result["value_report"] == "plain turtle report"
    assert result["value_tool_call_count"] == 1
    assert llm.bound_tools == []
    assert len(llm.plain_invocations) == 1
    assert "Turtle v0.15 决策分析任务" in llm.plain_invocations[0]


def test_toolkit_prepare_turtle_analysis_schema_company_name_is_optional():
    schema_model = Toolkit.prepare_turtle_analysis.args_schema
    if hasattr(schema_model, "model_json_schema"):
        schema = schema_model.model_json_schema()
    else:
        schema = schema_model.schema()

    assert "company_name" not in schema.get("required", [])


@pytest.mark.parametrize(
    "payload",
    [
        {
            "facts": {
                "context": {
                    "ticker": "600519",
                    "market": "A",
                    "trade_date": "2026-05-19",
                    "period_end": "2025-12-31",
                    "holding_channel": "long_term_domestic",
                    "company_name": "贵州茅台",
                },
                "report": {
                    "fields": {
                        "net_profit": {
                            "name": "net_profit",
                            "value": 100,
                        }
                    },
                    "metadata": {},
                    "caveats": [],
                },
                "market": {"fields": {}, "caveats": []},
                "status": "complete",
                "caveats": [],
            },
            "signals": {"status": "non_decisionable", "results": {}, "veto_reasons": [], "caveats": []},
        },
        {
            "facts": {
                "context": {
                    "ticker": "600519",
                    "market": "A",
                    "trade_date": "2026-05-19",
                    "period_end": "2025-12-31",
                    "holding_channel": "long_term_domestic",
                    "company_name": "贵州茅台",
                },
                "report": {"fields": {}, "metadata": {}, "caveats": []},
                "market": {"fields": {}, "caveats": []},
                "status": "non_decisionable",
                "caveats": [],
            },
            "signals": {
                "status": "non_decisionable",
                "results": {
                    "R": {
                        "name": "R",
                        "value": None,
                        "sources": [],
                        "missing_inputs": ["market_cap"],
                    }
                },
                "veto_reasons": [],
                "caveats": [],
            },
        },
        {
            "facts": {
                "context": {
                    "ticker": "600519",
                    "market": "A",
                    "trade_date": "2026-05-19",
                    "period_end": "2025-12-31",
                    "holding_channel": "long_term_domestic",
                    "company_name": "贵州茅台",
                },
                "report": {
                    "fields": {
                        "net_profit": "not a fact object",
                    },
                    "metadata": {},
                    "caveats": [],
                },
                "market": {"fields": {}, "caveats": []},
                "status": "complete",
                "caveats": [],
            },
            "signals": {
                "status": "complete",
                "results": {
                    "R": {
                        "name": "R",
                        "formula": "R = profit / market_cap",
                        "substitution": "R = 10 / 100",
                        "value": 10.0,
                        "unit": "percent",
                        "sources": ["fixture"],
                        "missing_inputs": [],
                        "status": "complete",
                    }
                },
                "veto_reasons": [],
                "caveats": [],
            },
        },
    ],
)
def test_value_analyst_rejects_partial_turtle_payload_without_plain_llm(monkeypatch, payload):
    monkeypatch.setattr(
        "tradingagents.agents.analysts.value_analyst._get_company_name",
        lambda ticker, market_info: "贵州茅台",
    )

    llm = RecordingLLM()
    toolkit = SimpleNamespace(prepare_turtle_analysis=lambda **kwargs: "{}")
    analyst = create_value_analyst(llm, toolkit)

    result = analyst(
        {
            "messages": [
                HumanMessage(content="analyze 600519"),
                ToolMessage(
                    content=json.dumps(payload, ensure_ascii=False),
                    name="prepare_turtle_analysis",
                    tool_call_id="call_turtle",
                ),
            ],
            "trade_date": "2026-05-19",
            "company_of_interest": "600519",
            "value_tool_call_count": 0,
        }
    )

    assert llm.plain_invocations == []
    assert "Turtle" in result["value_report"]
    assert "失败" in result["value_report"] or "不可决策" in result["value_report"]
    assert result["value_tool_call_count"] == 1


class TestAggregatedStatus:
    def test_merge_status_picks_most_severe_of_report_and_market(self):
        """report=complete, market=degraded → 顶层 degraded"""
        with patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_report_facts",
            return_value=TurtleReportFacts(status="complete"),
        ), patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_market_facts",
            return_value=TurtleMarketFacts(status="degraded"),
        ):
            payload = prepare_turtle_analysis_payload(
                ticker="600519", market="A",
                trade_date="2026-05-19", company_name="X",
            )
        data = json.loads(payload)
        assert data["facts"]["status"] == "degraded"

    def test_non_decisionable_dominates(self):
        with patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_report_facts",
            return_value=TurtleReportFacts(status="non_decisionable"),
        ), patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_market_facts",
            return_value=TurtleMarketFacts(status="complete"),
        ):
            payload = prepare_turtle_analysis_payload(
                ticker="600519", market="A",
                trade_date="2026-05-19", company_name="X",
            )
        data = json.loads(payload)
        assert data["facts"]["status"] == "non_decisionable"


class TestHoldingChannelPassthrough:
    def test_none_holding_channel_propagated_to_market_adapter(self):
        captured = {}

        def fake_market(ticker, market, holding_channel):
            captured["holding_channel"] = holding_channel
            return TurtleMarketFacts(status="degraded")

        with patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_report_facts",
            return_value=TurtleReportFacts(status="complete"),
        ), patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_market_facts",
            side_effect=fake_market,
        ):
            prepare_turtle_analysis_payload(
                ticker="600519", market="A",
                trade_date="2026-05-19", company_name="X",
                holding_channel=None,
            )
        assert captured["holding_channel"] is None

    def test_explicit_holding_channel_propagated(self):
        captured = {}

        def fake_market(ticker, market, holding_channel):
            captured["holding_channel"] = holding_channel
            return TurtleMarketFacts(status="complete")

        with patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_report_facts",
            return_value=TurtleReportFacts(status="complete"),
        ), patch(
            "tradingagents.tools.turtle_analysis_tool.get_turtle_market_facts",
            side_effect=fake_market,
        ):
            prepare_turtle_analysis_payload(
                ticker="600519", market="A",
                trade_date="2026-05-19", company_name="X",
                holding_channel="long_term_domestic",
            )
        assert captured["holding_channel"] == "long_term_domestic"
