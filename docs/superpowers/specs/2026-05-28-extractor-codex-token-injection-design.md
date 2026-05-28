# 设计：向 financial-report-llm-extractor 注入 codex 订阅 token

> 状态：已批准设计（2026-05-28）。约定：标识符/代码英文，叙述中文。

## 目标

让 TradingAgents-CN 在用 **codex 订阅** 作为深度分析模型时，能把**每用户、每请求**解析出的 codex OAuth access token 注入给进程内的 `financial-report-llm-extractor`，使其 LLM 补充步骤真正可用——同时**完全保留 extractor 独立运行时用自己 `~/.codex/auth.json` 的能力**。

## 背景与问题

当前（PR #13 之后）codex 复用是**断的**：

- TradingAgents-CN 的 codex token 由 `oauth_service.resolve` 从 **MongoDB 按用户**读取并刷新，再由 `analysis_service._inject_oauth_token_if_needed` **每请求**写入 `config["deep_api_key"]`（`analysis_service.py:122,152`）。`config_bridge` 对 codex/claude_code **跳过** key 桥接（`config_bridge.py:126-131`）。
- extractor 的 `CodexResponsesClient` 只从**本机 `~/.codex/auth.json`**（或 `$CODEX_HOME`）解析 token（`subscription_auth.py:121-124`）。
- 两个来源不同 → Mongo 里的 token 到不了 extractor → 标准网页 OAuth 部署下 codex 补充步骤跑不通。

本设计补上这条：把 TA-CN 已解析的 token **以程序传参**方式注入 extractor，token 全程不落盘、不进环境变量、不进缓存。

claude_code、模型级 key（②）不在本设计范围。

## Token 流转（端到端）

```
analysis_service._inject_oauth_token_if_needed   （按用户、async）
  → config["deep_api_key"] = <codex oauth token>
  → TradingAgentsGraph(config) → Toolkit(config) → Toolkit._config   （类级全局）
       │
       ▼  （基本面工具调用时）
agent_utils._try_financial_report_client_section
  仅当 TRADINGAGENTS_DEEP_PROVIDER == "codex" 时：
    token = Toolkit._config.get("deep_api_key")
  → create_financial_report_adapter(frc_config, subscription_token=token)
  → FinancialReportAdapter 暂存 subscription_token
  → get_annual_report_data 构造 ExtractorConfig(subscription_token=token)
       │  （上游）逐层透传 optional kwarg
       ▼
  CodexResponsesClient: access_token = subscription_token or
        resolve_subscription_credentials("openai-codex").access_token
```

token 仅存在于内存中的 config 对象/调用参数里；**不写 transport JSON、不设环境变量**。

## 组件改动

### A. 上游 extractor（`../financial-report-llm-extractor`，独立 repo）

注入机制 = **方案 A：`ExtractorConfig` 新增可选字段 `subscription_token: str | None = None`**，逐层透传到 codex client。纯增量、向后兼容、不破坏独立用法。需要改的文件与穿透链：

1. `src/financial_report_llm_extractor/client.py`
   - `ExtractorConfig` 增加字段 `subscription_token: str | None = None`（frozen dataclass，默认 None）。
   - `FinancialReportClient.get_extraction`：把 `self.config.subscription_token` 传给 `run_pipeline(..., subscription_token=...)`。
2. `src/financial_report_llm_extractor/pipeline_core.py`
   - `run_pipeline(...)`（line 39）增加 `subscription_token: str | None = None`，并在调用 `run_company_evaluation(...)`（line 129）时透传。
3. `src/financial_report_llm_extractor/structured_sources/company_evaluation.py`
   - `run_company_evaluation(...)`（line 342）增加 `subscription_token: str | None = None`，透传给 `_run_llm_supplement_step`。
   - `_run_llm_supplement_step(...)`（line 450）增加该参数；调用 `create_llm_client(config, cache_root=..., subscription_token=...)`（line 494）时传入。
4. `src/financial_report_llm_extractor/llm_transport.py`
   - `create_llm_client(config, *, transport=None, cache_root=None, subscription_token=None)`（line 322）：把 `subscription_token` 传给会用到它的 client（codex / 后续 claude-code）；OpenAI 兼容 / gemini client 忽略它。
   - `CodexResponsesClient.__init__` 接收 `subscription_token`；在 line 588 处把
     `credentials = resolve_subscription_credentials("openai-codex"); _codex_headers(credentials.access_token)`
     改为 `access_token = subscription_token or resolve_subscription_credentials("openai-codex").access_token; _codex_headers(access_token)`。
   - account_id 仍由 `codex_chatgpt_account_id(access_token)`（line 751）从 token 派生，无需额外注入。

**precedence / 回退**：注入的 `subscription_token` 非空 → 用它并旁路文件解析；为 None → 走 `resolve_subscription_credentials` 读 `~/.codex/auth.json`/`$CODEX_HOME`（现状）。CLI（`extract-llm` / `run_real_transport_probe`）不设该字段 → 始终 None → standalone 行为零变化。

### B. TradingAgents-CN（Apache 区）

1. `tradingagents/dataflows/financial_reports/adapter.py`
   - `create_financial_report_adapter(config, subscription_token: str | None = None)`：把 token 透传给 `FinancialReportAdapter`。
   - `FinancialReportAdapter.__init__` 暂存 `self.subscription_token`；`get_annual_report_data` 构造 `ExtractorConfig(...)`（line 134-138）时加 `subscription_token=self.subscription_token`。
2. `tradingagents/agents/utils/agent_utils.py`
   - `_try_financial_report_client_section`：仅当 `os.getenv("TRADINGAGENTS_DEEP_PROVIDER") == "codex"` 时，取 `token = Toolkit._config.get("deep_api_key")`，传 `create_financial_report_adapter(frc_config, subscription_token=token)`；非 codex 时传 None。

## 向后兼容 / 独立运行保留

- extractor 所有新增参数都是 `... = None` 默认；现有调用方、CLI、测试不受影响。
- TA-CN 非 codex provider：`subscription_token` 为 None，行为同 PR #13；openai 兼容/deepseek 仍走 `api_key_env` 读 `{PROVIDER}_API_KEY`。

## 已知限制（已接受，写入文档）

调用点只能从**类级全局 `Toolkit._config`** 读 `deep_api_key`（Toolkit 不持有按实例 config，与该处既有的 report_collector 接线同源）。**并发多用户** codex 分析下，该全局槽可能持有另一个用户刚注入的 token → 某次运行可能用错用户的 codex 订阅。这是本仓库既有的全局 config 并发模型；本特性 opt-in、默认关闭，工具定位教育/研究、单次运行。**集成文档需明确标注此限制**，建议并发多用户 codex 场景谨慎启用。

## 安全

- token 不写 transport JSON、不设环境变量、不进 LLM 响应缓存（缓存 key 基于 system_prompt+payload+model，不含鉴权头）。
- 实现时**禁止把 token 打进日志**（现有 codex 路径如有 token 日志需一并核查）。

## 测试策略

**extractor（pytest，零依赖、frozen dataclass 风格）：**
- 单测 `CodexResponsesClient`：注入 `subscription_token` 时用注入值构造鉴权头、**不**调用文件解析；为 None 时回退文件解析（mock `resolve_subscription_credentials`）。
- 透传单测：`ExtractorConfig(subscription_token=...)` 经 `create_llm_client` 到达 codex client（可用 fake transport 断言请求头里的 Bearer token）。
- 回归：CLI / 现有 `ExtractorConfig` 构造（不传该字段）行为不变。

**TradingAgents-CN（pytest，`tests/unit/`）：**
- `create_financial_report_adapter(config, subscription_token=...)` → adapter 暂存并在 `ExtractorConfig` 里带上（沿用现有 `install_fake_extractor` monkeypatch 风格断言传入的 `ExtractorConfig.subscription_token`）。
- 调用点 gating：`TRADINGAGENTS_DEEP_PROVIDER=="codex"` 时从 `Toolkit._config["deep_api_key"]` 取 token 并传入；非 codex 时为 None。

## 仓库协作 / 分支

- extractor 改动在其**独立 repo** 上开分支 + 自测 + 提交（遵循其 AGENTS.md：零运行时依赖、stdlib、frozen dataclass）。
- TA-CN 改动依赖 PR #13 的 adapter/materialize 代码：在 `feat/financial-report-llm-config-reuse` 之上续做（栈式）或并入该 PR；具体在实现计划阶段定。
- TA-CN 侧通过已安装的 extractor 库消费新字段；需说明两端版本/安装协调（extractor 先行发布/安装含新字段的版本）。

## 范围外（Out of scope）

- claude_code 订阅（其 extractor 路径仅诊断态/被 Anthropic 拦，注入 token 也跑不通）。
- 模型级 key 复用（②，`TRADINGAGENTS_DEEP_API_KEY` 方案）——单独跟踪。
- 解决类级全局 config 的并发问题（按请求 config 透传）——更大重构，本设计仅记为限制。
