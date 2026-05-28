"""FinancialSituationMemory must refuse to embed for OAuth-subscription
providers and require an explicit EMBEDDING_PROVIDER env var. Source the
list of OAuth providers from the shared constant, not hardcoded literals."""

import pytest

from tradingagents.agents.utils.memory import (
    FinancialSituationMemory,
    UnsupportedEmbeddingError,
)


def test_codex_without_embedding_provider_raises(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    with pytest.raises(UnsupportedEmbeddingError):
        FinancialSituationMemory("test", {"llm_provider": "codex", "memory_enabled": False})


def test_claude_code_without_embedding_provider_raises(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    with pytest.raises(UnsupportedEmbeddingError):
        FinancialSituationMemory("test", {"llm_provider": "claude_code", "memory_enabled": False})


def test_uppercase_codex_also_blocked(monkeypatch):
    """The check normalizes to lowercase — guard the lowercase comparison."""
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    with pytest.raises(UnsupportedEmbeddingError):
        FinancialSituationMemory("test", {"llm_provider": "CODEX"})


def test_api_key_provider_does_not_raise_oauth_error(monkeypatch):
    """openai should NOT trigger the OAuth-embedding block. (May raise other
    errors downstream during embedding-client setup if no key — that's fine,
    we only assert it's not UnsupportedEmbeddingError from the oauth gate.)"""
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    try:
        FinancialSituationMemory("test", {"llm_provider": "openai"})
    except UnsupportedEmbeddingError:
        pytest.fail("openai is api_key, must not hit OAuth embedding gate")
    except Exception:
        pass  # other downstream errors are out of scope for this test
