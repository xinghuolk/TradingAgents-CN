from types import SimpleNamespace

from tradingagents.graph.model_usage import (
    clear_model_usage,
    get_model_usage_snapshot,
    model_usage_context,
    record_openai_response_usage,
)


def test_record_openai_response_usage_extracts_object_response_tokens():
    clear_model_usage("task-openai")
    response = SimpleNamespace(
        model="gpt-4.1-mini",
        usage=SimpleNamespace(input_tokens=20, output_tokens=6),
    )

    with model_usage_context(task_id="task-openai", node_name="tools_news"):
        record_openai_response_usage(
            response,
            duration_seconds=0.25,
            currency="USD",
        )

    node = get_model_usage_snapshot("task-openai")["nodes"]["news_analyst"]
    assert node["calls"] == 1
    assert node["provider"] == "openai"
    assert node["model"] == "gpt-4.1-mini"
    assert node["input_tokens"] == 20
    assert node["output_tokens"] == 6


def test_record_openai_response_usage_extracts_dict_response_with_default_model():
    clear_model_usage("task-openai-dict")
    response = {
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 4,
        }
    }

    with model_usage_context(task_id="task-openai-dict", node_name="tools_news"):
        record_openai_response_usage(
            response,
            default_model="gpt-4.1-nano",
            duration_seconds=0.1,
            currency="USD",
        )

    node = get_model_usage_snapshot("task-openai-dict")["nodes"]["news_analyst"]
    assert node["calls"] == 1
    assert node["provider"] == "openai"
    assert node["model"] == "gpt-4.1-nano"
    assert node["input_tokens"] == 11
    assert node["output_tokens"] == 4


def test_record_openai_response_usage_noops_without_context():
    clear_model_usage("task-openai-no-context")

    record_openai_response_usage(
        {
            "model": "gpt-4.1-mini",
            "usage": {"input_tokens": 7, "output_tokens": 2},
        },
        duration_seconds=0.05,
        currency="USD",
    )

    assert get_model_usage_snapshot("task-openai-no-context")["summary"][
        "total_calls"
    ] == 0


def test_stock_news_openai_records_response_usage(monkeypatch):
    from tradingagents.dataflows import interface

    recorded = []

    class FakeResponses:
        def create(self, **kwargs):
            return SimpleNamespace(
                model="gpt-4.1-mini",
                usage=SimpleNamespace(input_tokens=12, output_tokens=5),
                output=[
                    None,
                    SimpleNamespace(
                        content=[SimpleNamespace(text="news response")]
                    ),
                ],
                request_kwargs=kwargs,
            )

    class FakeOpenAI:
        def __init__(self, *, base_url):
            self.base_url = base_url
            self.responses = FakeResponses()

    def fake_record(response, **kwargs):
        recorded.append((response, kwargs))

    monkeypatch.setattr(
        interface,
        "get_config",
        lambda: {
            "backend_url": "https://openai-compatible.example",
            "quick_think_llm": "gpt-4.1-mini",
        },
    )
    monkeypatch.setattr(interface, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(interface, "record_openai_response_usage", fake_record)

    result = interface.get_stock_news_openai("AAPL", "2026-06-01")

    assert result == "news response"
    assert len(recorded) == 1
    assert recorded[0][1]["provider"] == "openai"
    assert recorded[0][1]["default_model"] == "gpt-4.1-mini"
    assert recorded[0][1]["currency"] == "USD"
    assert recorded[0][1]["duration_seconds"] >= 0
