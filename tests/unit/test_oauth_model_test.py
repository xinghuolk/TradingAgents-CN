"""OAuth订阅模型(codex/claude_code)真实测试逻辑的单元测试。

覆盖 ConfigService._test_oauth_model 的关键分支，全部mock，无网络/DB。
"""
import pytest

from app.models.config import LLMConfig
from app.services.config_service import ConfigService


def _make_service():
    # 不触发 __init__(会连接DB/读取配置)，直接构造空实例
    return ConfigService.__new__(ConfigService)


def _patch_collection(monkeypatch):
    # get_credentials_collection 返回的对象不会被实际使用(resolve被mock)，
    # 但仍需避免真实Motor连接。
    monkeypatch.setattr(
        "app.routers.oauth.get_credentials_collection",
        lambda: object(),
    )


class _FakeResp:
    def __init__(self, content):
        self.content = content


@pytest.mark.asyncio
async def test_codex_not_bound(monkeypatch):
    _patch_collection(monkeypatch)

    async def _resolve(collection, user_id, provider):
        raise Exception("用户未绑定 codex 订阅")

    monkeypatch.setattr("app.services.oauth_service.resolve", _resolve)

    service = _make_service()
    result = await service.test_llm_config(
        LLMConfig(provider="codex", model_name="gpt-5.5-codex", enabled=True),
        user_id="u1",
    )
    assert result["success"] is False
    assert "未绑定" in result["message"] or "凭据" in result["message"]


@pytest.mark.asyncio
async def test_codex_bound_ping_ok(monkeypatch):
    _patch_collection(monkeypatch)

    async def _resolve(collection, user_id, provider):
        return "tok"

    monkeypatch.setattr("app.services.oauth_service.resolve", _resolve)

    class _FakeLLM:
        def invoke(self, prompt):
            return _FakeResp("hello")

    def _factory(**kwargs):
        assert kwargs["api_key"] == "tok"
        assert kwargs["provider"] == "codex"
        return _FakeLLM()

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.create_llm_by_provider", _factory
    )

    service = _make_service()
    result = await service.test_llm_config(
        LLMConfig(provider="codex", model_name="gpt-5.5-codex", enabled=True),
        user_id="u1",
    )
    assert result["success"] is True
    assert "codex" in result["message"] and "响应" in result["message"]
    assert result["details"]["sample"] == "hello"


@pytest.mark.asyncio
async def test_codex_bound_ping_fails(monkeypatch):
    _patch_collection(monkeypatch)

    async def _resolve(collection, user_id, provider):
        return "tok"

    monkeypatch.setattr("app.services.oauth_service.resolve", _resolve)

    class _FakeLLM:
        def invoke(self, prompt):
            raise RuntimeError("network boom")

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.create_llm_by_provider",
        lambda **kwargs: _FakeLLM(),
    )

    service = _make_service()
    result = await service.test_llm_config(
        LLMConfig(provider="codex", model_name="gpt-5.5-codex", enabled=True),
        user_id="u1",
    )
    assert result["success"] is False
    assert "调用失败" in result["message"]


@pytest.mark.asyncio
async def test_claude_code_bound_token_only(monkeypatch):
    _patch_collection(monkeypatch)

    async def _resolve(collection, user_id, provider):
        return "tok"

    monkeypatch.setattr("app.services.oauth_service.resolve", _resolve)

    called = {"factory": False}

    def _factory(**kwargs):
        called["factory"] = True
        raise AssertionError("create_llm_by_provider should NOT be called for claude_code")

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.create_llm_by_provider", _factory
    )

    service = _make_service()
    result = await service.test_llm_config(
        LLMConfig(provider="claude_code", model_name="claude-sonnet", enabled=True),
        user_id="u1",
    )
    assert result["success"] is True
    assert "Claude Code" in result["message"] and "限制" in result["message"]
    assert called["factory"] is False
    assert result["details"]["verified"] == "token_only"


@pytest.mark.asyncio
async def test_no_user_id(monkeypatch):
    _patch_collection(monkeypatch)

    service = _make_service()
    result = await service.test_llm_config(
        LLMConfig(provider="codex", model_name="gpt-5.5-codex", enabled=True),
        user_id=None,
    )
    assert result["success"] is False
    assert "用户" in result["message"]
