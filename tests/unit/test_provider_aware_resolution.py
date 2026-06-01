"""Provider-aware 模型解析:两级回退(precise → model_name → default)。"""
from app.services.simple_analysis_service import _match_llm_config


def _cfgs():
    return [
        {"model_name": "gpt-4o", "provider": "openai", "api_base": "https://api.openai.com/v1"},
        {"model_name": "gpt-4o", "provider": "openrouter", "api_base": "https://openrouter.ai/api/v1"},
        {"model_name": "qwen-turbo", "provider": "dashscope", "api_base": None},
    ]


def test_precise_match_picks_correct_provider():
    cfg = _match_llm_config(_cfgs(), "gpt-4o", "openrouter")
    assert cfg is not None and cfg["provider"] == "openrouter"


def test_precise_match_case_insensitive():
    cfg = _match_llm_config(_cfgs(), "gpt-4o", "OpenRouter")
    assert cfg is not None and cfg["provider"] == "openrouter"


def test_provider_none_falls_back_to_first_model_name_match():
    cfg = _match_llm_config(_cfgs(), "gpt-4o", None)
    assert cfg is not None and cfg["provider"] == "openai"  # 首个匹配


def test_precise_miss_falls_back_to_model_name_not_default():
    # provider 给了但该 (provider, model) 不存在 → 回退 model_name 匹配,而不是返回 None
    cfg = _match_llm_config(_cfgs(), "gpt-4o", "ghost-provider")
    assert cfg is not None and cfg["provider"] == "openai"  # 永不劣化


def test_no_match_returns_none():
    assert _match_llm_config(_cfgs(), "no-such-model", "openai") is None


def test_recommend_models_with_providers_returns_four_tuple():
    """recommend_models_with_providers 必须返回 4 元组 (q_model, q_provider, d_model, d_provider)。"""
    from app.services.model_capability_service import get_model_capability_service
    svc = get_model_capability_service()
    result = svc.recommend_models_with_providers("标准")
    assert isinstance(result, tuple) and len(result) == 4
    q_model, q_provider, d_model, d_provider = result
    # 模型名应为字符串(或在无配置时来自默认);provider 可能为 None
    assert q_model is None or isinstance(q_model, str)
    assert d_model is None or isinstance(d_model, str)


# ---------------------------------------------------------------------------
# get_provider_by_model_name(async):provider 提示需校验配对,防 stale hint
# ---------------------------------------------------------------------------

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import app.services.simple_analysis_service as sas


def _fake_system_config():
    """两家都有 gpt-4o,外加 qwen-turbo。"""
    return SimpleNamespace(llm_configs=[
        SimpleNamespace(model_name="gpt-4o", provider="openai"),
        SimpleNamespace(model_name="gpt-4o", provider="openrouter"),
        SimpleNamespace(model_name="qwen-turbo", provider="dashscope"),
    ])


def _run_get_provider(model_name, provider):
    async def _go():
        async def _fake_get_system_config():
            return _fake_system_config()
        with patch.object(sas.config_service, "get_system_config", _fake_get_system_config):
            return await sas.get_provider_by_model_name(model_name, provider)
    return asyncio.run(_go())


def test_async_valid_provider_hint_is_used():
    # (openrouter, gpt-4o) 成对存在 → 直接采用
    assert _run_get_provider("gpt-4o", "openrouter") == "openrouter"


def test_async_stale_provider_hint_falls_back_to_model_name():
    # (ghost, gpt-4o) 配对不存在 → 不短路,回退首个 model_name 匹配(openai)
    assert _run_get_provider("gpt-4o", "ghost") == "openai"


def test_async_no_hint_falls_back_to_first_match():
    assert _run_get_provider("gpt-4o", None) == "openai"
