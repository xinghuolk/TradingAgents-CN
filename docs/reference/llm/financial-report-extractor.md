# TradingAgents-CN 的 LLM 配置实现 & financial-report-llm-extractor 复用方案

> 目的：说明 **TradingAgents-CN** 当前是如何组织、存储、解析、实例化 LLM 配置的；
> 并给出让上游 **`../financial-report-llm-extractor`** 复用同一套 LLM 配置的改造方案。
>
> 约定：标识符 / 代码用英文，叙述用中文（与本仓库双语约定一致）。

---

## 第一部分 · TradingAgents-CN 的 LLM 配置实现

### 1. 总体架构：三层投射

LLM 配置在本项目里走的是一条 **"数据库 → 环境变量 → LLM 实例"** 的单向投射链：

```
┌──────────────────────────────────────────────────────────────┐
│  ① 存储层  MongoDB                                             │
│     llm_providers  集合   →  LLMProvider（厂家：name/key/url） │
│     system_configs 集合   →  LLMConfig  （模型：provider/model/temperature/...） │
│     由 app/services/config_service.py 读写，前端 Web UI 维护    │
└──────────────────────────────────────────────────────────────┘
                        │  app/main.py 启动时 & 每次分析前
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  ② 桥接层  app/core/config_bridge.py::bridge_config_to_env()   │
│     把 DB 配置投射成进程环境变量：                              │
│       {PROVIDER_NAME_UPPER}_API_KEY                            │
│       TRADINGAGENTS_QUICK_MODEL / _DEEP_MODEL / _DEFAULT_MODEL │
└──────────────────────────────────────────────────────────────┘
                        │  tradingagents/ 核心只读环境变量 + config dict
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  ③ 实例化层  tradingagents/graph/trading_graph.py             │
│     create_llm_by_provider(provider, model, backend_url, ...) │
│     → 返回具体的 LangChain Chat 模型（ChatOpenAI / ChatDeepSeek / ...）│
│     TradingAgentsGraph 据此创建 quick_thinking_llm / deep_thinking_llm │
└──────────────────────────────────────────────────────────────┘
```

> ⚠️ 关键含义（来自 CLAUDE.md）：改动核心依赖的某个配置键，通常要同时改
> `config_bridge.py`、`app/core/config.py` 的 `Settings`、`tradingagents/default_config.py` 三处，
> 只改一处会"静默失效"。

---

### 2. 存储层：数据模型

定义于 `app/models/config.py`：

- **`LLMProvider`（厂家级）**：`name`（唯一标识，如 `openai`/`dashscope`/`deepseek`/`google`）、
  `display_name`、`api_key`、`default_base_url`、`is_active`、`extra_config`。
- **`LLMConfig`（模型级）**：`provider`、`model_name`（如 `qwen-turbo`/`gpt-4o`/`deepseek-chat`）、
  `api_base`（可选覆盖）、`api_key`（可选，覆盖厂家 key）、`max_tokens`、`temperature`、`timeout`、
  `input_price_per_1k` / `output_price_per_1k` 等。

两者分别落在 MongoDB 的 `llm_providers` 与 `system_configs.llm_configs` 中，由前端 Web UI + `config_service.py` 维护。

---

### 3. 桥接层：`bridge_config_to_env()`

文件：`app/core/config_bridge.py`。它在 `app/main.py` 的 lifespan 启动钩子里、以及每次分析前执行，把 DB 配置投射成环境变量。LLM 相关的关键投射：

| 来源 | 环境变量 | 代码位置 |
|---|---|---|
| `LLMProvider.api_key` | `{provider.name.upper()}_API_KEY` | `config_bridge.py:93,102` |
| `LLMConfig.api_key`（模型级覆盖） | `{llm_config.provider.upper()}_API_KEY` | `config_bridge.py:129,139` |
| 默认模型 | `TRADINGAGENTS_DEFAULT_MODEL` | `config_bridge.py:150` |
| 快思考模型 | `TRADINGAGENTS_QUICK_MODEL` | `config_bridge.py:156` |
| 深思考模型 | `TRADINGAGENTS_DEEP_MODEL` | `config_bridge.py:162` |

即环境变量约定为 **`<PROVIDER 名大写>_API_KEY`**，例如：

```
OPENAI_API_KEY  DASHSCOPE_API_KEY  DEEPSEEK_API_KEY  GOOGLE_API_KEY
QIANFAN_API_KEY ZHIPU_API_KEY  SILICONFLOW_API_KEY  OPENROUTER_API_KEY
ANTHROPIC_API_KEY  CUSTOM_OPENAI_API_KEY（自定义兜底）
```

同文件还提供了两个读取辅助（其他模块复用配置时用）：

- `get_provider_api_key(provider)` → `os.environ.get(f"{provider.upper()}_API_KEY")`（`config_bridge.py:526-527`）
- `get_model(kind)` → 读 `TRADINGAGENTS_QUICK_MODEL` / `_DEEP_MODEL` / `_DEFAULT_MODEL`（`config_bridge.py:541-545`）

> 注意：Tushare/Finnhub 等数据源 key 也在这里桥接，但与 LLM 无关。

---

### 4. 实例化层：`create_llm_by_provider()`

文件：`tradingagents/graph/trading_graph.py:41`。这是**最干净、可复用的 LLM 构造入口**：

```python
def create_llm_by_provider(
    provider: str,        # google / dashscope / deepseek / openai / anthropic / 自定义
    model: str,           # 模型名
    backend_url: str,     # API 地址（base_url）
    temperature: float,
    max_tokens: int,
    timeout: int,
    api_key: str = None,  # 可选；为 None 时按 provider 从环境变量读取
):
    ...
```

分支逻辑（`trading_graph.py:41-177`）：

| provider | 适配器 / 类 | API Key 解析 | 默认 base_url |
|---|---|---|---|
| `claude_code` | `ChatClaudeCodeOAuth` | OAuth（本机 CLI 凭据，忽略 api_key/url） | Anthropic |
| `codex` | `ChatCodexOAuth` | OAuth（本机 CLI 凭据） | Codex |
| `google` | `ChatGoogleOpenAI` | `api_key` → `GOOGLE_API_KEY` | generativelanguage…/v1beta |
| `deepseek` | `ChatDeepSeek` | `api_key` → `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| `openai`/`siliconflow`/`openrouter`/`ollama` | `ChatOpenAI` | `api_key` → 各自 `*_API_KEY`（openrouter 还回退 `OPENAI_API_KEY`） | 传入 backend_url |
| `anthropic` | `ChatAnthropic` | LangChain 自行读 `ANTHROPIC_API_KEY` | 传入 backend_url |
| 其它（自定义厂家） | `ChatOpenAI`（OpenAI 兼容模式） | `api_key` → `{PROVIDER}_API_KEY` → `{provider}_API_KEY` → `CUSTOM_OPENAI_API_KEY` | 传入 backend_url |

适配器实现集中在 `tradingagents/llm_adapters/`：

```
openai_compatible_base.py   OpenAICompatibleBase + DashScope/Qianfan/Zhipu/Custom 适配器 + OPENAI_COMPATIBLE_PROVIDERS 注册表
deepseek_adapter.py         ChatDeepSeek
google_openai_adapter.py    ChatGoogleOpenAI
claude_code_adapter.py      ChatClaudeCodeOAuth（订阅式 OAuth）
codex_adapter.py / codex_responses_adapter.py   ChatCodexOAuth
dashscope_openai_adapter.py
subscription_credentials.py 订阅凭据解析
```

> 新增 provider 时（按 CLAUDE.md）应优先扩展 `create_llm_by_provider` + `openai_compatible_base.py`，
> 而不是再加一个 `elif`。

---

### 5. API Key 解析顺序（务必牢记）

```
config["quick_api_key"] / config["deep_api_key"]   （来自 DB 驱动的 web config）
        ↓ 若为空
provider 专属环境变量（DASHSCOPE_API_KEY / GOOGLE_API_KEY / DEEPSEEK_API_KEY / …）
        ↓ 若仍为空（仅自定义厂家分支）
CUSTOM_OPENAI_API_KEY
```

调试"用错了 key"时，先查 `config_bridge.py`。

---

### 6. 混合模式（mixed mode）与 config dict 关键键

`TradingAgentsGraph.__init__` 会**独立**构造两个 LLM：`quick_thinking_llm` 与 `deep_thinking_llm`。
当 `quick_provider != deep_provider` 时即为"混合模式"（如：快思考用 DashScope、深思考用 Claude）。
它从 config dict 读取的键：

| 键 | 含义 |
|---|---|
| `llm_provider` | 兜底 provider（无 quick/deep 覆盖时） |
| `quick_provider` / `deep_provider` | 按角色覆盖 provider |
| `quick_think_llm` / `deep_think_llm` | 两个角色的模型名 |
| `backend_url` | 兜底 base_url |
| `quick_backend_url` / `deep_backend_url` | 按角色覆盖 base_url |
| `quick_api_key` / `deep_api_key` | 按角色覆盖 API Key |
| `quick_model_config` / `deep_model_config` | dict：`max_tokens` / `temperature` / `timeout` 等 |

`tradingagents/default_config.py` 提供这些键的兜底默认（`llm_provider="openai"`、`quick_think_llm="gpt-4o-mini"`、`deep_think_llm="o4-mini"`、`backend_url="https://api.openai.com/v1"`）。

---

### 7. 支持的 Provider 一览

| Provider | 配置/枚举值 | 环境变量 | 默认 base_url |
|---|---|---|---|
| OpenAI | `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | LangChain 默认 |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` |
| 阿里百炼 / Qwen | `dashscope` | `DASHSCOPE_API_KEY` | `…/compatible-mode/v1` |
| Google Gemini | `google` | `GOOGLE_API_KEY` | `…/v1beta` |
| 百度千帆 | `qianfan` | `QIANFAN_API_KEY` | `qianfan.baidubce.com/v2` |
| 智谱 GLM | `zhipu` | `ZHIPU_API_KEY` | `open.bigmodel.cn/api/paas/v4` |
| SiliconFlow | `siliconflow` | `SILICONFLOW_API_KEY` | 自定义 |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY`（回退 OPENAI） | 自定义 |
| Ollama | `ollama` | （本地，可无 key） | 自定义 |
| Claude Code（订阅） | `claude_code` | 无（OAuth） | Anthropic |
| Codex（订阅） | `codex` | 无（OAuth） | Codex |
| 自定义 OpenAI 兼容 | 任意 | `{PROVIDER}_API_KEY` / `CUSTOM_OPENAI_API_KEY` | 自定义 |

---

## 第二部分 · financial-report-llm-extractor 的现状

文件核心：`src/financial_report_llm_extractor/llm_transport.py`。

- **零运行时依赖**：`pyproject.toml` 里 `dependencies = []`，HTTP 全部用标准库 `urllib`，**不引入** langchain / openai / anthropic SDK。这是该项目刻意的设计原则。
- **配置对象 `LlmTransportConfig`（frozen dataclass，`llm_transport.py:77-100`）**：
  ```python
  provider: str
  model: str
  base_url: str
  api_key_env: str          # 关键：只存"环境变量名"，不存 key 本身
  timeout_seconds: float = 30
  max_retries: int = 0
  ```
- **加载方式 `LlmTransportConfig.from_json(path)`**：从 JSON 文件读 `provider`/`model`，
  其余字段缺省时由 `PROVIDER_DEFAULTS`（`llm_transport.py:42-74`）补齐。
- **Provider 归一化 `_normalize_provider()`（`llm_transport.py:111-121`）**：
  `openai`→`openai-compatible`、`google`→`gemini`、`codex`→`openai-codex`、`claude/claude-code`→`claude-code`。
- **`PROVIDER_DEFAULTS` 注册表**——注意它用的环境变量名**和 TradingAgents-CN 完全一致**：

  | provider | base_url | api_key_env | kind |
  |---|---|---|---|
  | `openai-compatible` | （需自填） | `OPENAI_API_KEY` | openai-compatible |
  | `deepseek` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | openai-compatible |
  | `ollama` | `http://localhost:11434/v1` | `OLLAMA_API_KEY`（非必需） | openai-compatible |
  | `gemini` | `…/v1beta` | `GEMINI_API_KEY` | gemini |
  | `openai-codex` | `chatgpt.com/backend-api/codex` | （OAuth） | codex-responses |
  | `claude-code` | `api.anthropic.com` | （OAuth） | anthropic-messages |

- **工厂 `create_llm_client(config)`（`llm_transport.py:322`）** 按 `kind` 返回 4 类客户端之一
  （`OpenAiCompatibleClient` / `GeminiGenerateContentClient` / `CodexResponsesClient` / `ClaudeCodeMessagesClient`），
  它们都实现 `LlmJsonClient` 协议（`llm_transport.py:142-154`），核心方法是
  `complete_json(*, system_prompt, user_payload) -> dict`。
- **Key 读取时机**：在调用时由 `os.environ[config.api_key_env]` 读取（`llm_transport.py:919`），不在启动时读。
- **调用点**：CLI `extract-llm` → `run_real_transport_probe()`，以及 `structured_sources/company_evaluation.py` 的
  LLM 补充步骤，都是通过 `LlmTransportConfig.from_json(path)` + `create_llm_client(config)` 进入。

---

## 第三部分 · 两个项目的对比与映射

| 维度 | TradingAgents-CN | financial-report-llm-extractor |
|---|---|---|
| 抽象层 | LangChain Chat 模型（重依赖） | 自研 stdlib HTTP 客户端（零依赖） |
| 配置载体 | MongoDB → 环境变量 → config dict | `LlmTransportConfig`（JSON 文件） |
| key 存放 | DB / 环境变量；运行时解析 | **只存环境变量名**，调用时读 |
| 构造入口 | `create_llm_by_provider(...)` | `create_llm_client(LlmTransportConfig)` |
| 调用接口 | LangChain `.invoke(messages)` | `client.complete_json(system_prompt, user_payload)` |
| **环境变量约定** | **`{PROVIDER}_API_KEY`** | **`{PROVIDER}_API_KEY`（一致！）** |

**核心结论**：两个项目天然共享同一套 `{PROVIDER_UPPER}_API_KEY` 环境变量约定。
TradingAgents-CN 已经把 DB 里配置的 key 桥接到了这些环境变量上 —— 也就是说，
当你在 TradingAgents-CN Web UI 配好 DeepSeek 后，`DEEPSEEK_API_KEY` 就在进程里了，
extractor 的 `LlmTransportConfig(api_key_env="DEEPSEEK_API_KEY")` 会**直接读到同一个 key**。
**缺口仅在于"用哪个 provider / 哪个 model / 哪个 base_url"这三项的传递**，
而不在 key 本身。

⚠️ **不要直接 import TradingAgents-CN 的 `create_llm_by_provider`** 到 extractor：那会把 langchain 整套依赖拖进来，
破坏 extractor 的"零依赖"原则。集成应停留在**配置层**（生成一个 `LlmTransportConfig`），而非实例层。

---

## 第三部分补充 · 现有集成已经是"同进程库调用"（重要事实）

排查后确认：本项目**已经存在**对 extractor 的集成，且是**同一个 Python 进程内的库调用**，不是独立服务/子进程。

- **代码入口**：`tradingagents/dataflows/financial_reports/adapter.py:35` 直接
  `from financial_report_llm_extractor.client import FinancialReportClient`，
  进程内调用 `client.get_extraction(...)`（`adapter.py:139-146`）；包没装则
  `ImportError → 优雅降级`（`adapter.py:41-42`）。
- **配置入口**：`tradingagents/dataflows/financial_reports/config.py` 从环境变量读出
  `FinancialReportClientConfig`（`enabled` / `cache_only` / `include_llm_supplement` /
  `llm_config_path` / `pdf_root` / `extractor_cache_root` / `allow_llm_models` 等）。
- **Docker `FINANCIAL_REPORT_EXTRACTOR_HOST_ROOT` 不是进程**，只是 bind-mount：把宿主机
  extractor 目录挂到容器 `/app/external/financial-report-llm-extractor`，供进程内的库读
  **文件**（`downloads/` 的 PDF、`tmp/.cache/` 缓存、LLM 配置 JSON）。
  见 `docker-compose.yml:24`、`.env.docker:362-404`、`.env.example:563-609`。
  开关 `FINANCIAL_REPORT_CLIENT_ENABLED`、`FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT` 默认均关闭。

**当前 LLM 配置链路（这正是要改的点）**：

```
FINANCIAL_REPORT_LLM_CONFIG_PATH (env)
  → FinancialReportClientConfig.llm_config_path           (config.py:40)
  → ExtractorConfig(llm_config_path=Path)                 (adapter.py:135)
  → FinancialReportClient → run_pipeline(llm_config_path) (client.py:454)
  → LlmTransportConfig.from_json(path)                    (extractor 内部)
```

即：**今天 extractor 的 LLM 走的是独立手写的 JSON 文件，与本项目的 MongoDB/环境变量配置无关。**
注入点是 `ExtractorConfig.llm_config_path`，**只收文件路径，不收内存对象**。

---

## 第四部分 · 集成方案（让 extractor 复用本项目 LLM 配置）

> **已定方向（用户确认）**：部署形态 = **同进程**；extractor **复用本项目的模型**；
> **不**往 extractor 的 `PROVIDER_DEFAULTS` 补国产 provider。
>
> **据此推荐：方案 B —— 由 TradingAgents-CN 临时生成 transport JSON，指给 `llm_config_path`，extractor 零改动。**
> 因为现有注入点 `ExtractorConfig.llm_config_path` 只收文件路径，方案 B 与之天然契合；
> "不补国产"也成立——见方案 B 的 provider 映射说明。

下面 3 个方案按"耦合度从低到高"排列。

### 方案 A（推荐基线）·共享环境变量 + extractor 增加 `from_env()` 构造器

**思路**：复用两者已有的 `{PROVIDER}_API_KEY` 约定，extractor 不连 DB、不加依赖。
只需让 extractor 能从一小撮环境变量里读出 provider/model/base_url（key 仍走既有的 `api_key_env`）。

extractor 侧新增（不破坏零依赖）：

```python
# src/financial_report_llm_extractor/llm_transport.py
@classmethod
def from_env(cls, prefix: str = "FRLE_LLM_") -> "LlmTransportConfig":
    """从环境变量构造（供 TradingAgents-CN 等外部配置源注入）。
    读取 FRLE_LLM_PROVIDER / _MODEL / _BASE_URL / _API_KEY_ENV，
    缺省项由 PROVIDER_DEFAULTS 补齐。"""
    provider = _normalize_provider(os.environ["FRLE_LLM_PROVIDER"])
    defaults = PROVIDER_DEFAULTS.get(provider)
    return cls(
        provider=provider,
        model=os.environ["FRLE_LLM_MODEL"],
        base_url=os.environ.get("FRLE_LLM_BASE_URL")
            or (defaults.base_url if defaults else ""),
        api_key_env=os.environ.get("FRLE_LLM_API_KEY_ENV")
            or (defaults.api_key_env if defaults else "OPENAI_API_KEY"),
    )
```

TradingAgents-CN 侧（Apache 区，放 `tradingagents/` 或 `scripts/`）增加一个导出器，把它已解析的 LLM 配置写进上述环境变量 + `{PROVIDER}_API_KEY`：

```python
# 伪代码：读取本项目的 quick/deep 配置，导出 extractor 所需 env
os.environ["FRLE_LLM_PROVIDER"]    = deep_provider          # 或 quick_provider
os.environ["FRLE_LLM_MODEL"]       = get_model("deep")      # TRADINGAGENTS_DEEP_MODEL
os.environ["FRLE_LLM_BASE_URL"]    = deep_backend_url
os.environ["FRLE_LLM_API_KEY_ENV"] = f"{deep_provider.upper()}_API_KEY"
# {PROVIDER}_API_KEY 已由 bridge_config_to_env() 设好
```

- ✅ extractor 保持零依赖；改动最小（一个 classmethod）。
- ✅ 同机/同环境部署最省事。
- ⚠️ 需要双方处在同一进程/环境（或由调用方 export 环境）。

### 方案 B（★推荐）·TradingAgents-CN 自动生成 transport JSON，指给 `llm_config_path`

**思路**：保持 MongoDB 为唯一数据源；由 TradingAgents-CN 把自己已解析的 LLM 配置写成一个
临时 transport JSON，再让现有的 `FinancialReportClientConfig.llm_config_path` 指向它。
**extractor 完全不用改**，沿用既有的 `LlmTransportConfig.from_json(path)`。

```python
# TradingAgents-CN 侧（Apache 区，建议放 tradingagents/dataflows/financial_reports/）
import json, tempfile
from app.core.config_bridge import get_provider_api_key, get_model  # 或从分析 config dict 取

def materialize_extractor_llm_config(provider: str, backend_url: str, role: str = "deep") -> str:
    """把本项目已解析的 LLM 配置落成 extractor 吃的 transport JSON，返回临时文件路径。"""
    model = get_model(role)                          # TRADINGAGENTS_DEEP_MODEL
    if provider.lower() in {"codex", "openai-codex"}:
        # 订阅类：token 由 extractor 从 ~/.codex/auth.json 自解析，无需 api_key_env / base_url
        cfg = {"provider": "codex", "model": model}
    else:
        # API-key 类（openai 兼容 / deepseek 等）
        cfg = {
            "provider": provider,                    # 如 deepseek / openai / dashscope
            "model": model,
            "base_url": backend_url,                 # 本项目 deep_backend_url（deepseek 可省）
            "api_key_env": f"{provider.upper()}_API_KEY",  # key 已由 bridge_config_to_env() 设好
        }
    fd, path = tempfile.mkstemp(prefix="frle_llm_", suffix=".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path
# 接入点：在 financial_reports/config.py 或 adapter 里，当未显式提供
# FINANCIAL_REPORT_LLM_CONFIG_PATH 时，回退到本函数生成的路径。
```

**为什么"不补国产 provider"成立**：extractor 的 `_normalize_provider` 不认识
`dashscope`/`qianfan`/`zhipu` → `resolve_provider_kind` 对未知 provider 返回
`"openai-compatible"`（`llm_transport.py:106-107`）→ 走 `OpenAiCompatibleClient`。
只要上面生成的 JSON **显式带了 `base_url` + `api_key_env`**，这些 OpenAI 兼容的国产厂家就能
直接跑通，**无需**往 extractor 的 `PROVIDER_DEFAULTS` 加条目。

**生成逻辑要按"鉴权方式"分两支**（仍全在本项目侧）：

- **API-key 类**（`openai`/OpenAI 兼容、`deepseek` 等）：JSON 带 `provider` + `model` +
  `base_url` + `api_key_env`；key 已由 `bridge_config_to_env()` 落到 `{PROVIDER}_API_KEY`。
  - DeepSeek 注意：本项目 base_url 是 `https://api.deepseek.com`（无 `/v1`），extractor 默认
    `…/v1`，二者 DeepSeek 都接受；生成时省略 base_url 让 extractor 用默认，或原样带上皆可。
- **订阅类（Codex）**：JSON 只需 `{"provider": "codex", "model": "<model>"}`——
  `_normalize_provider("codex") → "openai-codex"`（kind=`codex-responses`），
  **不需要 `api_key_env`**。extractor 的 `CodexResponsesClient`（功能可用，非诊断态）
  会自己从 `~/.codex/auth.json`（或 `$CODEX_HOME`）解析 OAuth token（`subscription_auth.py:121-124`）。
  - ⚠️ 含义：Codex 的**凭据是 OS 级共享**，不经本项目配置——本项目与 extractor **各自**读同一份
    `~/.codex/auth.json`。"复用本项目配置"对 Codex 实际只复用了"用 codex + 哪个 model"。
  - ⚠️ **Docker**：现有 `FINANCIAL_REPORT_EXTRACTOR_HOST_ROOT` 只挂数据目录，**不含** codex 凭据；
    容器内跑 codex 需额外把 `~/.codex` bind-mount 进去或设 `CODEX_HOME`（属部署配置，非代码改动）。
  - ⚠️ token 过期由 Codex CLI 负责刷新；extractor **只读不刷**，过期会直接报错。

  > 更新 (2026-05-28)：codex token 现由 TA-CN 在请求时解析（oauth_service，按用户，来自 MongoDB）并经
  > `create_financial_report_adapter(..., subscription_token=...)` → `ExtractorConfig.subscription_token` 注入
  > extractor，**无需**容器内有本地 `~/.codex` 登录。token 仅程序传参，不落盘/不进环境变量/不进缓存。
  > ⚠️ 并发限制：调用点从类级全局 `Toolkit._config` 读 `deep_api_key`，并发多用户 codex 分析下可能串 token；
  > 本特性 opt-in、工具定位单次运行，多用户并发 codex 场景请谨慎启用。

- ✅ 单一数据源（Mongo）；extractor 零改动；与现有 `llm_config_path` 注入点天然契合。
- ✅ 同进程下生成临时文件 + 指路径，开销可忽略；配置变更时重新生成即可。
- ✅ 已核实 extractor 端**无 provider 白名单/模型校验**会拦截未知 provider：
  `OpenAiCompatibleClient` POST 到 `{config.base_url}/chat/completions` 并用 `api_key_env`
  读 key（`llm_transport.py:123-124`），只要 JSON 带齐 base_url+api_key_env 即可。
- ⚠️ **唯一上游不支持的情形：纯 `anthropic`（API key）provider**。extractor 只有诊断用的
  OAuth `claude-code` 路径（`anthropic-messages` kind，被官方策略拦截），**没有 api-key 版
  Anthropic 客户端**。若本项目复用的深思考模型走纯 anthropic API key，则需在上游加一个客户端；
  走 dashscope/deepseek/openai/qwen/gemini 等则无需动上游。
- ℹ️ `FINANCIAL_REPORT_ALLOW_LLM_MODELS`（`config.py:38`）目前**仅被解析、未被 adapter 实际使用**
  （`adapter.py` 未引用 `config.allow_llm_models`），当前不构成 gate；若日后启用需保持对齐。

### 方案 C（不推荐）·extractor 直连 MongoDB 读配置

让 extractor 用 pymongo 直接读 `llm_providers`/`system_configs`。
**否决理由**：引入 `pymongo` 破坏 extractor 的零依赖原则，且要求 extractor 知道 DB 连接串，耦合过重。

### 推荐组合：A + B 并存

- 进程内嵌/同环境调用 → 走 **A**（`from_env()`，最轻）。
- 离线/独立批处理 → 走 **B**（导出 JSON，`from_json()` 不动）。
- 两者的"翻译表"是同一份 provider→(base_url, api_key_env) 映射，正好对应 extractor 既有的 `PROVIDER_DEFAULTS`，无需重复维护。

---

## 第五部分 · 改造落地清单（按已定方案 B）

已定：**同进程** + **复用本项目模型** + **不补国产 provider** → 走方案 B，**extractor 零改动**，
改动集中在 TradingAgents-CN 的 Apache 区。

**TradingAgents-CN 侧（Apache 区，勿放 `app/` 专有区）—— ✅ 已实现 (2026-05-27)：**
1. 在 `tradingagents/dataflows/financial_reports/` 新增 `materialize_extractor_llm_config(...)`：
   读取本项目已解析的 `provider` / `model`（`get_model("deep")`）/ `backend_url`，写成临时 transport JSON。
2. 在 `financial_reports/config.py` / `adapter.py` 的取值处加回退：当 `FINANCIAL_REPORT_LLM_CONFIG_PATH`
   未显式提供且 `include_llm_supplement=True` 时，调用①生成路径并传给 `ExtractorConfig(llm_config_path=...)`。
3. 复用 `config_bridge.get_provider_api_key()` / `get_model()`，避免重复解析逻辑；
   key 本身已由 `bridge_config_to_env()` 落到 `{PROVIDER}_API_KEY`，JSON 只需带 `api_key_env`。
4. 加单测（`tests/unit/test_financial_report_*.py` 已有同款结构可参照）：验证生成的 JSON 字段、
   未配置时的回退、以及 provider 为 dashscope 时落到 openai-compatible。
5. （可选）若日后启用 `FINANCIAL_REPORT_ALLOW_LLM_MODELS` 校验，把本项目实际复用的模型名纳入。

> 落地文件：`tradingagents/dataflows/financial_reports/llm_config_export.py`（生成 JSON）、`app/core/config_bridge.py::bridge_deep_llm_role_to_env`（桥接 deep provider/url）、`tradingagents/dataflows/financial_reports/adapter.py::create_financial_report_adapter`（无显式 path 时回退生成）。

**extractor 侧（`../financial-report-llm-extractor`，即"上游"）：** 本方案下**无需改动**。
目标复用的三类 provider 均已被上游支持：
- **OpenAI 兼容** → `openai-compatible` kind / `OpenAiCompatibleClient` ✅
- **DeepSeek** → `PROVIDER_DEFAULTS["deepseek"]`（openai-compatible，`DEEPSEEK_API_KEY`）✅
- **Codex 订阅** → `openai-codex` / `codex-responses` / `CodexResponsesClient`（功能可用）✅

**唯一例外**是纯 `anthropic`（API key）provider——上游只有诊断态 OAuth claude-code，缺 api-key 版
Anthropic 客户端；本次三类目标不涉及，无需处理。
（另：仅当未来想"无文件、纯内存注入"时，才考虑给 `ExtractorConfig` 增加
`llm_config: LlmTransportConfig | None` 字段——属可选增强，非必需。）

**部署（Docker）注意**——仅与 Codex 相关，属配置非代码：
- 容器内跑 Codex 需让 extractor 能读到 `~/.codex/auth.json`：bind-mount `~/.codex` 或设 `CODEX_HOME`。
- token 刷新依赖宿主机 Codex CLI；extractor 只读不刷，过期即报错。

**License 边界提醒**：桥接代码放 **Apache 2.0** 区（`tradingagents/`），不要塞进专有的 `app/`。

**已确认的决策**：
- 部署形态：**同进程**（adapter 以库形式 import extractor，`adapter.py:35`）。
- 模型来源：**复用本项目模型**（经 `get_model("deep")` 等）。
- 国产 provider：**不补** extractor 的 `PROVIDER_DEFAULTS`——靠生成 JSON 显式带 `base_url`+`api_key_env`，
  走 openai-compatible 兜底即可。

---

## 附：关键文件索引

| 作用 | 文件:行 |
|---|---|
| LLM 构造入口 | `tradingagents/graph/trading_graph.py:41`（`create_llm_by_provider`） |
| provider 分支 | `tradingagents/graph/trading_graph.py:41-177` |
| 适配器 | `tradingagents/llm_adapters/`（`openai_compatible_base.py` 等） |
| DB→env 桥接 | `app/core/config_bridge.py`（key:93,129；model:150-162；helper:526-545） |
| 数据模型 | `app/models/config.py`（`LLMProvider` / `LLMConfig`） |
| 兜底默认 | `tradingagents/default_config.py` |
| **现有 extractor 集成（同进程）** | `tradingagents/dataflows/financial_reports/adapter.py`（import:35；调用:139-146；llm_config_path:135） |
| **集成配置（读 env）** | `tradingagents/dataflows/financial_reports/config.py`（llm_config_path:40；allow_models:38） |
| **Docker 挂载/开关** | `docker-compose.yml:24`、`.env.docker:362-404`、`.env.example:563-609` |
| extractor 配置/工厂 | `../financial-report-llm-extractor/src/financial_report_llm_extractor/llm_transport.py:34-337` |
| extractor 公共 API | `../financial-report-llm-extractor/src/financial_report_llm_extractor/client.py`（ExtractorConfig:77；llm_config_path:454） |
