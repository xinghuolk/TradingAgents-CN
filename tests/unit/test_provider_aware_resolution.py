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
