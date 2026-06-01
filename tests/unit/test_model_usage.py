from unittest.mock import patch

from tradingagents.graph.model_usage import (
    canonical_node_key,
    clear_model_usage,
    get_model_usage_snapshot,
    model_usage_context,
    record_llm_call,
)


def test_cost_auto_computed_from_tokens_when_not_provided():
    """未直接传 cost 但有 provider/model/token 时,用计价表自动估算成本。"""
    with patch(
        "tradingagents.config.config_manager.config_manager.calculate_cost",
        return_value=(0.0123, "CNY"),
    ) as mock_calc:
        with model_usage_context(task_id="task-autocost", node_name="value_analyst"):
            record_llm_call(
                provider="codex",
                model="gpt-5.5",
                duration_seconds=1.0,
                input_tokens=100,
                output_tokens=20,
            )
    mock_calc.assert_called_once_with("codex", "gpt-5.5", 100, 20)
    node = get_model_usage_snapshot("task-autocost")["nodes"]["value_analyst"]
    assert node["cost"] == 0.0123
    assert node["currency"] == "CNY"
    assert node["costs_by_currency"] == {"CNY": 0.0123}
    assert node["partial"] is False
    clear_model_usage("task-autocost")


def test_cost_not_computed_when_pricing_missing_stays_partial():
    """计价表无匹配(返回 0.0)时不采纳,cost 保持 None / partial,不误报 0 成本。"""
    with patch(
        "tradingagents.config.config_manager.config_manager.calculate_cost",
        return_value=(0.0, "CNY"),
    ):
        with model_usage_context(task_id="task-nopricing", node_name="value_analyst"):
            record_llm_call(
                provider="unknown",
                model="mystery",
                duration_seconds=1.0,
                input_tokens=100,
                output_tokens=20,
            )
    node = get_model_usage_snapshot("task-nopricing")["nodes"]["value_analyst"]
    assert node["cost"] is None
    assert node["partial"] is True
    clear_model_usage("task-nopricing")


def test_canonical_node_keys_cover_graph_and_tool_nodes():
    assert canonical_node_key("Market Analyst") == "market_analyst"
    assert canonical_node_key("tools_market") == "market_analyst"
    assert canonical_node_key("Msg Clear Market") == "market_analyst"
    assert canonical_node_key("Value Analyst") == "value_analyst"
    assert canonical_node_key("tools_value") == "value_analyst"
    assert canonical_node_key("Msg Clear Value") == "value_analyst"
    assert canonical_node_key("Risk Judge") == "risk_judge"
    assert canonical_node_key("SignalProcessor") == "signal_processor"


def test_record_llm_call_aggregates_under_current_node():
    with model_usage_context(task_id="task-1"):
        with model_usage_context(node_name="Value Analyst"):
            record_llm_call(
                provider="codex",
                model="gpt-5.5",
                duration_seconds=1.25,
                input_tokens=100,
                output_tokens=25,
                cost=0.12,
                currency="CNY",
            )

    snapshot = get_model_usage_snapshot("task-1")
    node = snapshot["nodes"]["value_analyst"]
    assert node["display_name"] == "价值投资分析"
    assert node["provider"] == "codex"
    assert node["model"] == "gpt-5.5"
    assert node["calls"] == 1
    assert node["input_tokens"] == 100
    assert node["output_tokens"] == 25
    assert node["cost"] == 0.12
    assert node["currency"] == "CNY"
    assert node["partial"] is False
    assert snapshot["summary"]["total_calls"] == 1
    assert snapshot["summary"]["costs_by_currency"] == {"CNY": 0.12}


def test_partial_and_mixed_currency_snapshot():
    with model_usage_context(task_id="task-2"):
        with model_usage_context(node_name="News Analyst"):
            record_llm_call(
                provider="openai",
                model="gpt-4.1",
                duration_seconds=0.5,
                cost=0.01,
                currency="USD",
            )
            record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.7)

    node = get_model_usage_snapshot("task-2")["nodes"]["news_analyst"]
    assert node["provider"] == "mixed"
    assert node["model"] == "mixed"
    assert node["providers"] == ["codex", "openai"]
    assert node["models"] == ["gpt-4.1", "gpt-5.5"]
    assert node["cost"] is None
    assert node["currency"] is None
    assert node["costs_by_currency"] == {"USD": 0.01}
    assert node["partial"] is True


def test_clear_model_usage_removes_task_snapshot():
    with model_usage_context(task_id="task-3", node_name="Trader"):
        record_llm_call(provider="deepseek", model="flash", duration_seconds=0.1)
    assert get_model_usage_snapshot("task-3")["summary"]["total_calls"] == 1
    clear_model_usage("task-3")
    assert get_model_usage_snapshot("task-3")["summary"]["total_calls"] == 0


def test_token_partial_call_with_known_cost_keeps_cost_convenience_fields():
    with model_usage_context(task_id="task-4", node_name="Msg Clear Market"):
        record_llm_call(
            provider="codex",
            model="gpt-5.5",
            duration_seconds=0.2,
            cost=0.03,
            currency="CNY",
        )

    node = get_model_usage_snapshot("task-4")["nodes"]["market_analyst"]
    assert node["partial"] is True
    assert node["cost"] == 0.03
    assert node["currency"] == "CNY"
    assert node["costs_by_currency"] == {"CNY": 0.03}


def test_missing_cost_metadata_suppresses_cost_convenience_fields():
    with model_usage_context(task_id="task-5", node_name="Value Analyst"):
        record_llm_call(
            provider="codex",
            model="gpt-5.5",
            duration_seconds=0.2,
            input_tokens=10,
            output_tokens=5,
            cost=0.03,
            currency="CNY",
        )
        record_llm_call(
            provider="codex",
            model="gpt-5.5",
            duration_seconds=0.2,
            input_tokens=20,
            output_tokens=10,
        )

    node = get_model_usage_snapshot("task-5")["nodes"]["value_analyst"]
    assert node["partial"] is True
    assert node["cost"] is None
    assert node["currency"] is None
    assert node["costs_by_currency"] == {"CNY": 0.03}


def test_blank_currency_with_cost_marks_partial_without_convenience_cost():
    with model_usage_context(task_id="task-6", node_name="Trader"):
        record_llm_call(
            provider="codex",
            model="gpt-5.5",
            duration_seconds=0.2,
            input_tokens=10,
            output_tokens=5,
            cost=0.03,
            currency="CNY",
        )
        record_llm_call(
            provider="codex",
            model="gpt-5.5",
            duration_seconds=0.2,
            input_tokens=10,
            output_tokens=5,
            cost=0.04,
            currency="  ",
        )

    node = get_model_usage_snapshot("task-6")["nodes"]["trader"]
    assert node["partial"] is True
    assert node["cost"] is None
    assert node["currency"] is None
    assert node["costs_by_currency"] == {"CNY": 0.03}


def test_record_llm_call_noops_without_task_or_node_context():
    record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.1)

    with model_usage_context(task_id="task-7"):
        record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.1)

    assert get_model_usage_snapshot("task-7")["summary"]["total_calls"] == 0


def test_nested_model_usage_context_restores_previous_node_context():
    with model_usage_context(task_id="task-8", node_name="Market Analyst"):
        record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.1)
        with model_usage_context(node_name="Value Analyst"):
            record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.1)
        record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.1)

    nodes = get_model_usage_snapshot("task-8")["nodes"]
    assert nodes["market_analyst"]["calls"] == 2
    assert nodes["value_analyst"]["calls"] == 1
