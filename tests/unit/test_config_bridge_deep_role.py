import os

from app.core.config_bridge import bridge_deep_llm_role_to_env


def _clear(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_DEEP_PROVIDER", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_DEEP_BACKEND_URL", raising=False)


def test_empty_model_sets_nothing(monkeypatch):
    _clear(monkeypatch)
    assert bridge_deep_llm_role_to_env("", resolver=lambda m: {}) == {}
    assert "TRADINGAGENTS_DEEP_PROVIDER" not in os.environ


def test_sets_provider_and_backend_url(monkeypatch):
    _clear(monkeypatch)
    resolver = lambda m: {
        "provider": "dashscope",
        "backend_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "k",
    }
    result = bridge_deep_llm_role_to_env("qwen-max", resolver=resolver)
    assert result == {
        "TRADINGAGENTS_DEEP_PROVIDER": "dashscope",
        "TRADINGAGENTS_DEEP_BACKEND_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    assert os.environ["TRADINGAGENTS_DEEP_PROVIDER"] == "dashscope"
    assert os.environ["TRADINGAGENTS_DEEP_BACKEND_URL"].endswith("/compatible-mode/v1")


def test_codex_sets_provider_without_backend_url(monkeypatch):
    _clear(monkeypatch)
    resolver = lambda m: {"provider": "codex", "backend_url": "", "api_key": None}
    result = bridge_deep_llm_role_to_env("gpt-5.5-codex", resolver=resolver)
    assert result == {"TRADINGAGENTS_DEEP_PROVIDER": "codex"}
    assert "TRADINGAGENTS_DEEP_BACKEND_URL" not in os.environ


def test_none_model_sets_nothing(monkeypatch):
    _clear(monkeypatch)
    assert bridge_deep_llm_role_to_env(None, resolver=lambda m: {}) == {}
    assert "TRADINGAGENTS_DEEP_PROVIDER" not in os.environ
