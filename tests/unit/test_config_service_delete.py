"""delete_llm_config used `llm.provider.value` (legacy enum access). After
the enum→string migration, `provider` is a plain str, so .value raises
AttributeError, the outer try/except swallows it, and the router reports
'大模型配置不存在' for a config GET clearly returns. Guard against the
regression by exercising the str-provider path."""

import pytest

from app.models.config import LLMConfig, SystemConfig
from app.services.config_service import ConfigService


@pytest.mark.asyncio
async def test_delete_llm_config_matches_string_provider(monkeypatch):
    cfg = SystemConfig(
        config_name="default",
        config_type="system",
        llm_configs=[
            LLMConfig(provider="ollama", model_name="qwen3-8b", enabled=True),
            LLMConfig(provider="openai", model_name="gpt-4", enabled=True),
        ],
    )
    service = ConfigService.__new__(ConfigService)  # bypass __init__ DB connect

    async def fake_get_system_config():
        return cfg

    async def fake_save_system_config(updated):
        # Capture what was passed; assert post-delete state
        assert len(updated.llm_configs) == 1
        assert updated.llm_configs[0].model_name == "gpt-4"
        return True

    monkeypatch.setattr(service, "get_system_config", fake_get_system_config)
    monkeypatch.setattr(service, "save_system_config", fake_save_system_config)

    result = await service.delete_llm_config("ollama", "qwen3-8b")
    assert result is True


@pytest.mark.asyncio
async def test_delete_llm_config_returns_false_when_not_found(monkeypatch):
    cfg = SystemConfig(
        config_name="default",
        config_type="system",
        llm_configs=[LLMConfig(provider="openai", model_name="gpt-4", enabled=True)],
    )
    service = ConfigService.__new__(ConfigService)

    async def fake_get_system_config():
        return cfg

    async def fake_save_system_config(_):
        raise AssertionError("should not save when nothing matched")

    monkeypatch.setattr(service, "get_system_config", fake_get_system_config)
    monkeypatch.setattr(service, "save_system_config", fake_save_system_config)

    result = await service.delete_llm_config("nonexistent", "x")
    assert result is False
