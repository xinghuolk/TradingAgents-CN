"""Test memory.py's subscription-mode embedding fallback."""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stubs(stub_optional_llm_deps):
    pass


class TestSubscriptionModeEmbeddingFallback:
    def test_claude_code_without_embedding_provider_raises(self, monkeypatch):
        """OAuth provider + no EMBEDDING_PROVIDER → raise UnsupportedEmbeddingError."""
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        from tradingagents.agents.utils.memory import (
            FinancialSituationMemory, UnsupportedEmbeddingError,
        )
        config = {"llm_provider": "claude_code"}
        with pytest.raises(UnsupportedEmbeddingError):
            FinancialSituationMemory("test_mem", config)

    def test_codex_without_embedding_provider_raises(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        from tradingagents.agents.utils.memory import (
            FinancialSituationMemory, UnsupportedEmbeddingError,
        )
        with pytest.raises(UnsupportedEmbeddingError):
            FinancialSituationMemory("test_mem", {"llm_provider": "codex"})

    def test_claude_code_with_embedding_provider_uses_it(self, monkeypatch):
        """Setting EMBEDDING_PROVIDER=dashscope allows memory to construct."""
        monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
        # The construction may still fail for other reasons, but it must NOT
        # raise UnsupportedEmbeddingError.
        from tradingagents.agents.utils.memory import (
            FinancialSituationMemory, UnsupportedEmbeddingError,
        )
        try:
            FinancialSituationMemory("test_mem", {"llm_provider": "claude_code"})
        except UnsupportedEmbeddingError:
            pytest.fail("Should not raise UnsupportedEmbeddingError when EMBEDDING_PROVIDER set")
        except Exception:
            pass  # other init failures (e.g. ChromaDB) are not our concern
