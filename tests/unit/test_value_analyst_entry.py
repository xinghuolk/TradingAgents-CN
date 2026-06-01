from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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
                        "name": "prepare_turtle_analysis",
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


def test_should_continue_value_routes_pending_tool_call_at_count_cap():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "prepare_turtle_analysis",
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

    assert ConditionalLogic().should_continue_value(state) == "tools_value"


def test_should_continue_value_stops_after_executed_tool_call_cap():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "prepare_turtle_analysis",
                        "args": {"ticker": "000001", "market": "A"},
                        "id": "call_value",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content="value analysis result",
                name="prepare_turtle_analysis",
                tool_call_id="call_value",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "prepare_turtle_analysis",
                        "args": {"ticker": "000001", "market": "A"},
                        "id": "call_value_retry",
                        "type": "tool_call",
                    }
                ],
            ),
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


def test_graph_setup_uses_deep_llm_for_value_analyst_and_quick_llm_for_market(
    monkeypatch,
):
    quick_llm = MagicMock(name="quick_llm")
    deep_llm = MagicMock(name="deep_llm")
    captured_llms = {}

    def make_node(name):
        def node(state):
            return {f"{name}_report": "ok"}

        return node

    def capture_factory(name):
        def factory(llm, _toolkit):
            captured_llms[name] = llm
            return make_node(name)

        return factory

    monkeypatch.setattr(
        "tradingagents.graph.setup.create_market_analyst",
        capture_factory("market"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_value_analyst",
        capture_factory("value"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_bull_researcher",
        lambda llm, memory: make_node("bull"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_bear_researcher",
        lambda llm, memory: make_node("bear"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_research_manager",
        lambda llm, memory: make_node("research_manager"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_trader",
        lambda llm, memory: make_node("trader"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_risky_debator",
        lambda llm: make_node("risky"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_neutral_debator",
        lambda llm: make_node("neutral"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_safe_debator",
        lambda llm: make_node("safe"),
    )
    monkeypatch.setattr(
        "tradingagents.graph.setup.create_risk_manager",
        lambda llm, memory: make_node("risk_manager"),
    )

    setup = GraphSetup(
        quick_thinking_llm=quick_llm,
        deep_thinking_llm=deep_llm,
        toolkit=SimpleNamespace(),
        tool_nodes={
            "market": ToolNode([dummy_value_tool]),
            "value": ToolNode([dummy_value_tool]),
        },
        bull_memory=None,
        bear_memory=None,
        trader_memory=None,
        invest_judge_memory=None,
        risk_manager_memory=None,
        conditional_logic=ConditionalLogic(),
        config={"llm_provider": "test"},
    )

    setup.setup_graph(["market", "value"])

    assert captured_llms["value"] is deep_llm
    assert captured_llms["market"] is quick_llm
