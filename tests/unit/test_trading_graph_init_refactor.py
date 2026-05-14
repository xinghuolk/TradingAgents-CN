"""Smoke tests for TradingAgentsGraph.__init__ after the elif-chain refactor.

Each retained provider must construct without errors.
"""
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _stubs(stub_optional_llm_deps):
    pass


def _make_config(provider: str, *, with_api_key: bool = True) -> dict:
    cfg = {
        "llm_provider": provider,
        "deep_think_llm": "test-deep",
        "quick_think_llm": "test-quick",
        "backend_url": "https://example.test",
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "memory_enabled": False,  # disable memory to avoid embedding side-effects
        "project_dir": "/tmp",
        "quick_model_config": {"max_tokens": 100, "temperature": 0.0, "timeout": 10},
        "deep_model_config": {"max_tokens": 100, "temperature": 0.0, "timeout": 10},
    }
    if with_api_key:
        cfg["quick_api_key"] = "test-key"
        cfg["deep_api_key"] = "test-key"
    return cfg


class TestRefactorPreservesRetainedProviders:
    def test_openai_provider(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("openai"))
            assert g.quick_thinking_llm is not None

    def test_anthropic_provider(self):
        with patch("langchain_anthropic.ChatAnthropic"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("anthropic"))
            assert g.quick_thinking_llm is not None

    def test_google_provider(self):
        with patch("tradingagents.llm_adapters.google_openai_adapter.ChatGoogleOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("google"))

    def test_deepseek_provider(self):
        with patch("tradingagents.llm_adapters.deepseek_adapter.ChatDeepSeek"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("deepseek"))

    def test_openrouter_provider(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("openrouter"))

    def test_ollama_provider(self):
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=_make_config("ollama"))

    def test_claude_code_oauth(self):
        # Provide access_token via api_key so adapter doesn't call sc.resolve
        cfg = _make_config("claude_code")
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=cfg)

    def test_codex_oauth(self):
        cfg = _make_config("codex")
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        g = TradingAgentsGraph(config=cfg)


class TestRefactorRoutesUnknownToFallback:
    def test_dashscope_via_fallback(self, monkeypatch):
        """Now-removed dashscope native branch should land on generic OpenAI-compat."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
        cfg = _make_config("dashscope")
        cfg["backend_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=cfg)
            assert g.quick_thinking_llm is not None

    def test_qianfan_via_fallback(self, monkeypatch):
        monkeypatch.setenv("QIANFAN_API_KEY", "test")
        cfg = _make_config("qianfan")
        cfg["backend_url"] = "https://qianfan.baidubce.com/v2"
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=cfg)

    def test_zhipu_via_fallback(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "test")
        cfg = _make_config("zhipu")
        cfg["backend_url"] = "https://open.bigmodel.cn/api/paas/v4"
        with patch("langchain_openai.ChatOpenAI"):
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            g = TradingAgentsGraph(config=cfg)
