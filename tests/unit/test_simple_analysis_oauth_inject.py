"""simple_analysis_service (the LIVE web analysis path) must inject the user's
OAuth access_token into config for codex/claude_code providers, otherwise the
core ChatCodexOAuth adapter gets api_key=None and falls back to a nonexistent
~/.codex/auth.json ("No Codex credentials found").

These tests exercise the gating + stamping logic of
_inject_oauth_token_if_needed_sync, with the actual token-resolution I/O
(_resolve_oauth_tokens) monkeypatched out — its loop-local Motor correctness is
validated by a live run, not unit tests.
"""

from app.services.simple_analysis_service import SimpleAnalysisService


def _svc():
    # Bypass __init__ (which builds a thread pool / DB clients).
    return SimpleAnalysisService.__new__(SimpleAnalysisService)


def test_non_oauth_config_is_untouched_and_never_resolves():
    svc = _svc()

    def _boom(*a, **k):
        raise AssertionError("_resolve_oauth_tokens must not be called for non-OAuth")

    svc._resolve_oauth_tokens = _boom
    cfg = {
        "llm_provider": "openai",
        "quick_provider": "openai",
        "deep_provider": "deepseek",
        "quick_api_key": "k1",
        "deep_api_key": "k2",
    }
    svc._inject_oauth_token_if_needed_sync("u1", cfg)
    assert cfg["quick_api_key"] == "k1"
    assert cfg["deep_api_key"] == "k2"


def test_deep_codex_only_stamps_deep_key():
    svc = _svc()
    svc._resolve_oauth_tokens = lambda uid, pbr: {role: f"tok-{prov}" for role, prov in pbr.items()}
    cfg = {
        "llm_provider": "openai",
        "quick_provider": "openai",
        "deep_provider": "codex",
        "quick_api_key": "k1",
        "deep_api_key": None,
    }
    svc._inject_oauth_token_if_needed_sync("u1", cfg)
    assert cfg["quick_api_key"] == "k1"          # untouched
    assert cfg["deep_api_key"] == "tok-codex"    # injected


def test_both_roles_codex_stamps_both():
    svc = _svc()
    svc._resolve_oauth_tokens = lambda uid, pbr: {role: "TOK" for role in pbr}
    cfg = {
        "llm_provider": "codex",
        "quick_provider": "codex",
        "deep_provider": "codex",
        "quick_api_key": None,
        "deep_api_key": None,
    }
    svc._inject_oauth_token_if_needed_sync("u1", cfg)
    assert cfg["quick_api_key"] == "TOK"
    assert cfg["deep_api_key"] == "TOK"


def test_claude_code_quick_role_recognized():
    svc = _svc()
    svc._resolve_oauth_tokens = lambda uid, pbr: {role: f"t-{prov}" for role, prov in pbr.items()}
    cfg = {
        "llm_provider": "deepseek",
        "quick_provider": "claude_code",
        "deep_provider": "deepseek",
        "quick_api_key": None,
        "deep_api_key": "dk",
    }
    svc._inject_oauth_token_if_needed_sync("u1", cfg)
    assert cfg["quick_api_key"] == "t-claude_code"
    assert cfg["deep_api_key"] == "dk"


def test_providers_by_role_falls_back_to_llm_provider():
    """When quick/deep_provider keys are absent, the single llm_provider is used."""
    svc = _svc()
    svc._resolve_oauth_tokens = lambda uid, pbr: {role: "X" for role in pbr}
    cfg = {"llm_provider": "codex", "quick_api_key": None, "deep_api_key": None}
    svc._inject_oauth_token_if_needed_sync("u1", cfg)
    assert cfg["quick_api_key"] == "X"
    assert cfg["deep_api_key"] == "X"
