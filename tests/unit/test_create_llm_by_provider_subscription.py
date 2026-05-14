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
