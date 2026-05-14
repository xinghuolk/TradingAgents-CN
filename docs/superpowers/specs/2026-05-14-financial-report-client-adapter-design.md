# FinancialReportClient Adapter 设计 Spec

> 日期：2026-05-14
> 状态：Draft for review
> 上游 contract：`/home/like/git/financial-report-llm-extractor/docs/superpowers/specs/2026-05-13-financial-report-client-productization-design.md`
> 阶段：Phase 1b，TradingAgents-CN 下游接入

## 目标

TradingAgents-CN 将 `financial-report-llm-extractor` 的 `FinancialReportClient` 作为年报类字段的权威来源。下游只 import 上游公开 client API，不读取 extractor 的 SQLite、JSON artifact、CLI 输出、`tmp/runs` 路径或内部 cache。

Phase 1b 首批同时接入：

- 基本面分析工具：在 `get_stock_fundamentals_unified` 中输出年报权威数据段落。
- 价值投资工具：在穿透回报率、现金健康度、健康评分计算前，用 Turtle 年报字段覆盖或补充 `financial_data`。

## Non-Goals

- 不在 TradingAgents-CN 内实现 Turtle 字段抽取、provider semantics、LLM supplement merge 或 source policy。
- 不让 TradingAgents-CN 读 extractor DB、JSON、CLI 或中间 artifact。
- 不把 report-collector 继续作为财务字段分析或补缺来源。
- 不在 Phase 1b 引入 HTTP sidecar、job queue、异步任务编排或 UI。
- 不在 TradingAgents-CN 内计算 extractor 的 clean coverage 或 provider trust policy。

## 上游 Contract 摘要

Phase 1b 只依赖以下上游 public API：

```python
from financial_report_llm_extractor.client import (
    ConfidenceLevel,
    ExtractionResult,
    ExtractorConfig,
    ExtractorError,
    FieldValue,
    FinancialReportClient,
    PdfQuery,
    RefreshPolicy,
    Staleness,
)
```

下游消费规则：

- `FieldValue.is_reliable` 是默认可参与结构化计算的唯一条件。
- `ConfidenceLevel.LLM_SUPPLEMENT` 必须经模型分级 policy 允许后才可进入计算。
- `result.staleness.is_missing` 必须跳过；`is_stale` 必须写入 warning/caveat，由下游策略决定是否继续。
- `raw_bucket` 只用于日志、caveat、retry 策略，不用于核心业务分支。
- `ExtractorError` 必须转换为 TradingAgents-CN 自己的日志和用户可读提示，不向 agent 暴露内部异常。

## 前置条件

1. TradingAgents-CN 运行环境升级到 Python 3.11+。上游 extractor `requires-python >=3.11`，Phase 1b 采用 in-process import，不设计 subprocess fallback。
2. `financial-report-llm-extractor` Phase 1a 已完成，`FinancialReportClient` 可安装并 import。
3. report-collector 保留财报搜索/下载能力，但默认停止财务分析与补缺调用。

## 模块结构

新增独立模块，避免把 Turtle contract 分散进现有大文件：

```text
tradingagents/dataflows/financial_reports/
  __init__.py
  adapter.py
  mapper.py
  policy.py
  formatter.py
```

### `adapter.py`

职责：

- 延迟 import `financial_report_llm_extractor.client`，避免未安装 extractor 时影响普通启动。
- 从配置和环境变量构造 `ExtractorConfig`。
- 提供 `get_annual_report_data(ticker, market, period_end)`。
- 统一处理 `Staleness` 与 `ExtractorError`。
- 调用 `pdf_resolver` 时复用 TradingAgents-CN 的财报目录或 report-collector 下载结果。

建议接口：

```python
@dataclass(frozen=True)
class FinancialReportAdapterResult:
    available: bool
    company: str
    market: str
    period_end: str
    extraction: ExtractionResult | None
    warnings: list[str]
    errors: list[str]


class FinancialReportAdapter:
    def get_annual_report_data(
        self,
        *,
        ticker: str,
        market: str,
        period_end: str | None,
    ) -> FinancialReportAdapterResult:
        ...
```

### `policy.py`

职责：

- 定义年报字段能否用于计算。
- 按 LLM model 分级判断 supplement 是否允许进入计算。
- 管理 fallback precedence。

默认策略：

```text
年报字段优先级：
1. FieldValue.is_reliable
2. trusted LLM supplement
3. AKShare fallback
4. 缺失/冲突/不可用进入 caveat
```

LLM 分级：

- Codex/GPT 类模型：可配置允许参与计算，必须输出 caveat。
- DeepSeek 类模型：默认只展示，不参与计算。
- 未知模型：只展示，不参与计算。

建议配置：

```text
FINANCIAL_REPORT_CLIENT_ENABLED=true
FINANCIAL_REPORT_ALLOW_LLM_MODELS=gpt-5.5,codex
FINANCIAL_REPORT_CACHE_ONLY=true
FINANCIAL_REPORT_FORCE_REFRESH=false
FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT=
FINANCIAL_REPORT_LLM_CONFIG_PATH=
```

### `mapper.py`

职责：

- 将 Turtle `FieldValue` 映射为 TradingAgents-CN 现有 `financial_data` dict。
- 标记 `_data_source` 与 `_supplemented_details`。
- 不复用 report-collector schema。

首批字段映射：

| TradingAgents-CN key | Turtle field |
| --- | --- |
| `net_profits[0]` | `net_profit` |
| `operating_cash_flow` | `operating_cash_flow` |
| `capex` | `capex` |
| `free_cash_flow` | `operating_cash_flow - capex`，仅当两者可用 |
| `total_equity` | `total_equity` |
| `cash_and_equivalents` | `cash_and_equivalents` 或现金类字段 |
| `interest_bearing_debt` | `interest_bearing_debt` |
| `current_assets` | `current_assets` |
| `current_liabilities` | `current_liabilities` |
| `current_ratio` | `current_assets / current_liabilities`，仅当分母大于 0 |
| `total_assets` | `total_assets` |
| `debt_ratio` | 可由负债/资产字段推导；若输入不足则保持现有 fallback |

进入计算的字段来源：

- reliable 字段：`_data_source[field] = "financial-report-client"`
- trusted LLM 字段：`_data_source[field] = "financial-report-client:llm:<model>"`
- unavailable / ambiguous：不写入计算值，只写 `_supplemented_details` caveat。

### `formatter.py`

职责：

- 生成基本面分析中的“年报权威数据”段落。
- 生成 LLM caveat、staleness warning、missing/unavailable/retry 说明。
- 格式化字段时避免暴露 raw bucket 作为业务概念。

输出段落应包含：

- extraction company / market / period_end / catalog_version。
- reliable 核心字段摘要。
- trusted LLM 字段列表及模型 caveat。
- stale/missing 状态提示。
- unresolved / terminal / source_unavailable 字段的简短说明。

## 接入点

### 基本面分析

接入 `tradingagents/agents/utils/agent_utils.py::get_stock_fundamentals_unified`。

设计：

- A 股、港股都先尝试读取 `FinancialReportAdapter`。
- 如果 adapter 返回 reliable 年报字段，在现有 AKShare/港股基本面段落之前插入“年报权威数据”段落。
- 如果 adapter missing/stale/error，写 warning 段落并继续现有数据源，不中断 agent。
- report-collector 的 PDF 财务摘要段落默认停止调用。

### 价值投资

接入 `tradingagents/tools/value_investment_tool.py`。

设计：

- `_fetch_financial_data_structured()` 获取基础结构后，调用 Turtle mapper 覆盖/补充年报字段。
- 行情、市值、分红、回购继续走现有 fetcher。
- 只有 mapper policy 允许的字段参与 `PenetratingYieldCalculator`、`CashHealthCalculator`、`HealthScoreCalculator`。
- 报告末尾追加数据来源说明和 LLM caveat。

## report-collector 边界

保留：

- `ReportCollectorClient`
- 财报搜索、下载、选择最新报告
- 作为 `pdf_resolver` 或 PDF 获取器

停止默认调用：

- report-collector 财务字段 mapping
- report-collector 对 AKShare 缺字段补缺
- report-collector PDF 财务摘要段落

实现策略：

- Phase 1b 不大删现有 report-collector 分析代码，避免与当前分支未提交改动冲突。
- 默认配置关闭 report-collector 分析路径。
- 后续 Turtle adapter 稳定后，再清理 legacy report-collector 分析代码。

## 配置

新增默认配置项，放入 `tradingagents/default_config.py`，并支持环境变量覆盖：

```python
"financial_report_client_enabled": os.getenv("FINANCIAL_REPORT_CLIENT_ENABLED", "false").lower() == "true",
"financial_report_cache_only": os.getenv("FINANCIAL_REPORT_CACHE_ONLY", "true").lower() == "true",
"financial_report_force_refresh": os.getenv("FINANCIAL_REPORT_FORCE_REFRESH", "false").lower() == "true",
"financial_report_allow_llm_models": os.getenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "gpt-5.5,codex"),
"financial_report_extractor_cache_root": os.getenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", ""),
"financial_report_llm_config_path": os.getenv("FINANCIAL_REPORT_LLM_CONFIG_PATH", ""),
```

默认 `financial_report_client_enabled=false`，保证未安装 extractor 的用户不受影响。

### LLM 配置边界

TradingAgents-CN 与 extractor 的 LLM 配置互相隔离：

- TradingAgents-CN 的 LLM 配置只服务于多智能体分析、报告生成、研究员辩论和最终投资建议。
- extractor 的 LLM 配置只服务于年报字段抽取、PDF evidence supplement 和 LLM supplement。
- TradingAgents-CN 不读取、不保存、不校验 extractor 的 API key。
- TradingAgents-CN 不解析 extractor 的 LLM config schema，只把 `FINANCIAL_REPORT_LLM_CONFIG_PATH` 传给 `FinancialReportClient`。
- extractor 的 API key 由 extractor config 内的 `api_key_env` 决定，例如 `FINANCIAL_REPORT_OPENAI_API_KEY`。
- `ExtractionResult.llm_provider` / `llm_model` 只作为结果 metadata，用于 caveat 和 LLM trust policy。

示例：

```text
TradingAgents-CN env:
  FINANCIAL_REPORT_LLM_CONFIG_PATH=/path/to/extractor_llm_config.json

extractor_llm_config.json:
  provider/model/base_url/api_key_env 由 extractor 自己解释
```

如果 extractor 因 LLM config 或 key 缺失失败，adapter 只捕获并转换为 warning/error，TradingAgents-CN 不接管 key 管理。

## 错误与降级

错误处理规则：

- extractor 未安装：adapter 返回 unavailable warning，现有链路继续。
- `ExtractorError(reason="unknown_field")`：记录 error，跳过对应映射。
- `staleness=MISSING`：不进入计算，报告提示缺少年报 extraction。
- `staleness=STALE`：默认可展示但不覆盖计算；若配置允许 stale，必须输出 caveat。
- `FORCE_REFRESH` 失败：记录 error，fallback 到现有 AKShare/非年报链路。

## 测试策略

单元测试优先，不依赖真实 extractor、网络或 LLM。

新增测试建议：

- `policy.py`：reliable 字段可计算；DeepSeek LLM 默认不可计算；Codex/GPT 可按配置计算。
- `mapper.py`：Turtle fields 正确覆盖 `financial_data`，并写 `_data_source`。
- `mapper.py`：unavailable/ambiguous 字段不参与计算。
- `formatter.py`：输出 reliable 字段、LLM caveat、stale/missing warning。
- `adapter.py`：extractor 未安装、ExtractorError、MISSING/STALE/FRESH 均可降级。
- `value_investment_tool.py`：启用 client 后，年报字段优先于 AKShare；report-collector 分析路径不再默认调用。
- `agent_utils.py`：fundamentals 输出包含年报权威数据段落。

代表性 fixture：

- CN `600519`
- HK `00001`
- HK USD/CNY issuer
- HK `gross_profit` non-reliable

## 验收标准

- TradingAgents-CN 不读取 extractor DB、JSON artifact、CLI 或 `tmp/runs`。
- 未安装 extractor 时，现有分析流程不崩溃。
- 启用 client 后，fundamentals 报告出现年报权威数据段落。
- 启用 client 后，value investment 计算优先使用 `is_reliable` 年报字段。
- DeepSeek LLM supplement 默认不进入计算。
- Codex/GPT LLM supplement 在配置允许时可进入计算，并输出 caveat。
- report-collector 仍可用于财报下载/选择，但默认不再参与财务字段分析或补缺。
- 现有 report-collector 未提交改动不被回滚或大规模重写。

## Open Decisions

1. `period_end` 默认值：是否由 TradingAgents-CN 根据当前日期推最近年报期，还是要求调用方显式传入。
2. PDF resolver：优先查本地下载目录，还是通过 report-collector 自动下载最新年报后返回路径。
3. Stale 数据默认是否允许参与计算。建议默认不参与，只展示。
