# 大模型订阅鉴权（Codex / Claude Code）接入设计

> 状态：分析与方案设计 · 未实施
> 日期：2026-05-14
> 作者：Claude Code 协助
> 参考实现：[`hermes-agent`](https://github.com/) （仅供调研参考，许可证与实现细节请独立确认）

## 1. 背景与目标

### 1.1 现状
TradingAgents-CN 目前**仅支持 API Key 鉴权**调用大模型。所有 provider（OpenAI、Anthropic、Google、阿里百炼、DeepSeek、智谱、千帆、自定义聚合器等）都通过下述方式认证：

- 用户在 Web UI 配置 `api_key` / `api_base`，由 `app/services/config_service.py` 写入 MongoDB
- 启动期 `app/core/config_bridge.py::bridge_config_to_env()` 把这些值投射到环境变量
- `tradingagents/graph/trading_graph.py::create_llm_by_provider` 与 `__init__` 里的 provider 分支用 `ChatOpenAI(api_key=..., base_url=...)` / `ChatAnthropic(...)` 完成 Bearer 认证

证据：
- `app/models/config.py:42-67`（`LLMProvider` 模型，字段仅 `api_key`、`api_secret`、`default_base_url`）
- `app/models/config.py:186-235`（`LLMConfig`：`provider` + `model_name` + `api_key` + `api_base`）
- `tradingagents/llm_adapters/openai_compatible_base.py:114-138`（仅传 `api_key`+`base_url` 给父类，无自定义 header 注入口）
- `tradingagents/graph/trading_graph.py:41-190`（`create_llm_by_provider` 各 provider 分支只走 API Key）

### 1.2 目标
让用户可以**用已经购买的 Claude Pro/Max 或 ChatGPT Plus/Pro 订阅**直接驱动 TradingAgents 的多智能体分析流，不再额外购买 API 信用：

- **Claude Code 订阅**：读取本机 `~/.claude/.credentials.json`（或 macOS Keychain `Claude Code-credentials` 条目）中的 OAuth access token，按 Anthropic Messages API 的 OAuth 协议调用。
- **Codex（ChatGPT）订阅**：读取本机 `~/.codex/auth.json`（Codex CLI 已登录后写入）的 OAuth token，按 `https://chatgpt.com/backend-api/codex` 端点调用。

### 1.3 非目标
- 不在服务器侧为用户托管订阅 token（合规与安全风险，且违反订阅条款）
- 不实现 Claude Code / Codex CLI 的完整 OAuth Device Code 授权流程（让用户先用官方 CLI 登录一次即可）
- 不重写已有 API Key 流程，订阅模式作为「第三种 provider 类别」并存

## 2. hermes-agent 是怎么做的

`hermes-agent` 把订阅鉴权抽象成「OAuth provider」类别，与 API Key provider 并列，关键设计如下。

### 2.1 Provider 注册表（`hermes_cli/auth.py:132-272`）

```python
@dataclass
class ProviderConfig:
    id: str                  # "openai-codex" / "anthropic"
    name: str
    auth_type: str           # "oauth_device_code" / "oauth_external" / "api_key"
    portal_base_url: str = ""
    inference_base_url: str = ""
    client_id: str = ""
    api_key_env_vars: Tuple[str, ...] = ()

PROVIDER_REGISTRY: Dict[str, ProviderConfig] = {
    "openai-codex": ProviderConfig(
        id="openai-codex",
        auth_type="oauth_external",
        inference_base_url="https://chatgpt.com/backend-api/codex",
        client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    ),
    "anthropic": ProviderConfig(
        id="anthropic",
        auth_type="oauth_external",
        api_key_env_vars=("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
    ),
}
```

`auth_type` 字段是分流的钥匙——一个 provider 是 API Key 还是 OAuth 由它决定，下游的所有逻辑都用它做 if/else。

### 2.2 凭据来源（多级回退 + 自动刷新）

**Claude Code**（`agent/anthropic_adapter.py:717-868`）：

```python
def read_claude_code_credentials() -> Optional[Dict[str, Any]]:
    # 1. macOS Keychain
    kc_creds = _read_claude_code_credentials_from_keychain()
    if kc_creds:
        return kc_creds
    # 2. ~/.claude/.credentials.json
    cred_path = Path.home() / ".claude" / ".credentials.json"
    ...

def is_claude_code_token_valid(creds: Dict[str, Any]) -> bool:
    expires_at = creds.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)
    return now_ms < (expires_at - 60_000)   # 留 60s 缓冲

def refresh_anthropic_oauth_pure(refresh_token: str, ...) -> Dict[str, Any]:
    token_endpoints = [
        "https://platform.claude.com/v1/oauth/token",
        "https://console.anthropic.com/v1/oauth/token",
    ]
    ...
```

**Codex**（`hermes_cli/auth.py:2416-2681`）：

```python
def resolve_codex_runtime_credentials(
    force_refresh: bool = False,
    refresh_if_expiring: bool = True,
    refresh_skew_seconds: int = 120,   # 提前 2 分钟刷新
) -> Dict[str, Any]:
    should_refresh = _codex_access_token_is_expiring(access_token, 120)
    if should_refresh:
        tokens = _refresh_codex_auth_tokens(tokens, refresh_timeout_seconds)
    ...
```

### 2.3 鉴权头差异

订阅 token 不能简单当成 API Key 拼成 `Authorization: Bearer ...`——服务端会用一组 beta header 区分客户端身份。

**Anthropic OAuth**（`agent/anthropic_adapter.py:604-621`）：

```python
elif _is_oauth_token(api_key):
    kwargs["auth_token"] = api_key    # 用 auth_token 而不是 api_key
    kwargs["default_headers"] = {
        "anthropic-beta": ",".join([
            "claude-code-20250219",
            "oauth-2025-04-20",
            "interleaved-thinking-2025-05-14",
            "fine-grained-tool-streaming-2025-05-14",
        ]),
        "user-agent": f"claude-cli/{_get_claude_code_version()}",
        "x-app": "cli",
    }
```

**Token 形式检测**（`agent/anthropic_adapter.py:325-350`）：

```python
def _is_oauth_token(key: str) -> bool:
    if key.startswith("sk-ant-api"):  return False  # 普通 API Key
    if key.startswith("sk-ant-"):     return True   # OAuth-managed
    if key.startswith("eyJ"):         return True   # JWT
    if key.startswith("cc-"):         return True   # Claude Code OAuth
```

**Codex**：`Authorization: Bearer <access_token>` + 端点切到 `https://chatgpt.com/backend-api/codex`，无特殊 header。

### 2.4 配置声明（`cli-config.yaml.example:8-42`）

```yaml
model:
  provider: "auto"        # 或 "openai-codex" / "anthropic" / "custom"

  # 订阅模式时不需要写 api_key（从本机凭据读）
  # provider: "openai-codex"
  # provider: "anthropic"

  # API Key 模式时显式写
  # provider: "custom"
  # api_key: "sk-ant-api-..."
  # base_url: "https://api.anthropic.com/v1"
```

### 2.5 关键设计判断
1. **OAuth provider 是「头等公民」，不是 API Key 的特例**——单独 enum、单独 auth flow、单独 header 处理。
2. **凭据不进项目数据库**，直接读本机 CLI 已经登录好的文件 / Keychain，自动跟随官方 CLI 的刷新节奏。
3. **token 过期前主动刷新**（提前 60–120s），调用方不感知。
4. **beta header 是必需的**——Anthropic OAuth 缺了 `oauth-2025-04-20` 会被拒。

## 3. 与 TradingAgents-CN 的差距

| 维度 | 当前实现 | 需要补的能力 |
|------|---------|-------------|
| Provider 枚举 | `openai/anthropic/google/dashscope/...`（约 10 个）| 新增 `claude_code` / `codex` 两个 OAuth provider |
| 凭据字段 | `api_key` + `api_secret` | `auth_type` + `oauth_credential_source`（path/keychain）+ 缓存的 `access_token`/`refresh_token`/`expires_at` |
| 适配器 | `ChatOpenAI(api_key=..., base_url=...)` 一条路径 | 能注入 `default_headers` + 走 `auth_token` 模式的子类 |
| Token 生命周期 | 不存在 | 启动期读取 + 调用前过期检查 + 刷新 + 失败回退 |
| Web UI | 厂家管理页只有 "Key + Base URL" 表单 | 多一种「订阅登录态」卡片，展示来源/有效期/刷新按钮 |
| config_bridge | 把 `api_key` 投到环境变量 | 区分 OAuth provider，把刷新好的 access_token + headers 透传到 graph |
| ChromaDB embedding | 用 LLM provider 的 API Key 复用 | 订阅模式无法做 embedding，必须强制 fallback 到独立 embedding provider |

## 4. 落地方案

整体思路：**复用现有「DB 配置 → config_bridge → tradingagents 核心」三段式架构**，只在每段加一条订阅分支。前端、后端、核心三层都改动很轻。

### 4.1 数据模型扩展

文件：`app/models/config.py`

```python
class AuthType(str, Enum):
    API_KEY = "api_key"
    OAUTH_SUBSCRIPTION = "oauth_subscription"   # 新增

class OAuthCredentialSource(str, Enum):
    CLAUDE_CODE_KEYCHAIN = "claude_code_keychain"    # macOS only
    CLAUDE_CODE_FILE = "claude_code_file"            # ~/.claude/.credentials.json
    CODEX_FILE = "codex_file"                        # ~/.codex/auth.json
    ENV_VAR = "env_var"                              # CLAUDE_CODE_OAUTH_TOKEN 等
    MANUAL = "manual"                                # 用户粘贴

class LLMProvider(BaseModel):
    # ... 已有字段 ...
    auth_type: AuthType = AuthType.API_KEY                            # 新增
    oauth_credential_source: Optional[OAuthCredentialSource] = None   # 新增
    oauth_endpoint: Optional[str] = None  # eg. https://chatgpt.com/backend-api/codex

class LLMConfig(BaseModel):
    # ... 已有字段 ...
    auth_type: AuthType = AuthType.API_KEY     # 新增
    # 注意：access_token/refresh_token 不入库，只在内存/Redis 短期缓存
```

**关键原则**：刷新好的 access_token **不持久化到 MongoDB**，避免泄露面扩大。只用 Redis 短期缓存（TTL = 比 token 自身有效期更短）。

### 4.2 凭据读取与刷新模块（新建）

文件：`tradingagents/llm_adapters/subscription_credentials.py`（新增）

```python
"""订阅式鉴权凭据读取与刷新。

设计原则：
1. 只读本机 Claude Code / Codex CLI 已登录的凭据，不实现 OAuth 授权流。
2. token 接近过期（默认 120s 缓冲）时主动刷新。
3. 失败时抛出可识别异常，让上层提示用户「请先用 claude login / codex login」。
"""

import json, time, subprocess, os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

@dataclass
class SubscriptionCredential:
    access_token: str
    refresh_token: Optional[str]
    expires_at_ms: int   # epoch ms
    provider: str        # "claude_code" / "codex"
    raw: dict            # 用于诊断

def read_claude_code() -> Optional[SubscriptionCredential]:
    # 1. macOS Keychain
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output([
                "security", "find-generic-password",
                "-s", "Claude Code-credentials", "-w"
            ], timeout=5).decode().strip()
            raw = json.loads(out)
            return _parse_claude_code(raw)
        except Exception:
            pass
    # 2. ~/.claude/.credentials.json
    p = Path.home() / ".claude" / ".credentials.json"
    if p.exists():
        return _parse_claude_code(json.loads(p.read_text()))
    return None

def read_codex() -> Optional[SubscriptionCredential]:
    p = Path.home() / ".codex" / "auth.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text())
    return _parse_codex(raw)

def is_expiring(cred: SubscriptionCredential, skew_seconds: int = 120) -> bool:
    return int(time.time() * 1000) >= (cred.expires_at_ms - skew_seconds * 1000)

def refresh_claude_code(cred: SubscriptionCredential) -> SubscriptionCredential:
    # POST https://platform.claude.com/v1/oauth/token
    # grant_type=refresh_token, refresh_token=..., client_id=...
    ...

def refresh_codex(cred: SubscriptionCredential) -> SubscriptionCredential:
    # POST https://auth.openai.com/oauth/token
    ...

def resolve(provider: str, force_refresh: bool = False) -> SubscriptionCredential:
    """读凭据 + 必要时刷新。被 LLM 适配器的每次请求前调用（已做 Redis 缓存）。"""
    ...
```

### 4.3 LLM 适配器扩展

文件：`tradingagents/llm_adapters/anthropic_oauth_adapter.py`（新增）

```python
"""走 Anthropic OAuth 的 Claude 适配器，复用 langchain_anthropic.ChatAnthropic。

与普通 ChatAnthropic 的差异：
- 用 OAuth access token 而不是 API Key
- 必须带 anthropic-beta + user-agent + x-app header
- token 过期前自动刷新
"""

from langchain_anthropic import ChatAnthropic
from .subscription_credentials import resolve, is_expiring, refresh_claude_code

_OAUTH_BETAS = [
    "oauth-2025-04-20",
    "claude-code-20250219",
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
]

class ChatClaudeCodeOAuth(ChatAnthropic):
    def __init__(self, model, **kwargs):
        cred = resolve("claude_code")
        super().__init__(
            model=model,
            anthropic_api_key=None,                  # 不走 api_key
            default_headers={
                "anthropic-beta": ",".join(_OAUTH_BETAS),
                "user-agent": "claude-cli/1.0 (TradingAgents-CN)",
                "x-app": "cli",
                "Authorization": f"Bearer {cred.access_token}",
            },
            **kwargs,
        )
```

文件：`tradingagents/llm_adapters/codex_oauth_adapter.py`（新增）

```python
"""走 Codex (ChatGPT 订阅) 的适配器，基于 ChatOpenAI 改 base_url。"""

from langchain_openai import ChatOpenAI
from .subscription_credentials import resolve

class ChatCodexOAuth(ChatOpenAI):
    def __init__(self, model, **kwargs):
        cred = resolve("codex")
        super().__init__(
            model=model,
            api_key=cred.access_token,
            base_url="https://chatgpt.com/backend-api/codex",
            **kwargs,
        )
```

> **重要**：`ChatOpenAI`/`ChatAnthropic` 内部缓存 client，token 刷新后必须**重新构造实例**或调用 `client._set_api_key`。简单做法：每次 `TradingAgentsGraph.propagate()` 入口都新建适配器实例（已经是当前调用模式）。

### 4.4 graph 层挂接

文件：`tradingagents/graph/trading_graph.py`，扩展 `create_llm_by_provider`：

```python
def create_llm_by_provider(provider, model, backend_url, temperature, max_tokens, timeout, api_key=None):
    p = provider.lower()

    # ===== 订阅模式分支（放在最前，避免被 fallback 吃掉）=====
    if p == "claude_code":
        from tradingagents.llm_adapters.anthropic_oauth_adapter import ChatClaudeCodeOAuth
        return ChatClaudeCodeOAuth(
            model=model, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
        )
    if p == "codex":
        from tradingagents.llm_adapters.codex_oauth_adapter import ChatCodexOAuth
        return ChatCodexOAuth(
            model=model, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
        )

    # ===== 以下为现有 API Key 分支，不动 =====
    if p == "google":
        ...
```

`TradingAgentsGraph.__init__` 里那一长串 elif 分支也需要新增 `claude_code` / `codex` 入口——但更彻底的做法是**把 `__init__` 里的分支重构为统一调用 `create_llm_by_provider`**（现在已经有一半 provider 通过它走，另一半还在 elif 里硬编码）。

### 4.5 config_bridge 改动

文件：`app/core/config_bridge.py`

订阅模式下**不向环境变量写 api_key**，而是写一个标记位 + 把 provider 名传过去：

```python
def bridge_config_to_env():
    config = await config_service.get_system_config()
    for llm in config.llm_configs:
        if not llm.enabled:
            continue
        if llm.auth_type == AuthType.OAUTH_SUBSCRIPTION:
            # 不投 api_key；订阅适配器在实例化时自己读本机凭据
            os.environ[f"TRADINGAGENTS_{llm.provider.upper()}_AUTH"] = "oauth"
        else:
            os.environ[f"{llm.provider.upper()}_API_KEY"] = llm.api_key
            os.environ[f"{llm.provider.upper()}_API_BASE"] = llm.api_base or ""
```

### 4.6 Web UI 改动

前端（`frontend/src/views/config/` 或 `LLMConfig.vue` 相关组件）：

1. 厂家管理页新增「订阅模式」选项卡，列出：
   - Claude Code（Claude Pro/Max 订阅）
   - Codex（ChatGPT Plus/Pro 订阅）
2. 进入订阅厂家时**不显示** API Key 输入框，改为：
   - 「检测本机凭据」按钮 → 调后端 `GET /api/config/subscription/{provider}/probe`
   - 展示：来源（Keychain / 文件 / 环境变量）、access token 有效期、上次刷新时间
   - 「刷新」按钮 → `POST /api/config/subscription/{provider}/refresh`
   - 凭据未找到时给出可复制的命令：`claude login` / `codex login`
3. 大模型选择页（已存在）允许把订阅 provider 当作普通 provider 选择。

后端新增路由（`app/routers/subscription_auth.py`）：

```python
@router.get("/api/config/subscription/{provider}/probe")
async def probe_subscription(provider: Literal["claude_code", "codex"]):
    """探测本机凭据状态，不刷新、不缓存。"""

@router.post("/api/config/subscription/{provider}/refresh")
async def refresh_subscription(provider: Literal["claude_code", "codex"]):
    """主动触发刷新，返回新的 expires_at。"""
```

⚠️ **部署形态影响**：
- **本地源码部署 / 绿色版**：用户机器上有 `~/.claude/.credentials.json`，直接读。
- **Docker 部署**：容器内**没有**用户的凭据文件，必须：
  - 把 `~/.claude` 和 `~/.codex` 挂进容器（`docker-compose.yml` 里加 volume），或
  - 把 token 通过环境变量 `CLAUDE_CODE_OAUTH_TOKEN` / `CODEX_ACCESS_TOKEN` 传入，或
  - 走 Web UI 粘贴模式（最差的体验，无自动刷新）

  README/部署文档需要明确这一限制。

### 4.7 ChromaDB embedding 兼容

`tradingagents/agents/utils/memory.py` 现在根据 `llm_provider` 决定 embedding 来源。订阅模式下：
- Claude Code 订阅 **不支持** embedding API（Anthropic 没开放）
- Codex 订阅 **不暴露** embedding 端点

方案：订阅模式启动时强制 fallback 到独立 embedding provider：
```python
if config["llm_provider"] in ("claude_code", "codex"):
    # 强制使用独立 embedding（dashscope/openai api_key），或禁用 memory
    embedding_provider = config.get("embedding_provider", "dashscope")
    if not embedding_provider_configured:
        config["memory_enabled"] = False
        logger.warning("订阅模式下未配置独立 embedding，已禁用 memory")
```

## 5. 风险与合规

1. **订阅条款合规**：Anthropic 和 OpenAI 的订阅服务条款均**明确禁止**把订阅用作 API 替代品给程序化批量调用使用。TradingAgents-CN 是「合规友好」定位（README L19）——上线此功能前需要：
   - 在 Web UI 第一次启用订阅模式时弹出免责声明，要求用户确认知晓条款风险
   - 默认限速：≤ 普通 CLI 使用强度（例如每分钟 ≤ 10 次 LLM 调用），并允许用户调整但带警告
   - 不在「演示部署」/ Docker Hub 镜像里默认启用此功能（仅源码版与文档提示）

2. **凭据安全**：
   - access_token 仅写 Redis、不写 MongoDB
   - Redis key 上加用户维度，避免多租户串号（如果项目支持多用户）
   - 日志里 token 必须打码（已有的脱敏函数需要补一条规则）

3. **token 失效的级联**：
   - 刷新失败时返回明确错误（不是 500），引导用户重跑 `claude login`
   - 分析任务运行中 token 过期：现在 graph 的每个节点都新建一次 LLM 是过度的，建议在 graph 启动前**预检** + 在每个节点入口**懒刷新**

4. **官方 CLI 升级风险**：Claude Code / Codex CLI 改了凭据文件格式（如新增字段、改加密）会让本功能瞬间断掉。建议：
   - 凭据解析模块加 schema 版本号校验
   - 解析失败时给出"请升级 hermes-agent / 检查 CLI 版本"提示
   - 在 CI 里加 smoke test（如果能拿到测试凭据）

## 6. 实施路线图

按依赖关系拆三个 PR，每个能独立合并、独立验证：

### PR-1：核心适配器与凭据模块 ✅ **已完成**

**计划范围**：新增 `subscription_credentials.py` + `claude_code_adapter.py` + `codex_adapter.py`，`create_llm_by_provider` 加分支，CLI 端到端验证。

**实际交付**：
- `tradingagents/llm_adapters/subscription_credentials.py`：本机凭据加载（`~/.claude/.credentials.json` + macOS Keychain + `~/.codex/auth.json`）、token 过期判断、refresh（Claude Code + Codex）、`resolve()` 编排
- `tradingagents/llm_adapters/claude_code_adapter.py`：`ChatClaudeCodeOAuth(ChatAnthropic)`，OAuth bearer token + `anthropic-beta` headers + `claude-cli/<dynamic>` user-agent + `x-app: cli`
- `tradingagents/llm_adapters/codex_adapter.py`：`ChatCodexOAuth`（初版，PR-2.5 重写）
- `tradingagents/graph/trading_graph.py`：`create_llm_by_provider` 增加 `claude_code` / `codex` 分支
- `scripts/smoke_test_claude_code_oauth.py`：CLI 端到端 smoke

**实际工作量**：~ 1.5 人日（包括最终评审 + 修复）

**已知 caveats**：
- macOS Keychain 来源的 Claude Code token 不自动刷新（C1 修复：拒绝刷新避免 stale-token 锁死）
- Codex `~/.codex/auth.json` 写回 deferred — refresh 后新 token 只在内存，下次进程启动还读旧的；如需多次复用 CLI 路径需 `codex login` 重做

---

### PR-2：Web OAuth 后端 ✅ **已完成（实际范围扩张）**

**计划范围**：AuthType 枚举、config_service、config_bridge、probe/refresh 路由、memory.py 回退、单测 + Docker 文档。

**实际交付**（远超原计划，包含 OAuth flow 实现 + 多个 PR-1 follow-up）：

**OAuth 基础设施**：
- `app/services/oauth_crypto.py`：AES-256-GCM 加解密 + 启动校验
- `app/services/oauth_service.py`：CRUD（store/resolve/delete）+ PKCE flow（Claude）+ device-code flow（Codex，端点改对：`/api/accounts/deviceauth/usercode`）
- `app/models/oauth.py`：Pydantic 模型 + `OAuthCredentialError`
- `app/routers/oauth.py`：7 个 REST 端点（status / authorize×2 / callback / poll / refresh / unbind）
- `app/core/database.py`：`user_oauth_credentials` 集合 + 唯一索引 `(user_id, provider)`
- `app/core/startup_validator.py`：启动期校验 `OAUTH_ENCRYPTION_KEY`
- `.env.example`：OAUTH_ENCRYPTION_KEY 配置块
- `pyproject.toml`：`cryptography>=42.0` 依赖

**Graph 重构**（Phase 2 范围扩张项）：
- `TradingAgentsGraph.__init__` 从 ~540 行 elif 链收紧到 ~36 行，统一走 `create_llm_by_provider`
- `create_llm_by_provider` 砍掉 dashscope/qianfan/zhipu/custom_openai 专用分支，国产 provider 走通用 OpenAI 兼容 fallback
- 保留原生分支：openai / anthropic / google / deepseek / openrouter / ollama / claude_code / codex

**集成**：
- `app/services/analysis_service.py`：OAuth provider 触发时调 `oauth_service.resolve(user_id, provider)` 注入 token 到 config
- `app/core/config_bridge.py`：跳过 OAuth provider 的 API key 桥接（两处：MongoDB 路径 + JSON fallback 路径）
- `tradingagents/agents/utils/memory.py`：`UnsupportedEmbeddingError` 让 OAuth 模式默认禁用 memory（除非用户设 `EMBEDDING_PROVIDER`）

**PR-1 follow-ups bundled in PR-2**：
- `subscription_credentials.resolve(force_refresh=False)` 参数
- `tests/conftest.py` 加 session 级 `stub_optional_llm_deps` fixture
- `tests/pytest.ini` 加 `asyncio_mode = auto`

**最终评审修复 4 个 Critical bug**：
1. `user["_id"]` → `user["id"]`（auth_db 返回 `id` 不是 `_id`）
2. `get_database()` 用 `db_manager.mongo_db` 而非硬编码字符串（多用户多 DB 场景）
3. `app/main.py` lifespan 加 `init_oauth_redis()`（Redis client 未在 FastAPI 进程初始化）
4. `create_llm_by_provider` 通用 fallback 分支正确传递 `api_key`（之前会丢）

**实际工作量**：~ 6 人日（远超原计划的 2 人日，因为：(a) 选择了 Web OAuth flow 而非纯本机凭据；(b) graph 重构纳入；(c) PR-1 follow-up 一起做；(d) 最终评审 4 个 Critical bug）

**实测验证状态**：
- ✅ Codex Web OAuth flow → MongoDB 加密存 → adapter → 真实 API 200 OK（`scripts/smoke_test_oauth_codex.py`）
- ✅ Codex 完整多智能体分析跑通（`scripts/smoke_test_analysis.py SMOKE_PROVIDER=codex`）
- ✅ DeepSeek 完整多智能体分析跑通（API-key 路径控制组，确认 PR-2 重构没拐错路）
- ⚠️ Claude Code PKCE flow 单测覆盖，未实测（spec § 5.1 标注 "Implementation must verify"）
- ⚠️ DashScope / Zhipu / Qianfan 经通用 fallback 路径，未实测（API 兼容性应该没问题，token 计费追踪可能丢失）
- ⚠️ Token refresh under real Cloudflare：refresh 走 `/oauth/token`，等 ≥1h access_token 过期才能自然触发

**已知 caveats / 留给后续**：
- Codex CLI 路径 `~/.codex/auth.json` 写回仍未做（PR-1 deferred decision）
- macOS Keychain writer 未做（PR-1 C1 deferred）
- 加密 key 轮转工具未做
- 多点部署 token 缓存（Redis L1）未做
- 加密 key 走外部 KMS 未做

---

### PR-2.5：Codex Responses API 适配器重写 ✅ **已完成（计划外发现）**

**触发**：PR-2 端到端验证时发现 `chatgpt.com/backend-api/codex` 用的是 OpenAI Responses API（不是 Chat Completions），消息格式、SSE 事件、tool call 格式、必需 headers 完全不同。原 PR-2 的 `ChatCodexOAuth(ChatOpenAI)` 薄子类调通必挂。

**实际交付**：
- `tradingagents/llm_adapters/codex_responses_adapter.py`：完整 port hermes-agent 的 `_CodexCompletionsAdapter` 模式 —— 把 `responses.stream(...)` 包装成 `chat.completions.create(**kwargs)` 兼容的 shim，让 ChatOpenAI 的现有 LangChain/LangGraph 基础设施继续工作。包含：
  - JWT 解码抽 `chatgpt_account_id`（Cloudflare 必需 header）
  - 消息格式转换（`input_text` / `output_text` shape，role-aware）
  - Tool call 双向转换（`call_*` ↔ `fc_*` 前缀映射）
  - SSE 事件解析（`response.output_text.delta` / `response.output_item.done`）
  - 必需 headers（`originator: codex_cli_rs` + `ChatGPT-Account-ID` + `User-Agent: codex_cli_rs/...`）
  - Sync + async 包装（async 用 `asyncio.to_thread` 转 sync）
  - Reasoning effort 透传（`extra_body.reasoning`）
- `tradingagents/llm_adapters/codex_adapter.py`：重写为通过 shim 接入 ChatOpenAI 的 `self.client` / `self.async_client`（不是 `self.root_client` — 经验证 `langchain_openai >=0.1` 的实际 attribute 形态）
- 41 个新单测覆盖 JWT 解码、消息转换、ID 映射、SSE 解析

**计划外的边角修复**：
- Codex device-code flow 端点从 `/oauth/device/code`（标准 RFC 8628，被 Cloudflare 403）改为 `/api/accounts/deviceauth/usercode`（OpenAI 内部 API + PKCE 混合 3 步流程）
- Verification URL 从合成的 `?user_code=...` 改为 hermes 实测的固定 URL `https://auth.openai.com/codex/device`（用户手动输 code）

**实际工作量**：~ 1 人日

**实测验证状态**：✅ 完整 OAuth flow + adapter + 真实 Codex API 调用 + 多智能体分析全跑通

---

### PR-3：前端 Web UI ✅ **已完成**

**合并状态**：PR #2 已合并到 `main`（merge commit `b6eb87a`，2026-05-14），分支 `feat/pr3-oauth-frontend` 已删除。13 commits，+3218/-11，13 文件。

**实际交付**：
- 「配置管理」新增「订阅授权」子菜单 + 双卡片面板 (Claude Code / Codex)
- `useOAuthStore` (Pinia) 统一管理状态 + flow 编排（PKCE popup + device-code 轮询）
- `ClaudeCodePkceDialog` + `CodexDeviceCodeDialog` 挂在 `App.vue` 作为全局 dialog；store 驱动显示
- `SubscriptionAuthManagement.vue` + `SubscriptionProviderCard.vue`：状态卡（有效期/上次刷新/即将过期）、手动刷新、解绑
- `LLMConfigDialog` 集成：注入 `claude_code` / `codex` 两个合成 provider；选中时隐藏 API Key/Base URL，显示绑定状态 + 「立即授权」/「管理订阅」入口；「管理订阅」会关闭 dialog 并跳到「订阅授权」tab
- `scripts/smoke_test_pr3_ui.md` 人工端到端 checklist

**安全 / 并发**（review 阶段发现并修复）：
- PKCE postMessage 验证 `event.source === pkceDialogPopup`，防止任意 cross-origin 页面伪造 `oauth-success`（spec §9 风险点）
- 两个 flow 在 `await` 前抢占 dialog 标志，关闭 TOCTOU 窗口（快速双击不会泄漏 listener / timer）
- 事件 listener 通过存储的引用精确移除；轮询 timer 在 cancel / 完成 / 过期所有路径都 `clearTimeout`

**显式 out-of-scope（用户决定，本地使用场景）**：
- 首次绑定合规免责声明 modal（spec §1.2 移除；上线生产前再加：补 `consent_acknowledged_at` 字段 + `POST /api/oauth/consent/{provider}` 端点 + 前端 modal）
- LLMConfig per-user 化（保持「LLMConfig 系统级 + OAuth per-user」组合）
- i18n（项目中文优先，硬编码中文）

**预估 vs 实际**：spec 估 ~2.5 人日；通过 subagent-driven development 单次 session 完成（10 个 task，每任务两阶段 review + 修复轮）。

**最终评审**：
- 第一轮：Task 2 / Task 8 各发现 3 个 important issue（re-entry guards、unawaited fetch、dialog-close-on-navigate 等），单独 fix commit 修复
- 第二轮 cross-task review：又发现 2 个 important（postMessage source verification + TOCTOU）+ 2 个 minor（stale model fields），全部修复
- 构建 ✅ pass；type-check 对新增文件零新错（pre-existing baseline 未触及）；lint 因本地 env 缺 peer dep `@rushstack/eslint-patch` 无法跑（与本 PR 无关）
- 手工 smoke checklist 留待运行 dev server + 真实 OAuth credentials 走一遍

## 7. 待定问题

1. 是否允许「同一次分析」混用订阅模式（quick LLM = Claude Code）+ API Key 模式（deep LLM = DeepSeek）？技术上现有「混合模式」（[`trading_graph.py:242-269`](../../tradingagents/graph/trading_graph.py)）已支持不同 provider 组合，扩展到订阅模式无障碍——但要确认产品定位是否需要。
2. 多用户 / SaaS 部署形态下，是否要支持把订阅 token 通过用户设置上传（而不是读容器本机）？涉及到 token 加密入库、用户级别隔离，复杂度显著上升，建议留待 v1.1 之后。
3. 是否要支持其他订阅式入口（Cursor、Windsurf 等）？hermes-agent 还接了一些社区代理（如 Nous Portal），是否纳入由社区驱动。

## 8. 参考资料

- `hermes-agent` 源码（路径见 § 2 各小节）
- Anthropic Messages API OAuth 鉴权：搜 `oauth-2025-04-20` beta header
- Claude Code 凭据格式：`~/.claude/.credentials.json`（结构由官方 CLI 决定，无公开 schema）
- Codex API 端点：`https://chatgpt.com/backend-api/codex`（非公开文档，由 Codex CLI 反推）
- 当前项目相关文件：
  - `tradingagents/graph/trading_graph.py:41-190`（`create_llm_by_provider`）
  - `tradingagents/llm_adapters/openai_compatible_base.py`
  - `app/core/config_bridge.py`
  - `app/models/config.py`
