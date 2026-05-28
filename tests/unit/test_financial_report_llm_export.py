import json
from pathlib import Path

from tradingagents.dataflows.financial_reports.llm_config_export import (
    materialize_extractor_llm_config,
)


def _clear_env(monkeypatch):
    for k in (
        "TRADINGAGENTS_DEEP_PROVIDER",
        "TRADINGAGENTS_DEEP_BACKEND_URL",
        "TRADINGAGENTS_DEEP_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)


def test_returns_none_when_provider_or_model_missing(monkeypatch):
    _clear_env(monkeypatch)
    assert materialize_extractor_llm_config() is None
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "deepseek")
    # model still missing
    assert materialize_extractor_llm_config() is None


def test_api_key_provider_writes_full_config(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "deepseek")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "deepseek-chat")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_BACKEND_URL", "https://api.deepseek.com")

    path = materialize_extractor_llm_config(cache_root=str(tmp_path))

    assert path == str(tmp_path / "frle_llm_config.deep.json")
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    assert cfg == {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
    }


def test_api_key_provider_omits_empty_base_url(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "deepseek")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "deepseek-chat")
    # no backend url

    cfg = json.loads(Path(materialize_extractor_llm_config(cache_root=str(tmp_path))).read_text("utf-8"))
    assert "base_url" not in cfg
    assert cfg["api_key_env"] == "DEEPSEEK_API_KEY"


def test_codex_writes_provider_and_model_only(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "codex")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "gpt-5.5-codex")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_BACKEND_URL", "ignored")

    cfg = json.loads(Path(materialize_extractor_llm_config(cache_root=str(tmp_path))).read_text("utf-8"))
    assert cfg == {"provider": "codex", "model": "gpt-5.5-codex"}


def test_unsupported_provider_degrades_to_none(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "anthropic")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "claude-3-5-sonnet")
    assert materialize_extractor_llm_config(cache_root=str(tmp_path)) is None


def test_openai_codex_alias_preserves_provider(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "openai-codex")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "gpt-5.5-codex")

    cfg = json.loads(Path(materialize_extractor_llm_config(cache_root=str(tmp_path))).read_text("utf-8"))
    assert cfg == {"provider": "openai-codex", "model": "gpt-5.5-codex"}


def test_dashscope_native_endpoint_normalized_to_compatible_mode(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "dashscope")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "qwen-max")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_BACKEND_URL", "https://dashscope.aliyuncs.com/api/v1")

    cfg = json.loads(Path(materialize_extractor_llm_config(cache_root=str(tmp_path))).read_text("utf-8"))
    assert cfg["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert cfg["api_key_env"] == "DASHSCOPE_API_KEY"


def test_dashscope_compatible_mode_url_left_unchanged(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "dashscope")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "qwen-max")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_BACKEND_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    cfg = json.loads(Path(materialize_extractor_llm_config(cache_root=str(tmp_path))).read_text("utf-8"))
    assert cfg["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_dashscope_custom_url_left_unchanged(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "dashscope")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "qwen-max")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_BACKEND_URL", "https://my-proxy.example.com/v1")

    cfg = json.loads(Path(materialize_extractor_llm_config(cache_root=str(tmp_path))).read_text("utf-8"))
    assert cfg["base_url"] == "https://my-proxy.example.com/v1"
