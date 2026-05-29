"""The shared OAuth-subscription-provider constant — single source of truth
for non-DB code paths (JSON fallback in config_bridge, Apache-side memory.py).
DB-backed code paths SHOULD prefer reading LLMProvider.auth_kind directly."""

from tradingagents.utils.oauth_providers import OAUTH_SUBSCRIPTION_PROVIDER_NAMES


def test_constant_is_immutable_frozenset():
    assert isinstance(OAUTH_SUBSCRIPTION_PROVIDER_NAMES, frozenset)


def test_contains_exactly_codex_and_claude_code():
    assert OAUTH_SUBSCRIPTION_PROVIDER_NAMES == frozenset({"codex", "claude_code"})


def test_membership_check_works():
    assert "codex" in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
    assert "claude_code" in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
    assert "openai" not in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
    assert "dashscope" not in OAUTH_SUBSCRIPTION_PROVIDER_NAMES
