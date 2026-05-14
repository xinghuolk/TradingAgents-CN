# LLM Adapters for TradingAgents
from .dashscope_openai_adapter import ChatDashScopeOpenAI
from .google_openai_adapter import ChatGoogleOpenAI

# NOTE: ChatClaudeCodeOAuth and ChatCodexOAuth are intentionally NOT re-exported
# at package level. Importing them eagerly here would force every consumer of
# tradingagents.llm_adapters to load the `anthropic` SDK and a Codex-side
# OpenAI client, even when those providers are not in use. Reach them via their
# submodule paths instead:
#
#   from tradingagents.llm_adapters.claude_code_adapter import ChatClaudeCodeOAuth
#   from tradingagents.llm_adapters.codex_adapter import ChatCodexOAuth
#
# `create_llm_by_provider` in tradingagents.graph.trading_graph imports them
# lazily inside the relevant provider branches, which is the supported path.

__all__ = ["ChatDashScopeOpenAI", "ChatGoogleOpenAI"]
