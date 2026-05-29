# 设计：OAuth 订阅 provider 在厂家管理中作为一等公民（v2）

> 状态：已批准设计（v1: 2026-05-28，v2: 同日，依据 adversarial review 修订）。约定：标识符 / 代码英文，叙述中文。

## 目标

把 `codex` 和 `claude_code` 这两个 OAuth 订阅 provider 在 `llm_providers` 集合里作为**一等公民**记录（带 `auth_kind="oauth"` 标记），让它们和 api-key 厂家走同一套 CRUD / 过滤 / UI 流程，并把散落在 `config_bridge` / `memory.py` / 前端的硬编码 `("claude_code","codex")` 特例收敛到**单一常量**或 `auth_kind` 字段。

## 背景与问题

当前 codex/claude_code 不在 `llm_providers` 表里（因为它们没有 api_key，是 OAuth 订阅），导致：

1. **`/api/config/llm` 看不到 codex 模型**（即使 `system_configs.llm_configs` 里已存在、`enabled=true`）—— 过滤逻辑要求 `provider in active_provider_names`（`app/routers/config.py:953-956`）。
2. 代码中**至少 5 处**用硬编码名单识别"OAuth 订阅 provider"：
   - `app/core/config_bridge.py:126`（DB 路径）
   - `app/core/config_bridge.py:161`（JSON 回退路径，v1 漏掉）
   - `app/services/analysis_service.py:107, 138`
   - `tradingagents/agents/utils/memory.py:111`（Apache 核心；屏蔽 OAuth provider 走 embedding）
   - `frontend/src/views/Settings/components/LLMConfigDialog.vue:401-418`（合成 SUBSCRIPTION_PROVIDERS 列表，下拉项）
3. **副带 regression**：`config_service.py::delete_llm_config` 用 `llm.provider.value`（旧 enum 写法），`provider` 现已是 `str` → AttributeError 被吞 → 报"大模型配置不存在"。**与本 PR 同捆修复**。

## 设计概览

```
┌─────────────────────────────────────────────────────────────────┐
│  LLMProvider 模型                                                  │
│    + auth_kind: Literal["api_key","oauth"] = "api_key"            │
│  LLMProviderResponse 同样新增 auth_kind（前端可读）                 │
│  LLMProviderRequest 不暴露此字段（服务端强制 default，杜绝鬼厂家）   │
└─────────────────────────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼─────────────────────────────────┐
        ▼                       ▼                                 ▼
 ┌──────────────────┐   ┌──────────────────────────┐    ┌─────────────────────────┐
 │ Seed 端点（幂等）  │   │ 共享常量模块                │    │ 厂家管理页 + 模型对话框   │
 │ POST /init-      │   │ OAUTH_SUBSCRIPTION_       │    │ + 快速添加按钮            │
 │ subscription     │   │ PROVIDER_NAMES =          │    │ + OAuth 行展示差异化      │
 │                  │   │   frozenset({"codex",      │    │ + ProviderDialog 适配     │
 │ → upsert codex + │   │   "claude_code"})         │    │ + LLMConfigDialog 适配    │
 │   claude_code    │   │ 给 JSON 回退路径 +         │    │   并移除合成 SUBSCRIPTION │
 │                  │   │ memory.py 使用             │    │   _PROVIDERS 列表         │
 └──────────────────┘   └──────────────────────────┘    └─────────────────────────┘
                                │
                                ▼
        ┌───────────────────────────────────────────────┐
        │ codex 现在是真正的 active provider              │
        │ → /api/config/llm 过滤天然放行                  │
        │ → 厂家管理 / 模型管理统一流程                    │
        │ → DB 路径用 provider.auth_kind 判断             │
        │ → 非 DB 路径用共享常量；下拉无重复项             │
        └───────────────────────────────────────────────┘
```

## 详细设计

### 1. 数据模型

`app/models/config.py`：

```python
# LLMProvider —— 持久化模型
class LLMProvider(BaseModel):
    ...
    auth_kind: Literal["api_key", "oauth"] = "api_key"  # 新增

# LLMProviderResponse —— 返回给前端
class LLMProviderResponse(BaseModel):
    ...
    auth_kind: Literal["api_key", "oauth"] = "api_key"  # 新增（前端要读）

# LLMProviderRequest —— 客户端请求体
# 不新增 auth_kind 字段（安全：客户端无法构造 oauth 鬼厂家）
class LLMProviderRequest(BaseModel):
    ...
    # 不出现 auth_kind
```

- **存量数据**：Pydantic 反序列化时给缺省值，所有现存 api-key 厂家文档无需迁移。
- **安全护栏**：`LLMProviderRequest` 不暴露 → `add_llm_provider` / `update_llm_provider` 收到的请求里没有 auth_kind → 服务端 `LLMProvider(**request.model_dump())` 自动用 default `"api_key"`。客户端绕过该护栏的唯一方法是直接调 seed 端点（仅创建预定义的两个 name）。

### 2. 后端

#### 2.1 共享常量模块（新增）

新文件 `tradingagents/utils/oauth_providers.py`（**Apache 区**，可被 `memory.py` 与 `config_bridge.py` 共同 import）：

```python
"""Authoritative list of OAuth subscription providers.

DB-driven code paths SHOULD prefer reading LLMProvider.auth_kind directly.
Non-DB code paths (JSON config fallback, Apache core memory layer) consult
this constant. Single source of truth for OAuth provider naming.
"""

OAUTH_SUBSCRIPTION_PROVIDER_NAMES = frozenset({"codex", "claude_code"})
```

#### 2.2 Seed 服务方法 + 端点

**服务层**：`app/services/config_service.py` 新增 `async def init_subscription_providers() -> dict[str, list[str]]`，仿照已有的 `init_aggregator_providers`：

- 内置两条 seed 数据：
  ```python
  _SUBSCRIPTION_SEEDS = [
    {
      "name": "codex",
      "display_name": "OpenAI Codex (订阅)",
      "auth_kind": "oauth",
      "default_base_url": "https://chatgpt.com/backend-api/codex",
      "is_active": True,
      "description": "ChatGPT 订阅式 Codex 模型，通过 OAuth 设备码授权使用",
      "supported_features": ["chat"],
    },
    {
      "name": "claude_code",
      "display_name": "Claude Code (订阅)",
      "auth_kind": "oauth",
      "default_base_url": "https://api.anthropic.com",
      "is_active": True,
      "description": "Anthropic Claude Code 订阅，通过 OAuth PKCE 授权使用",
      "supported_features": ["chat"],
    },
  ]
  ```
  注：`supported_features: ["chat"]` 是必需的——`ProviderDialog.vue:370-372` 校验 `min: 1`，缺失会导致已 seed 的行在 UI 上编辑时校验报错。
- **幂等策略**：`by name` upsert。
  - **不存在** → `insert_one(full seed doc)`。
  - **已存在** → 仅 `$set` 结构性字段：`auth_kind`、`default_base_url`、`updated_at`。**不覆盖** `display_name`、`description`、`is_active`、`supported_features`（用户可能改过）。
- 返回 `{"created": [...], "updated": [...]}` 给路由。

**路由层**：`app/routers/config.py` 新增 `POST /api/config/llm/providers/init-subscription`：

```python
@router.post("/llm/providers/init-subscription", response_model=dict)
async def init_subscription_providers(
    current_user: User = Depends(get_current_user),  # 与 init-aggregators 一致
):
    """Idempotently seed codex + claude_code OAuth subscription providers."""
    result = await config_service.init_subscription_providers()
    return result
```

**权限说明**：仓库内**目前没有 admin gate**（验证：grep `require_admin` / `get_current_admin` 0 命中）；所有 `app/routers/config.py` 的写操作都用 `get_current_user`（任意登录用户）。本端点遵守同样模式。若将来引入 admin gate，统一升级，本 spec 不引入特例。

#### 2.3 `config_bridge.py` 重构（双路径）

**DB 路径**（`app/core/config_bridge.py:126`）：

```python
# 旧
if provider.name in ("claude_code", "codex"): continue
# 新
if provider.auth_kind == "oauth": continue
```
此处 `provider` 已是从 DB 加载的 `LLMProvider` 对象。

**JSON 回退路径**（`app/core/config_bridge.py:161`，v1 漏掉）：

```python
# 旧
if llm_config.provider in ("claude_code", "codex"): continue
# 新
from tradingagents.utils.oauth_providers import OAUTH_SUBSCRIPTION_PROVIDER_NAMES
if llm_config.provider in OAUTH_SUBSCRIPTION_PROVIDER_NAMES: continue
```
JSON 回退路径里没有 LLMProvider 对象（数据从 JSON 文件读），所以读常量。

#### 2.4 `memory.py:111` 重构

`tradingagents/agents/utils/memory.py:111` 是 Apache 核心，**不能 import `app/`**。改为 import 共享常量：

```python
from tradingagents.utils.oauth_providers import OAUTH_SUBSCRIPTION_PROVIDER_NAMES
if self.llm_provider in OAUTH_SUBSCRIPTION_PROVIDER_NAMES:
    # OAuth 订阅 provider 不可用作 embedding，要求显式 EMBEDDING_PROVIDER
    ...
```

#### 2.5 `analysis_service.py:107/138` 保留硬编码

**保留**，原因（**修订 v1 的错误理由**）：
- 不是为了避免 DB 查询（数据已在内存）。
- 真实理由：(a) OAuth provider 名字本身是 system constant，几乎不变；(b) 本 PR 已经在重构 4 个站点，再加 2 个扩大 blast radius；(c) `analysis_service` 已经持有 `config.get("llm_provider")` 字符串，不持有 `LLMProvider` 对象——读 `auth_kind` 需要额外查询或重构上游数据流，得不偿失。
- 在该行加注释指明"参见 OAUTH_SUBSCRIPTION_PROVIDER_NAMES"。

#### 2.6 `app/routers/config.py::get_llm_configs` 过滤不变

Seed 后 codex/claude_code 是真的 active provider → 过滤天然放行，**无需任何 OAuth 豁免特例**。这是本设计的核心收益。

#### 2.7 `delete_llm_config` regression 修复

`app/services/config_service.py:589, 597` 的 `llm.provider.value` → `llm.provider`（两处）。

### 3. 前端

#### 3.1 类型 / API

- `frontend/src/types/config.ts`：`LLMProvider` 接口加 `auth_kind?: 'api_key' | 'oauth'`。
- `frontend/src/api/config.ts`：加 `initSubscriptionProviders(): Promise<{created: string[], updated: string[]}>`。

#### 3.2 厂家管理页（`ConfigManagement.vue` providers tab）

- 表格上方加按钮 **"快速添加订阅厂家 (Codex / Claude Code)"** → 调 `initSubscriptionProviders()` → 成功后 reload providers 表 → 提示 `已添加 N 个 / 已是最新` 文案（按 `created`/`updated` 长度）。
- "API密钥"列：`row.auth_kind === 'oauth'` 显示 `—`（不显示密钥框、显隐按钮）。其他厂家保持现有渲染。

#### 3.3 `ProviderDialog.vue`（编辑/添加厂家对话框）

**编辑模式**：
- `auth_kind === 'oauth'` 时：禁用（disabled）`api_key`、`default_base_url` 输入；保留 `display_name`、`is_active`、`description`、`supported_features` 可编辑。
- 顶部加一行说明："订阅类厂家无需 API Key，请前往订阅授权完成 OAuth 绑定"。
- `supported_features` 的 `min: 1` 校验保留（seed 已预填 `["chat"]`，不会触发空校验）。

**添加模式**：
- 完全保持现状（不暴露 `auth_kind` 选项）。提交时不带 `auth_kind` 字段 → 服务端用 default `"api_key"`。
- OAuth 厂家**只能**通过"快速添加"按钮加。

#### 3.4 `LLMConfigDialog.vue`（添加/编辑模型对话框）—— **移除合成 SUBSCRIPTION_PROVIDERS**

**当前问题**（v1 漏掉的关键点）：该文件第 401-418 行定义了 `SUBSCRIPTION_PROVIDERS` 合成数组，把 codex/claude_code 硬塞进 provider 下拉。Seed 后真实 provider 也从 DB 返回，下拉会出现**重复项**。

**修订**：
- 删除 `SUBSCRIPTION_PROVIDERS`、`SUBSCRIPTION_PROVIDER_NAMES` 两个常量。
- 删除"把合成列表合并进下拉"的所有调用点（约 7 处：line 418, 423-437, 557, 723, 798）。
- 新 `isSubscriptionProvider` 计算：
  ```ts
  const selectedProvider = computed(() =>
    providers.value.find(p => p.name === formData.value.provider)
  )
  const isSubscriptionProvider = computed(() =>
    selectedProvider.value?.auth_kind === 'oauth'
  )
  ```
- 选到 OAuth 厂家时：`api_key` 字段隐藏；其他字段（model_name / max_tokens / temperature / ...）保持。
- 现有 `isSubscriptionProvider` 触发的其他 UI 分支（如默认描述文案）按需保留，只是数据源换成 `auth_kind`。

### 4. 数据流（codex 端到端验证）

```
1) 用户进厂家管理 → 点"快速添加订阅厂家" → POST /init-subscription
2) llm_providers 集合多两条 (codex/claude_code, auth_kind=oauth, is_active=true)
3) 用户在大模型配置里"添加模型"，provider 下拉只出现 "OpenAI Codex (订阅)" 一次（无重复），选它 →
   LLMConfigDialog 检测 auth_kind=oauth → 不要求填 api_key
4) /api/config/llm 过滤：codex ∈ active_provider_names → 模型可见
5) 用户去"订阅授权" tab → OAuth device-code → token 入 oauth_credentials
6) 跑分析：analysis_service 注入 token 到 config["deep_api_key"]（用 OAUTH_SUBSCRIPTION_PROVIDER_NAMES 判断）；
   config_bridge 见 provider.auth_kind=oauth 跳过 key 桥接；
   核心 ChatCodexOAuth 用注入的 token 调 codex
7) 财报抽取链（PR #13）继续工作，不受本设计影响
8) memory.py 见 llm_provider ∈ OAUTH_SUBSCRIPTION_PROVIDER_NAMES → 提示需要 EMBEDDING_PROVIDER
```

### 5. 错误处理 / 边界

| 场景 | 行为 |
|---|---|
| Seed 端点失败（DB 异常等） | 返回 500 + 新 envelope（含 `exception_type`，PR #14） |
| 并发重复点 seed | mongo upsert 保证只创建一次 |
| 用户把 codex 改成 `is_active=false` | codex 模型立刻在管理 UI 隐藏；等同临时禁用 |
| 用户删了 codex 厂家行（DELETE） | codex 模型变孤儿不显示。一致行为 |
| **删了又重新 seed** | `llm_providers` 新行 `_id` 不同；但 `name="codex"` 同名 → 旧的 orphan `llm_configs[]` 自动按名匹配重新可见（无 FK 引用），孤儿"复活" |
| 用户没授权就跑 codex 模型 | `analysis_service.oauth_service.resolve` 抛 `OAuthCredentialError`（行为已存在，不动） |
| 用户在添加模型时手工输入 provider="codex" 而厂家没 seed | 模型保存进 `llm_configs`，但 codex 不在 active_provider_names → 模型不可见。前端提示"请先用快速添加按钮添加订阅厂家" |
| 客户端构造伪 `auth_kind="oauth"` 想 POST `/providers` | `LLMProviderRequest` 不含此字段 → 自动被忽略；服务端 `LLMProvider` 用默认值 `"api_key"`。无法创建鬼 oauth 厂家 |

### 6. 测试

**单测**（pytest，TA-CN .venv）：

- `tests/unit/test_llm_provider_auth_kind.py`：
  - `LLMProvider(auth_kind="oauth")` 反序列化 + 默认值兼容存量数据
  - **`LLMProviderResponse` 正确序列化 auth_kind**（防回归 Critical #1）
  - `LLMProviderRequest` 没有 `auth_kind` 字段 → 即使客户端塞了也被忽略（防回归 Critical #2）
- `tests/unit/test_config_service_delete.py`：`delete_llm_config` 能匹配 string provider；同时测一个 enum-like 边角 case（已不该出现，但加保险）
- `tests/unit/test_config_bridge_oauth_skip.py`：
  - DB 路径：mock 两类 provider，断言 `auth_kind=="oauth"` 跳过 api_key 桥接、`api_key` 类正常桥接
  - **JSON 回退路径**：mock provider 名在 `OAUTH_SUBSCRIPTION_PROVIDER_NAMES` 里时跳过
- `tests/unit/test_init_subscription_endpoint.py`：seed 端点幂等（两次调用，第二次只 updated 不 created；且不覆盖 display_name）
- `tests/unit/test_get_llm_configs_orphan_revive.py`：模拟"删 provider → orphan 模型隐藏 → 重 seed → 模型复活"流程
- `tests/unit/test_memory_oauth_block.py`：`FinancialSituationMemory.__init__` 对 OAuth provider 走 `EMBEDDING_PROVIDER` 分支（回归保护，确认重构后行为不变）

**手测脚本**（前端无 jest/vitest，验证 verified：`frontend/package.json` 无 test 脚本，spec 接受手测）：
1. 点快速添加 → 看 UI 出现 codex/claude_code 两行
2. 点 codex 编辑 → 看 api_key/base_url 禁用
3. 加 codex 模型 → 下拉中 "OpenAI Codex (订阅)" 仅出现一次（无重复）→ 对话框不要 api_key
4. 列表中看到 codex 模型
5. 走 OAuth 授权 → 跑一次分析 → 看分析使用 codex 成功
6. 删 codex 模型 → 验证 delete bug 修复（不再报"大模型配置不存在"）

### 7. 仓库 / 分支 / 协调

- 单仓（TA-CN）改动，分支 `feat/oauth-providers-first-class`（已建）。
- 不涉及 extractor 仓。
- 不影响 PR #13 / PR #14（已合并到 main）。
- 新增模块 `tradingagents/utils/oauth_providers.py` 在 Apache 区，无外部依赖。

### 8. 范围外（Out of scope）

- **三个 tab（厂家 / 模型目录 / 大模型配置）的合并重组** —— 后续 UX 单题。
- **对 `oauth_service.py` / OAuth 授权流程本身的修改** —— 不在本 spec 范围。
- **`analysis_service.py:107/138` 改读 `auth_kind`** —— 为缩小 blast radius，保留硬编码 + 注释指向常量。将来若 `analysis_service` 上游数据流重构允许零成本读到 `auth_kind`，可单独跟进。
- **`tradingagents/graph/trading_graph.py:66/76` 的 `provider == "codex"` / `== "claude_code"` 分支** —— 这是**按 provider 选适配器类**（`ChatCodexOAuth` vs `ChatClaudeCodeOAuth`），正确的轴是 provider 而非 OAuth；不归 OAuth 抽象覆盖。
- **真正的 admin gate** —— 仓库当前没有，本 spec 不引入特例。
- **将 seed 端点扩展为通用 OAuth provider 注册流程** —— 当前只两个，硬编码可接受；将来添新 OAuth provider 再讨论是否改成 list-driven。
- **前端引入 vitest 等测试框架** —— 当前项目无前端测试基建，前端改动接受"手测覆盖"风险，统一遗留。
