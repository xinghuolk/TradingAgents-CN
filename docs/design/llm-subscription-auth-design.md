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

### PR-1：核心适配器与凭据模块（不动 UI 和数据库）
- 新增 `tradingagents/llm_adapters/subscription_credentials.py`
- 新增 `tradingagents/llm_adapters/{anthropic_oauth_adapter, codex_oauth_adapter}.py`
- `create_llm_by_provider` 加 `claude_code` / `codex` 分支
- 单测：mock 本机凭据文件 → 验证 token 刷新、过期判断、header 拼装
- 端到端：通过 CLI（`python -m cli.main`）能跑通一次 Claude Code 订阅模式的分析
- 工作量：~ 2 人日

### PR-2：后端 API + config_bridge
- `app/models/config.py` 加 `AuthType` 枚举与字段
- `app/services/config_service.py` 处理新字段
- `app/core/config_bridge.py` 区分 OAuth provider
- 新增 `app/routers/subscription_auth.py` 的 probe/refresh 端点
- `tradingagents/agents/utils/memory.py` 在订阅模式禁用 / 切换 embedding
- 单测 + Docker 模式的部署文档
- 工作量：~ 2 人日

### PR-3：前端 UI
- 大模型厂家管理页新增「订阅模式」分组
- 凭据检测/刷新组件
- 第一次启用弹合规免责声明
- 工作量：~ 2 人日

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
