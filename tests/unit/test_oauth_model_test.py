"""模型测试逻辑的单元测试。

覆盖 ConfigService.test_llm_config / _test_oauth_model / _ping_via_real_llm
的关键分支，全部 mock，无网络/DB。

重构后所有供应商（OAuth codex + API Key 模型）的「测试」都统一走
create_llm_by_provider(...).invoke(...) 真实调用路径（_ping_via_real_llm）。
claude_code 仍为 token-only，不调用 create_llm_by_provider。
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


# ==================== OAuth 订阅模型 ====================


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
    """codex 已绑定 → 走统一的 _ping_via_real_llm 真实调用并成功"""
    _patch_collection(monkeypatch)

    async def _resolve(collection, user_id, provider):
        return "tok"

    monkeypatch.setattr("app.services.oauth_service.resolve", _resolve)

    class _FakeLLM:
        def invoke(self, prompt):
            return _FakeResp("hello")

    captured = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeLLM()

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.create_llm_by_provider", _factory
    )

    service = _make_service()
    result = await service.test_llm_config(
        LLMConfig(provider="codex", model_name="gpt-5.5-codex", enabled=True),
        user_id="u1",
    )
    # 统一路径：成功消息 + response_preview（与 API Key 模型一致）
    assert result["success"] is True
    assert "codex" in result["message"]
    assert result["details"]["response_preview"] == "hello"
    # 凭据通过 api_key 注入，max_tokens 已统一为 256
    assert captured["api_key"] == "tok"
    assert captured["provider"] == "codex"
    assert captured["max_tokens"] == 256


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
    # 只暴露异常类型，不泄露 token
    assert "RuntimeError" in result["message"]
    assert "tok" not in result["message"]


@pytest.mark.asyncio
async def test_claude_code_bound_token_only(monkeypatch):
    """claude_code 为 token-only：不应调用 create_llm_by_provider"""
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


# ==================== API Key 模型（统一真实调用路径）====================


def _make_service_with_provider(api_key_in_db=None, default_base_url="https://api.example.com/v1"):
    """构造一个 service，其 _get_db 返回带 llm_providers 的假 DB。"""
    service = _make_service()

    class _FakeProviders:
        async def find_one(self, query):
            doc = {}
            if api_key_in_db:
                doc["api_key"] = api_key_in_db
            if default_base_url:
                doc["default_base_url"] = default_base_url
            return doc or None

    class _FakeDB:
        llm_providers = _FakeProviders()

    async def _get_db():
        return _FakeDB()

    service._get_db = _get_db
    service._get_env_api_key = lambda provider_str: None
    service._is_valid_api_key = lambda key: bool(key) and len(key) > 3
    return service


@pytest.mark.asyncio
async def test_apikey_no_key_returns_early(monkeypatch):
    """未配置 API 密钥时应提前返回失败（不调用 LLM）"""
    service = _make_service_with_provider(api_key_in_db=None)

    called = {"factory": False}

    def _factory(**kwargs):
        called["factory"] = True
        raise AssertionError("create_llm_by_provider should not be called")

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.create_llm_by_provider", _factory
    )

    result = await service.test_llm_config(
        LLMConfig(provider="openai", model_name="gpt-4o",
                  api_base="https://api.openai.com/v1", enabled=True),
        user_id=None,
    )
    assert result["success"] is False
    assert "未配置有效的API密钥" in result["message"]
    assert called["factory"] is False


@pytest.mark.asyncio
async def test_apikey_real_invoke_success(monkeypatch):
    """openai（API Key）→ 通过 _ping_via_real_llm 真实调用并成功"""
    service = _make_service_with_provider()

    class _FakeLLM:
        def invoke(self, prompt):
            return _FakeResp("OK")

    captured = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeLLM()

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.create_llm_by_provider", _factory
    )

    result = await service.test_llm_config(
        LLMConfig(provider="openai", model_name="gpt-4o",
                  api_base="https://api.openai.com/v1", api_key="sk-test123", enabled=True),
        user_id=None,
    )
    assert result["success"] is True
    assert result["details"]["response_preview"] == "OK"
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-4o"
    assert captured["api_key"] == "sk-test123"
    assert captured["backend_url"] == "https://api.openai.com/v1"
    assert captured["max_tokens"] == 256


@pytest.mark.asyncio
async def test_apikey_invoke_raises_returns_failure(monkeypatch):
    """API Key 模型调用抛异常时应返回失败信息（不抛出）"""
    service = _make_service_with_provider()

    class _FakeLLM:
        def invoke(self, prompt):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.create_llm_by_provider",
        lambda **kwargs: _FakeLLM(),
    )

    result = await service.test_llm_config(
        LLMConfig(provider="deepseek", model_name="deepseek-chat",
                  api_base="https://api.deepseek.com", api_key="sk-test123", enabled=True),
        user_id=None,
    )
    assert result["success"] is False
    assert "deepseek" in result["message"]
    assert "RuntimeError" in result["message"]


@pytest.mark.asyncio
async def test_apikey_empty_response_returns_failure(monkeypatch):
    """API Key 模型返回空响应时应失败"""
    service = _make_service_with_provider()

    class _FakeLLM:
        def invoke(self, prompt):
            return _FakeResp("")

    monkeypatch.setattr(
        "tradingagents.graph.trading_graph.create_llm_by_provider",
        lambda **kwargs: _FakeLLM(),
    )

    result = await service.test_llm_config(
        LLMConfig(provider="dashscope", model_name="qwen-plus",
                  api_base="https://dashscope.aliyuncs.com", api_key="sk-test123", enabled=True),
        user_id=None,
    )
    assert result["success"] is False
    assert "响应为空" in result["message"]
