"""Unit tests for the claude_code / codex branches in create_llm_by_provider."""
import sys
import types
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Stub out heavy optional dependencies that are not installed in the test
# environment so that importing trading_graph does not fail at collection time.
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


if "dashscope" not in sys.modules:
    ds = _make_stub("dashscope")
    ds.TextEmbedding = MagicMock()  # type: ignore[attr-defined]

if "chromadb" not in sys.modules:
    _make_stub("chromadb")

if "chromadb.config" not in sys.modules:
    cc = _make_stub("chromadb.config")
    cc.Settings = MagicMock()  # type: ignore[attr-defined]

from tradingagents.graph.trading_graph import create_llm_by_provider  # noqa: E402


class TestCreateLlmByProviderSubscription:
    def test_claude_code_branch_returns_oauth_adapter(self):
        fake = MagicMock(name="ChatClaudeCodeOAuth-instance")
        with patch(
            "tradingagents.llm_adapters.claude_code_adapter.ChatClaudeCodeOAuth",
            return_value=fake,
        ) as ctor:
            llm = create_llm_by_provider(
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
            llm = create_llm_by_provider(
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
            assert create_llm_by_provider("Claude_Code", "claude-opus-4-7", "", 0, 1, 1) is fake
            assert create_llm_by_provider("CLAUDE_CODE", "claude-opus-4-7", "", 0, 1, 1) is fake
