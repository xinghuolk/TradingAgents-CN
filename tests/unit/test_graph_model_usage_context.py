from types import SimpleNamespace

from tradingagents.graph.model_usage import (
    get_model_usage_snapshot,
    model_usage_context,
    record_llm_call,
)
from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.trading_graph import TradingAgentsGraph


def test_wrapped_graph_node_sets_canonical_context():
    def node(state):
        record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.1)
        return {"ok": True}

    wrapped = GraphSetup._wrap_model_usage_node("Value Analyst", node)

    with model_usage_context(task_id="task-node"):
        assert wrapped({}) == {"ok": True}

    assert (
        get_model_usage_snapshot("task-node")["nodes"]["value_analyst"]["calls"] == 1
    )


class FakeGraph:
    def stream(self, _state, **_args):
        yield {"final_trade_decision": "BUY"}


def test_propagate_records_signal_processor_usage_and_clears_task_store(monkeypatch):
    graph = object.__new__(TradingAgentsGraph)
    graph.debug = False
    graph.config = {}
    graph.graph = FakeGraph()
    graph.propagator = SimpleNamespace(
        create_initial_state=lambda company_name, trade_date: {
            "company_of_interest": company_name,
            "trade_date": trade_date,
        },
        get_graph_args=lambda use_progress_callback: {"stream_mode": "values"},
    )
    graph.deep_thinking_llm = SimpleNamespace(model_name="deep-model")
    graph._print_timing_summary = lambda node_timings, total_elapsed: None
    graph._build_performance_data = lambda node_timings, total_elapsed: {
        "total_time": total_elapsed,
    }
    graph._log_state = lambda trade_date, final_state: None

    def fake_process_signal(full_signal, stock_symbol=None):
        record_llm_call(
            provider="codex",
            model="gpt-5.5",
            duration_seconds=0.1,
            input_tokens=4,
            output_tokens=2,
        )
        return {"action": full_signal, "stock": stock_symbol}

    monkeypatch.setattr(graph, "process_signal", fake_process_signal)

    final_state, decision = graph.propagate(
        "000001",
        "2026-06-01",
        task_id="task-propagate",
    )

    signal_usage = final_state["model_usage"]["nodes"]["signal_processor"]
    assert signal_usage["calls"] == 1
    assert signal_usage["input_tokens"] == 4
    assert signal_usage["output_tokens"] == 2
    assert decision["action"] == "BUY"
    assert get_model_usage_snapshot("task-propagate")["summary"]["total_calls"] == 0
