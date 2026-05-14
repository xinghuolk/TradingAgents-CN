"""Unit tests for the claude_code / codex branches in create_llm_by_provider."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _use_stubs(stub_optional_llm_deps):
    """Auto-apply the session-scoped LLM dep stubs from conftest."""


def _import_target():
    """Lazy import so the stubs (provided by stub_optional_llm_deps) are in
    sys.modules before trading_graph triggers the dashscope/chromadb imports."""
    from tradingagents.graph.trading_graph import create_llm_by_provider
    return create_llm_by_provider


class TestCreateLlmByProviderSubscription:
    def test_claude_code_branch_returns_oauth_adapter(self):
        fake = MagicMock(name="ChatClaudeCodeOAuth-instance")
        with patch(
            "tradingagents.llm_adapters.claude_code_adapter.ChatClaudeCodeOAuth",
            return_value=fake,
        ) as ctor:
            llm = _import_target()(
                provider="claude_code",
                model="claude-opus-4-7",
                backend_url="",        # ignored for OAuth path
                temperature=0.4,
                max_tokens=4000,
                timeout=180,
                api_key=None,
            )
        assert llm is fake
        ctor.assert_called_once()
        kwargs = ctor.call_args.kwargs
        assert kwargs["model"] == "claude-opus-4-7"
        assert kwargs["temperature"] == 0.4
        assert kwargs["max_tokens"] == 4000

    def test_codex_branch_returns_oauth_adapter(self):
        fake = MagicMock(name="ChatCodexOAuth-instance")
        with patch(
            "tradingagents.llm_adapters.codex_adapter.ChatCodexOAuth",
            return_value=fake,
        ) as ctor:
            llm = _import_target()(
                provider="codex",
                model="gpt-5",
                backend_url="",
                temperature=0.5,
                max_tokens=2000,
                timeout=180,
                api_key=None,
            )
        assert llm is fake
        kwargs = ctor.call_args.kwargs
        assert kwargs["model"] == "gpt-5"

    def test_claude_code_case_insensitive(self):
        fake = MagicMock()
        with patch(
            "tradingagents.llm_adapters.claude_code_adapter.ChatClaudeCodeOAuth",
            return_value=fake,
        ):
            assert _import_target()("Claude_Code", "claude-opus-4-7", "", 0, 1, 1) is fake
            assert _import_target()("CLAUDE_CODE", "claude-opus-4-7", "", 0, 1, 1) is fake

    def test_claude_code_api_key_routes_to_access_token(self):
        """api_key passed in (from oauth_service.resolve) becomes access_token."""
        with patch(
            "tradingagents.llm_adapters.claude_code_adapter.ChatClaudeCodeOAuth",
        ) as ctor:
            _import_target()(
                provider="claude_code",
                model="claude-opus-4-7",
                backend_url="",
                temperature=0.4,
                max_tokens=4000,
                timeout=180,
                api_key="web-supplied-token",
            )
        assert ctor.call_args.kwargs.get("access_token") == "web-supplied-token"

    def test_codex_api_key_routes_to_access_token(self):
        with patch(
            "tradingagents.llm_adapters.codex_adapter.ChatCodexOAuth",
        ) as ctor:
            _import_target()(
                provider="codex",
                model="gpt-5",
                backend_url="",
                temperature=0.5,
                max_tokens=2000,
                timeout=180,
                api_key="web-cx-token",
            )
        assert ctor.call_args.kwargs.get("access_token") == "web-cx-token"


class TestNativeBranchesReduced:
    """Verify dashscope/qianfan/zhipu/siliconflow no longer have native branches.

    Each should construct via the generic OpenAI-compatible fallback.
    """
    def test_dashscope_uses_generic_fallback(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
        with patch("tradingagents.graph.trading_graph.ChatOpenAI") as ctor:
            _import_target()(
                "dashscope", "qwen-test",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                0.1, 100, 10,
            )
        ctor.assert_called_once()
        kwargs = ctor.call_args.kwargs
        assert kwargs["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_qianfan_uses_generic_fallback(self, monkeypatch):
        monkeypatch.setenv("QIANFAN_API_KEY", "test")
        with patch("tradingagents.graph.trading_graph.ChatOpenAI") as ctor:
            _import_target()(
                "qianfan", "ernie-test",
                "https://qianfan.baidubce.com/v2",
                0.1, 100, 10,
            )
        kwargs = ctor.call_args.kwargs
        assert kwargs["base_url"] == "https://qianfan.baidubce.com/v2"

    def test_zhipu_uses_generic_fallback(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "test")
        with patch("tradingagents.graph.trading_graph.ChatOpenAI") as ctor:
            _import_target()(
                "zhipu", "glm-test",
                "https://open.bigmodel.cn/api/paas/v4",
                0.1, 100, 10,
            )
        kwargs = ctor.call_args.kwargs
        assert kwargs["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
