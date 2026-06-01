import pytest
from langchain_core.messages import AIMessage

from tradingagents.graph.model_usage import (
    describe_llm,
    get_model_usage_snapshot,
    instrument_llm_for_model_usage,
    model_usage_context,
)


class FakeBoundRunnable:
    model_name = "fake-model"

    def invoke(self, value):
        return AIMessage(
            content="bound-ok",
            usage_metadata={
                "input_tokens": 7,
                "output_tokens": 3,
                "total_tokens": 10,
            },
            response_metadata={
                "token_usage": {"prompt_tokens": 7, "completion_tokens": 3}
            },
        )


class FakeLLM:
    model_name = "fake-model"

    def invoke(self, value):
        return AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 11,
                "output_tokens": 5,
                "total_tokens": 16,
            },
        )

    def bind_tools(self, tools):
        return FakeBoundRunnable()


class ModelOnlyLLM:
    model = "model-only"


class FakeStreamingLLM:
    def stream(self, value):
        yield AIMessage(
            content="chunk",
            usage_metadata={
                "input_tokens": 13,
                "output_tokens": 8,
                "total_tokens": 21,
            },
        )

    async def astream(self, value):
        yield AIMessage(
            content="async-chunk",
            usage_metadata={
                "input_tokens": 17,
                "output_tokens": 9,
                "total_tokens": 26,
            },
        )


def test_instrumented_invoke_records_usage_and_preserves_description():
    llm = instrument_llm_for_model_usage(
        FakeLLM(), provider="codex", model="gpt-5.5"
    )
    assert describe_llm(llm) == "FakeLLM:fake-model"

    with model_usage_context(task_id="task-wrapper", node_name="Trader"):
        llm.invoke("hello")

    node = get_model_usage_snapshot("task-wrapper")["nodes"]["trader"]
    assert node["calls"] == 1
    assert node["provider"] == "codex"
    assert node["model"] == "gpt-5.5"
    assert node["input_tokens"] == 11
    assert node["output_tokens"] == 5


def test_describe_llm_ignores_model_attribute_to_preserve_legacy_behavior():
    assert describe_llm(ModelOnlyLLM()) == "ModelOnlyLLM"


def test_reinstrumented_llm_uses_latest_metadata():
    llm = instrument_llm_for_model_usage(FakeLLM(), provider="codex", model="old")
    instrument_llm_for_model_usage(llm, provider="openai", model="new")

    with model_usage_context(task_id="task-reinstrumented", node_name="Trader"):
        llm.invoke("hello")

    node = get_model_usage_snapshot("task-reinstrumented")["nodes"]["trader"]
    assert node["provider"] == "openai"
    assert node["model"] == "new"


def test_bind_tools_result_is_instrumented():
    llm = instrument_llm_for_model_usage(
        FakeLLM(), provider="codex", model="gpt-5.5"
    )
    bound = llm.bind_tools([])

    with model_usage_context(task_id="task-bound", node_name="Value Analyst"):
        bound.invoke({"messages": []})

    node = get_model_usage_snapshot("task-bound")["nodes"]["value_analyst"]
    assert node["calls"] == 1
    assert node["input_tokens"] == 7
    assert node["output_tokens"] == 3


def test_stream_records_under_context_captured_when_iterator_created():
    llm = instrument_llm_for_model_usage(
        FakeStreamingLLM(), provider="codex", model="gpt-5.5"
    )

    with model_usage_context(task_id="task-stream", node_name="News Analyst"):
        iterator = llm.stream("hello")

    assert list(iterator)

    node = get_model_usage_snapshot("task-stream")["nodes"]["news_analyst"]
    assert node["calls"] == 1
    assert node["input_tokens"] == 13
    assert node["output_tokens"] == 8


@pytest.mark.asyncio
async def test_astream_records_under_context_captured_when_iterator_created():
    llm = instrument_llm_for_model_usage(
        FakeStreamingLLM(), provider="codex", model="gpt-5.5"
    )

    with model_usage_context(task_id="task-astream", node_name="Social Analyst"):
        iterator = llm.astream("hello")

    chunks = []
    async for chunk in iterator:
        chunks.append(chunk)
    assert chunks

    node = get_model_usage_snapshot("task-astream")["nodes"]["social_analyst"]
    assert node["calls"] == 1
    assert node["input_tokens"] == 17
    assert node["output_tokens"] == 9
