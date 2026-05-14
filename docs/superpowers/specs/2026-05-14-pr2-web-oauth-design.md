# PR-2: Web OAuth 后端设计

> 状态：设计已批准，等待 spec 评审 → 实施计划
> 日期：2026-05-14
> 关联文档：
> - 整体路线图：[`docs/design/llm-subscription-auth-design.md`](../../design/llm-subscription-auth-design.md)
> - PR-1 实施计划：[`docs/superpowers/plans/2026-05-14-subscription-auth-pr1.md`](../plans/2026-05-14-subscription-auth-pr1.md)
> - 参考实现：`hermes-agent/hermes_cli/web_server.py:1640-1807`（Anthropic PKCE）、`hermes-agent/hermes_cli/auth.py:4548-4752`（Codex device code）
> - 实施分支：`feat/codex-claude-subscription-analysis`（PR-1 已合并到该分支）

## 1. 目标

让 TradingAgents-CN 的 Web 用户使用自己的 Claude Pro/Max 或 ChatGPT Plus/Pro 订阅授权进行多智能体分析，**无需 API Key**，**无需在服务器本机预先 `claude login`**，**支持多用户隔离**。

PR-1 实现了 CLI 友好的本机凭据读取路径；PR-2 实现一条独立的 Web 友好的路径：完整的浏览器内 OAuth 授权 + 凭据加密入库 + 多租户隔离。

### 非目标（明确划出 PR-3+）

- Web UI（"登录 Claude"按钮、绑定状态卡片等 — PR-3 负责）
- macOS Keychain 写回（PR-1 C1 defer 保持原状）
- 加密 key 的轮转脚本
- 多点部署 token 缓存（Redis L1）
- 加密 key 走外部 KMS（AWS KMS / Vault）

## 2. 整体架构

```
┌───────────────────────────────────────────────────────────────┐
│  frontend (PR-3 实现，不在 PR-2 范围)                          │
│    "登录 Claude"  → /api/oauth/authorize/anthropic           │
│    "登录 Codex"   → /api/oauth/authorize/codex                │
│    绑定状态卡片   → /api/oauth/status/{provider}             │
├───────────────────────────────────────────────────────────────┤
│  app/routers/oauth.py (新建)                                  │
│    REST 入口，6 个端点（详见 § 6）                            │
├───────────────────────────────────────────────────────────────┤
│  app/services/oauth_service.py (新建)                         │
│    业务编排：PKCE flow 状态机 / device code 轮询 /            │
│    resolve(user, provider) 懒刷新                             │
├───────────────────────────────────────────────────────────────┤
│  app/services/oauth_crypto.py (新建)                          │
│    AES-256-GCM 加解密 + 启动期 key 校验                       │
├───────────────────────────────────────────────────────────────┤
│  app/models/oauth.py (新建)                                   │
│    Pydantic 模型：OAuthCredentialDoc / OAuthBinding /         │
│    OAuthAuthorizeResponse / OAuthStatusResponse 等            │
├───────────────────────────────────────────────────────────────┤
│  MongoDB collection: user_oauth_credentials                   │
│    每个 (user_id, provider) 一条记录，token 加密              │
├───────────────────────────────────────────────────────────────┤
│  app/core/config_bridge.py (修改)                             │
│    分析启动前：若 llm_provider ∈ {claude_code, codex}，       │
│    调 oauth_service.resolve(user_id, provider) 取 token，     │
│    传给 trading_graph 的 quick_api_key / deep_api_key         │
├───────────────────────────────────────────────────────────────┤
│  tradingagents/graph/trading_graph.py (重构 + 收紧)           │
│    create_llm_by_provider 收紧 provider 集合；                │
│    __init__ 完全用 create_llm_by_provider 调用替换 elif 链    │
├───────────────────────────────────────────────────────────────┤
│  tradingagents/agents/utils/memory.py (修改)                  │
│    订阅模式下默认禁用 memory；可选独立 embedding              │
└───────────────────────────────────────────────────────────────┘
```

**关键设计原则**

1. PR-1 的 `subscription_credentials.py` 保持原样，仅服务 CLI / 本地开发场景。Web 后端不依赖它。
2. PR-2 的 `oauth_service.py` 是 Web 后端的独立凭据来源；和 PR-1 模块无相互调用。
3. 凭据**永不**以明文落盘或入库；MongoDB 里只存密文。日志里 token 字段必须脱敏。
4. 单 trades-analysis 进程内，token 懒刷新 + 写回 MongoDB；多进程下，下次请求自动看到新 token。

## 3. 数据模型

### 3.1 MongoDB collection: `user_oauth_credentials`

```javascript
{
  _id: ObjectId,
  user_id: "...",                       // 引用 users._id (JWT sub)
  provider: "claude_code" | "codex",    // 与 LLM provider 名保持一致 (PR-1 既定)
  ciphertext: BinData,                  // AES-256-GCM 密文
  nonce: BinData(12),                   // 96-bit nonce, 每次加密重新生成
  tag: BinData(16),                     // GCM 认证标签
  access_token_expires_at: ISODate,     // 仅 expires_at 明文，便于查询
  refresh_token_present: bool,          // 用于诊断与统计，明文
  created_at: ISODate,
  last_refresh_at: ISODate,
  last_used_at: ISODate                 // 用于"30 天未用 token 清理"任务
}
```

**provider 命名约定**：整个 PR-2 都用 `claude_code` 和 `codex` 作为 provider 名（和 PR-1 一致）。MongoDB doc、API 端点路径、Redis key、`oauth_service.resolve(provider=...)` 全部统一。授权目标服务的真名是 Anthropic / OpenAI，但**不**作为 provider 标识符出现，仅在外发 HTTP 请求里出现。

**索引**

```javascript
db.user_oauth_credentials.createIndex(
  { user_id: 1, provider: 1 },
  { unique: true, name: "uniq_user_provider" }
)
db.user_oauth_credentials.createIndex(
  { access_token_expires_at: 1 },
  { name: "expiry_scan" }
)
```

`ciphertext` 解密后是 JSON：

```json
{ "access_token": "...", "refresh_token": "..." }
```

设计选择：`refresh_token` 与 `access_token` 一起加密入同一 ciphertext，避免两个密文带来的同步问题。

### 3.2 临时 state 存储

OAuth flow 需要 state token（PKCE 的 state + code_verifier、device code 的 device_code）。这些**不入库**，使用 Redis：

```
Redis key (PKCE):       oauth:state:claude_code:<state>
Redis value (PKCE):     JSON { user_id, code_verifier, created_at }
TTL: 600s (10 minutes)

Redis key (device):     oauth:device:<user_id>:codex
Redis value (device):   JSON { device_code, interval, expires_at }
TTL: device flow expires_in
```

PKCE 的 Redis key 不包含 user_id（因为回调时只有 state 可查；user_id 从 value 里取出来）；device code 的 Redis key 包含 user_id（轮询请求带 JWT，自然拿得到）。

理由：state 短命，无需持久化；多进程下 Redis 提供一致视图。

## 4. 加密层 (`app/services/oauth_crypto.py`)

### 4.1 接口

```python
def encrypt_token_payload(payload: dict) -> tuple[bytes, bytes, bytes]:
    """加密 token JSON dict，返回 (ciphertext, nonce, tag)。"""

def decrypt_token_payload(ciphertext: bytes, nonce: bytes, tag: bytes) -> dict:
    """解密；GCM 认证失败时抛 OAuthCryptoError。"""

def validate_encryption_key_at_startup() -> None:
    """检查 OAUTH_ENCRYPTION_KEY 环境变量；fail-fast 在 startup_validator 调用。"""
```

### 4.2 算法选择

- **AES-256-GCM**，使用 `cryptography` 库
- 密钥来源：环境变量 `OAUTH_ENCRYPTION_KEY`，base64 编码的 32 字节
- 每次加密生成新的 12 字节 nonce（GCM 推荐长度）
- 16 字节 GCM 认证标签

### 4.3 启动校验

`app/core/startup_validator.py` 已存在；在其中扩展，启动期：
- 若 `OAUTH_ENCRYPTION_KEY` 未设置且 `user_oauth_credentials` 集合存在记录 → **fail-fast**（"加密 key 缺失，无法解密已有 token"）
- 若 `OAUTH_ENCRYPTION_KEY` 未设置且集合为空 → 仅警告（功能未启用）
- 若 `OAUTH_ENCRYPTION_KEY` 长度错 → fail-fast（"key 必须是 base64 32 字节"）

### 4.4 密钥生成指引

`.env.example` 加入：

```bash
# OAuth 订阅鉴权加密 key（用于加密 MongoDB 中的 OAuth token）
# 生成命令：python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
OAUTH_ENCRYPTION_KEY=
```

## 5. OAuth Flow 细节

### 5.1 Anthropic（PKCE）

参考 hermes：`hermes-agent/hermes_cli/web_server.py:1640-1807`。

> **实施时需要验证**：authorize URL 主机（`claude.ai/oauth/authorize` vs `console.anthropic.com/oauth/authorize`）和 scope 列表的确切值。下面以 hermes 当前观察到的为准；实施时第一件事是跑通一次手动 PKCE flow 验证常量。

**步骤**

1. **客户端**：GET `/api/oauth/authorize/claude_code`
2. **后端**：
   - 生成 `code_verifier`（43–128 字符随机）和 `code_challenge`（SHA-256 of verifier, base64url）
   - 生成 `state`（32 字节 base64url）
   - 写 Redis: `oauth:state:claude_code:<state>` → `{ user_id, code_verifier, created_at }` TTL 600s
   - 构造授权 URL：
     ```
     https://claude.ai/oauth/authorize
       ?response_type=code
       &client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e
       &redirect_uri=<推导>
       &scope=org:create_api_key user:profile user:inference
       &state=<state>
       &code_challenge=<code_challenge>
       &code_challenge_method=S256
     ```
   - **重要**：redirect_uri 从请求 `Host`/`X-Forwarded-Host` 头推导，形如 `https://<host>/api/oauth/callback/claude_code`。一致性：授权请求里 URL 必须和回调验证里的 URL 完全相同
   - 响应：`{ "authorize_url": "...", "state": "..." }`，UI 跳转到该 URL
3. **用户**：在 `claude.ai` 完成授权，被重定向到 `<host>/api/oauth/callback/claude_code?code=...&state=...`
4. **后端**：GET `/api/oauth/callback/claude_code`
   - 从 Redis 取 `{ user_id, code_verifier }`（key 不存在 → 错误 "state expired or invalid"）
   - POST `https://platform.claude.com/v1/oauth/token`：
     ```
     grant_type=authorization_code
     code=<code>
     redirect_uri=<同 step 2>
     client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e
     code_verifier=<from Redis>
     ```
   - 解析响应 `{ access_token, refresh_token, expires_in }`
   - 加密 token，upsert 到 `user_oauth_credentials`，doc 的 user_id 来自 Redis value 中
   - 删除 Redis state
   - 响应：HTML 关闭弹窗（含 `window.opener.postMessage('oauth-success', '*')`）。PR-3 前端用 `window.open` + message 接收方式驱动。

### 5.2 Codex（device code）

参考 hermes：`hermes-agent/hermes_cli/auth.py:4548-4752`。

**步骤**

1. **客户端**：POST `/api/oauth/authorize/codex`
2. **后端**：
   - POST `https://auth.openai.com/oauth/device/code`：
     ```
     client_id=app_EMoamEEZ73f0CkXaXp7hrann
     scope=openid profile email
     ```
   - 解析响应：`{ device_code, user_code, verification_uri_complete, expires_in, interval }`
   - 写 Redis: `oauth:device:<user_id>:codex` → `{ device_code, interval, expires_at }` TTL = expires_in
   - 响应：`{ user_code: "ABCD-EFGH", verification_uri: "...", expires_in: 600, interval: 5 }`
   - UI 显示 user_code + 跳转链接，让用户在 ChatGPT 网站输入
3. **客户端**：每 `interval` 秒轮询 POST `/api/oauth/poll/codex`
4. **后端**：
   - 从 Redis 取 device_code
   - POST `https://auth.openai.com/oauth/token`：
     ```
     grant_type=urn:ietf:params:oauth:grant-type:device_code
     device_code=<from Redis>
     client_id=app_EMoamEEZ73f0CkXaXp7hrann
     ```
   - 响应：
     - `authorization_pending`：返回 `{ status: "pending" }`，UI 继续轮询
     - `slow_down`：返回 `{ status: "pending", increment_interval: true }`
     - `expired_token`：删除 Redis，返回 `{ status: "expired" }`
     - 成功：`{ access_token, refresh_token, expires_in }` → 加密入库 → 删除 Redis → 返回 `{ status: "bound" }`

### 5.3 token 刷新（懒）

`oauth_service.resolve(user_id, provider)` 是统一入口，对照 PR-1 的 `subscription_credentials.resolve()`：

```python
async def resolve(
    user_id: str,
    provider: Literal["claude_code", "codex"],
    *,
    force_refresh: bool = False,
) -> str:
    """Return a fresh access_token for (user_id, provider), refreshing if needed.

    Raises:
        OAuthCredentialError: if no binding exists or refresh fails.
    """
```

行为：
1. 从 MongoDB 取 doc；不存在 → 抛 `OAuthCredentialError("尚未绑定，请先授权")`
2. 解密 ciphertext → `{access_token, refresh_token}`
3. 若 `force_refresh` 或 `expires_at <= now() + 60s skew`：
   - 调 `refresh_anthropic` / `refresh_codex`（复用 PR-1 已有的 `tradingagents.llm_adapters.subscription_credentials.refresh_claude_code` 与 `refresh_codex`）
   - 加密新 token，update doc（`last_refresh_at = now()`）
   - 返回新 `access_token`
4. 否则直接返回明文 `access_token`，update `last_used_at`

### 5.4 与 PR-1 模块的关系

`oauth_service.refresh_*` 不重复实现，**直接复用** `tradingagents.llm_adapters.subscription_credentials.refresh_claude_code` 和 `refresh_codex`。

但 `subscription_credentials.resolve()` 的"读 → 检查 → 刷新 → 写回"逻辑不能直接复用，因为：
- PR-1 写回的是本机 JSON 文件
- PR-2 写回的是加密 MongoDB doc

因此 PR-2 自己写一遍上层逻辑，仅复用底层网络调用。

## 6. API 端点

所有端点路径在 `/api/oauth/*`，新建 router `app/routers/oauth.py`。

| Method | Path | 鉴权 | 说明 |
|--------|------|------|------|
| GET    | `/api/oauth/authorize/claude_code` | 当前用户 | 启动 PKCE，返回 `{authorize_url, state}` |
| POST   | `/api/oauth/authorize/codex`       | 当前用户 | 启动 device code，返回 `{user_code, verification_uri, interval}` |
| GET    | `/api/oauth/callback/claude_code`  | state query | PKCE 回调，交换 token，入库 |
| POST   | `/api/oauth/poll/codex`            | 当前用户 | 轮询设备授权状态 |
| GET    | `/api/oauth/status/{provider}`     | 当前用户 | 当前用户绑定状态：`{bound, expires_at, last_refresh_at}` 或 `{bound: false}`。`{provider}` ∈ `claude_code`/`codex` |
| POST   | `/api/oauth/refresh/{provider}`    | 当前用户 | 主动触发刷新 |
| DELETE | `/api/oauth/unbind/{provider}`     | 当前用户 | 解绑：从 MongoDB 删除 doc |

**鉴权**：除 `/api/oauth/callback/anthropic` 外，全部走现有 JWT 鉴权（参考 `app/routers/auth_db.py`）。回调端点不能要求 JWT（浏览器重定向回来时通常没有 JWT 头），改为：
- PKCE state token 本身就是用户标识 — Redis 存了 `<user_id>:anthropic:<state>`，从 state 反查
- 这要求 state 是不可猜测的（已用 32 字节 base64url 随机串保证）

`/api/oauth/poll/codex` 用户必须是发起 device code flow 的同一人（通过 JWT 比对 Redis 里的 user_id）。

### 6.1 响应模型

```python
class AuthorizeAnthropicResponse(BaseModel):
    authorize_url: HttpUrl
    state: str
    expires_in: int = 600

class AuthorizeCodexResponse(BaseModel):
    user_code: str               # "ABCD-EFGH"
    verification_uri: HttpUrl
    expires_in: int
    interval: int

class PollCodexResponse(BaseModel):
    status: Literal["pending", "bound", "expired", "denied"]
    increment_interval: bool = False

class OAuthStatusResponse(BaseModel):
    bound: bool
    provider: str
    expires_at: Optional[datetime] = None
    last_refresh_at: Optional[datetime] = None
```

## 7. 把 Web token 接入 graph

PR-1 的 `ChatClaudeCodeOAuth` / `ChatCodexOAuth` 在构造时调 `subscription_credentials.resolve()` 读本机凭据。Web 路径需要绕过这一步，让 token 由 `oauth_service.resolve()` 提供。

### 7.1 Adapter 改造

```python
class ChatClaudeCodeOAuth(ChatAnthropic):
    def __init__(self, model: str, *, access_token: Optional[str] = None, **kwargs):
        if access_token is None:
            # CLI 路径：从本机凭据读 (PR-1 行为)
            cred = sc.resolve("claude_code")
            access_token = cred.access_token
        # else: Web 路径，调用方已 resolve 并通过 api_key 传入
        # ... 后续 anthropic.Anthropic(auth_token=access_token, ...) ...
```

`ChatCodexOAuth` 类似。

### 7.2 `create_llm_by_provider` 与 api_key 透传

`create_llm_by_provider("claude_code", ..., api_key=<oauth_token>)` 时，把 `api_key` 作为 `access_token=` 透传：

```python
if provider.lower() == "claude_code":
    from tradingagents.llm_adapters.claude_code_adapter import ChatClaudeCodeOAuth
    return ChatClaudeCodeOAuth(
        model=model,
        access_token=api_key,  # Web 路径：用 oauth_service 传入的 token；CLI 路径：None → 本机 resolve
        temperature=temperature, max_tokens=max_tokens, timeout=timeout,
    )
```

### 7.3 `analysis_service` 注入

`app/services/analysis_service.py`（PR-2 修改）在构造 config 调 `TradingAgentsGraph` 之前：

```python
if config["llm_provider"] in ("claude_code", "codex"):
    from app.services import oauth_service
    token = await oauth_service.resolve(user_id, config["llm_provider"])
    config["quick_api_key"] = token
    config["deep_api_key"] = token
```

或混合模式下：

```python
for role, provider_field in [("quick", "quick_provider"), ("deep", "deep_provider")]:
    provider = config.get(provider_field)
    if provider in ("claude_code", "codex"):
        token = await oauth_service.resolve(user_id, provider)
        config[f"{role}_api_key"] = token
```

### 7.4 `config_bridge` 改动

`app/core/config_bridge.py::bridge_config_to_env` 当前在启动期跑一次，没有用户上下文。订阅模式下**不**通过它桥接 token — token 在每次分析请求里由 `analysis_service` 取得当前 user_id 后调 `oauth_service.resolve()`。

`config_bridge` 改动仅限于：跳过 `claude_code` / `codex` provider 的 API Key 桥接（不报错，因为没有 API Key 可桥接）。

这是 PR-1 → PR-2 的关键接缝。

## 8. Graph 重构

### 8.1 `create_llm_by_provider` 的 provider 集合

**保留原生分支**（有特殊逻辑）：
- `openai`
- `anthropic`
- `google`
- `deepseek`（独立 token 跟踪）
- `openrouter`（与 openai 几乎一样但保留以维持现有 README 提及）
- `ollama`（local，无 api_key）
- `claude_code` ← OAuth（PR-1 已加）
- `codex` ← OAuth（PR-1 已加）

**收紧到通用 OpenAI 兼容 fallback**（删除专门 elif）：
- `dashscope` / `alibaba` / `阿里百炼`
- `qianfan`
- `zhipu`
- `siliconflow`
- `custom_openai`
- 任何其它未识别的 provider name

→ 这些 provider 用户**仍能**使用，但要在 Web UI 配置里设置 `base_url`（指向对应的 OpenAI 兼容端点）。`create_llm_by_provider` 的"自定义厂家"fallback 已能处理。

### 8.2 `TradingAgentsGraph.__init__` 重构

当前实现：~400 行 elif 链，每个 provider 一个分支重复创建 LLM。

新实现：

```python
def __init__(self, ..., config=None):
    self.config = config or DEFAULT_CONFIG

    quick_provider = self.config.get("quick_provider") or self.config["llm_provider"]
    deep_provider = self.config.get("deep_provider") or self.config["llm_provider"]

    self.quick_thinking_llm = create_llm_by_provider(
        provider=quick_provider,
        model=self.config["quick_think_llm"],
        backend_url=self.config.get("quick_backend_url") or self.config.get("backend_url", ""),
        temperature=self.config.get("quick_model_config", {}).get("temperature", 0.7),
        max_tokens=self.config.get("quick_model_config", {}).get("max_tokens", 4000),
        timeout=self.config.get("quick_model_config", {}).get("timeout", 180),
        api_key=self.config.get("quick_api_key"),
    )
    self.deep_thinking_llm = create_llm_by_provider(
        provider=deep_provider,
        model=self.config["deep_think_llm"],
        backend_url=self.config.get("deep_backend_url") or self.config.get("backend_url", ""),
        temperature=self.config.get("deep_model_config", {}).get("temperature", 0.7),
        max_tokens=self.config.get("deep_model_config", {}).get("max_tokens", 4000),
        timeout=self.config.get("deep_model_config", {}).get("timeout", 180),
        api_key=self.config.get("deep_api_key"),
    )
    # ... memory / graph setup 不变 ...
```

代码量减少 ~370 行。

**验证**：对每个保留的原生 provider 写一个 smoke 单测，确保 `__init__(config={"llm_provider": "X", "deep_think_llm": "test", "quick_think_llm": "test"})` 不抛异常。Mock 掉 LLM 类的网络调用。

## 9. memory.py embedding 回退

`tradingagents/agents/utils/memory.py:FinancialSituationMemory.__init__` 当前根据 `llm_provider` 选 embedding：
- `dashscope/qianfan` → DashScope TextEmbedding
- `deepseek` → 阿里百炼 → OpenAI fallback
- `google` → 阿里百炼 → OpenAI fallback
- 其它 → OpenAI

订阅模式下：

```python
if config["llm_provider"] in ("claude_code", "codex"):
    embedding_provider = os.getenv("EMBEDDING_PROVIDER")
    if not embedding_provider:
        logger.warning(
            "订阅模式 (provider=%s) 不支持 OAuth token 做 embedding。"
            "Memory 已默认禁用。如需启用，请设置 EMBEDDING_PROVIDER "
            "（dashscope / openai）并提供对应 API Key。",
            config["llm_provider"],
        )
        # 标记禁用 — 由 TradingAgentsGraph 读取该标记跳过 memory 实例化
        self._disabled_reason = "subscription_no_embedding"
        return
    # else: 用 EMBEDDING_PROVIDER 指定的方式
```

`TradingAgentsGraph.__init__` 已有 `memory_enabled` 判断；扩展为：

```python
memory_enabled = self.config.get("memory_enabled", True)
if memory_enabled:
    try:
        self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
        # ...
    except UnsupportedEmbeddingError:  # 新异常
        logger.warning("Memory 不可用，已自动禁用")
        memory_enabled = False
if not memory_enabled:
    self.bull_memory = None
    # ... 其它 memory 设为 None ...
```

`memory.py` 检测到订阅模式 + 无 `EMBEDDING_PROVIDER` 时抛 `UnsupportedEmbeddingError`，让 graph 干净地降级。

## 10. PR-1 跟进项

### 10.1 `force_refresh` 参数

PR-1 `subscription_credentials.resolve()` 加可选参数：

```python
def resolve(
    provider: Literal["claude_code", "codex"],
    *,
    force_refresh: bool = False,
) -> SubscriptionCredential:
    """... 当 force_refresh=True 时即使未到期也强制刷新。"""
```

测试：`test_force_refresh_bypasses_expiry_check`。

### 10.2 测试 stub fixture 化

`tests/unit/test_create_llm_by_provider_subscription.py:11-26` 的模块级 `sys.modules["dashscope"] = stub` 块提到 `tests/conftest.py` 的 session-scoped fixture：

```python
# tests/conftest.py
import sys
import types
from unittest.mock import MagicMock
import pytest

@pytest.fixture(scope="session", autouse=False)
def stub_optional_llm_deps():
    """供 tests/unit/test_*_subscription.py 等使用。autouse=False 故需要显式请求。"""
    if "dashscope" not in sys.modules:
        # ...
```

测试文件改为：
```python
@pytest.fixture(autouse=True)
def _(stub_optional_llm_deps):
    pass
```

### 10.3 Keychain writer

**不做**。Web 后端不依赖本机 Keychain；CLI 用户在 token 接近过期时会收到 PR-1 的 actionable error 提示运行 `claude login`。如果 CLI 用户群增长再补。

## 11. 测试策略

### 11.1 单测（pytest）

- `tests/unit/test_oauth_crypto.py`
  - 加解密往返
  - tag 校验（密文修改一字节 → 解密失败）
  - 错误 key 长度 → fail-fast
- `tests/unit/test_oauth_service_anthropic.py`
  - PKCE flow 完整 mock（mock httpx + Redis）
  - state 不匹配 → 拒绝
  - state 过期 → 拒绝
  - 回调成功 → MongoDB upsert
- `tests/unit/test_oauth_service_codex.py`
  - device code flow
  - 轮询状态机：pending / slow_down / expired / success
- `tests/unit/test_oauth_service_resolve.py`
  - 无 binding → 错误
  - token 未到期 → 直接返回
  - token 到期 → 刷新 + 写回
  - `force_refresh=True`
  - 解密失败 → 友好错误（建议重新授权）
- `tests/unit/test_oauth_router.py`
  - 6 个端点的鉴权与响应形态（mock service 层）
- `tests/unit/test_config_bridge_oauth.py`
  - LLM provider in (claude_code, codex) + 已绑定 → token 注入 config
  - LLM provider in (claude_code, codex) + 未绑定 → 友好错误
- `tests/unit/test_trading_graph_init_refactor.py`
  - 每个保留 provider 一个 smoke：构造 graph 不抛异常
  - 删除分支后，dashscope / qianfan / zhipu 走 fallback 仍然初始化成功
- `tests/unit/test_memory_subscription_fallback.py`
  - OAuth provider + 无 EMBEDDING_PROVIDER → memory 禁用 + 警告
  - OAuth provider + EMBEDDING_PROVIDER=dashscope → memory 用 dashscope embedding

预期：~30–40 个新单测。

### 11.2 集成测试

`tests/integration/test_oauth_flow_e2e.py`：起一个 FastAPI TestClient，mock 上游 Anthropic/OpenAI 服务器（httpx mock），跑完整 PKCE flow 和 device code flow，断言 MongoDB 状态。这个测试归到 `pytest -m integration`，默认跳过。

### 11.3 手动 smoke

`scripts/smoke_test_oauth_pkce.py`：本地启动 backend，浏览器手动完成一次 Anthropic 授权，断言 MongoDB 写入了密文且 `resolve()` 能解出可用 token。文档化在 README 里。

## 12. 风险

### 12.1 安全

- **加密 key 泄露 = 所有 token 失效**。.env.example 里的命令明确说明 key 如何生成。生产环境运维需要把 key 注入到 secret manager（Kubernetes Secret / Docker secrets / AWS Parameter Store）。
- **state token 必须不可猜**：用 `secrets.token_urlsafe(32)`
- **OAuth callback 端点不能要求 JWT**（浏览器重定向无 Header），靠 state 防 CSRF
- 日志里 token 必须打码：`oauth_service` 内部统一通过 `_redact()` helper

### 12.2 合规

- 设计文档 § 5.1 已记录订阅条款风险：Anthropic / OpenAI 订阅 ToS 限制程序化批量调用
- PR-3 UI 在首次绑定时弹合规提示（PR-2 留好后端字段：`user_oauth_credentials.consent_acknowledged_at`）

### 12.3 升级

- Codex CLI 凭据格式如更换 → 本设计不受影响（PR-2 用 OAuth 直接调，不读 CLI 文件）
- Anthropic 改 OAuth client_id 或 endpoint → 需要更新常量（同 PR-1）

### 12.4 多点部署

PR-2 假设单实例后端。多实例下：
- Redis 已用于 state 存储 → ok
- 同一用户在两个实例并发刷新 token → MongoDB upsert 会有一个赢家，可能其中一个看到 invalid_grant。可接受，下次重试自动恢复
- 真正的多实例 + 高并发场景留给 v1.1（加分布式锁）

## 13. 实施路线

参考 § 2 架构图自顶向下：

1. **加密层** (`oauth_crypto.py` + 启动校验) — 最底层，先有
2. **数据模型** (`models/oauth.py` + MongoDB collection + 索引)
3. **OAuth service 核心** (`oauth_service.py`)
   - 复用 PR-1 的 `refresh_claude_code` / `refresh_codex`
   - PKCE flow / device code flow / resolve / unbind
4. **API router** (`routers/oauth.py`)
5. **PR-1 跟进**：`force_refresh` 参数 + test stub fixture
6. **Adapter 改造**：`ChatClaudeCodeOAuth` / `ChatCodexOAuth` 接受 `access_token` 显式参数
7. **`create_llm_by_provider` 收紧** + **`TradingAgentsGraph.__init__` 重构**
8. **`config_bridge` 集成** — analysis_service 调用 oauth_service.resolve 注入 token
9. **`memory.py` 回退**
10. **Smoke + 集成测试**

每一步独立可测；详细任务列表与代码片段进入 `docs/superpowers/plans/`。

## 14. 待定问题

1. **回调端点的成功响应格式**：HTML 关闭弹窗 vs 重定向回 `/oauth/callback` 前端路由 vs 返回 JSON。**决定：返回 HTML（含 JS 自动关闭弹窗），PR-3 前端用 `window.opener.postMessage` 接收成功信号**。如果 PR-3 不用弹窗模式，再调整。
2. **绑定状态在 Web UI 多模型选择页的可见性**：用户的"已绑定 Claude"是否在大模型厂家选择页直接展示？由 PR-3 决定，但 PR-2 的 `/api/oauth/status/{provider}` 端点足够支撑。
3. **token 在多次分析任务中的复用**：每次 `analysis_service` 启动时调一次 `resolve()` 获取 token；分析期间 token 不变（PR-1 的 token-lifetime-in-adapter 限制）。对一次性 ~5min 的分析任务足够。长任务（>60min）需 PR-3 之后再考虑。

## 15. 参考

- PR-1 实现：commits `e6ad1bb..13ba1d9`
- hermes-agent OAuth 入口：
  - PKCE：`hermes_cli/web_server.py:1640-1807`
  - Device code：`hermes_cli/auth.py:4548-4752`
- 已确认的常量（PR-1 已用）：
  - Anthropic client_id: `9d1c250a-e61b-44d9-88ed-5944d1962f5e`
  - Codex client_id: `app_EMoamEEZ73f0CkXaXp7hrann`
  - Anthropic token endpoints: `platform.claude.com/v1/oauth/token` / `console.anthropic.com/v1/oauth/token`
  - Codex token endpoint: `auth.openai.com/oauth/token`
  - Codex device endpoint: `auth.openai.com/oauth/device/code`
