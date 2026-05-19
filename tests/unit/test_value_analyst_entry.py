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
