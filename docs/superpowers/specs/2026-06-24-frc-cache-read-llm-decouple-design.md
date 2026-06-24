# FRC 缓存读取与跑 LLM 解耦（include_llm 退化修复）

- 日期：2026-06-24
- 范围：单个 PR
- 生产代码边界：`tradingagents/dataflows/financial_reports/adapter.py`（Apache 2.0，唯一改动的生产文件）
- PR 文件范围：上述生产文件 + 单测 `tests/unit/test_financial_report_adapter.py` + 配套文档更正 `docs/analysis/00003`（执行时三者都不可漏）

## 背景

下游日常 backend 分析（CACHE_FIRST）读不到上游已归一的 LLM 字段（`dividends_paid`/`repurchase_of_stock`/`stock_based_compensation` 等的 `normalized_value`），导致 turtle `value_report` 缺分红/派息、`non_decisionable`。

**根因（已实测确认，纠正 `docs/analysis/00003 §2` 的错误诊断）**：不是缓存多层不同步——上游 `extracted.db` 里的值一直是对的（DB / `.cache/runs/evaluation.json` / `tmp/runs/evaluation.json` 三层一致，`dividends_paid normalized_value=929784589.68`）。真正根因在下游 `adapter.py:254`：

```python
include_llm = bool(self.config.include_llm_supplement and self.config.llm_config_path)
```

这个布尔同时控制两件本应独立的事：
1. **`:266` 读取过滤** `get_extraction(include_llm_supplement=include_llm)`：client 在 `include_llm_supplement=False` 时把 `llm_supplement_present` 字段构造成 `value=None`（上游 `client.py:672-686`，`reason="llm_supplement_filtered"`，纯读取层、不动 DB 实存值）。
2. **`:258` pdf_resolver** + **跑 pipeline**：提取新数据才需要。

**退化链**：日常路径若 deep-provider env（`TRADINGAGENTS_DEEP_PROVIDER/MODEL`）缺失 → `materialize_extractor_llm_config` 返回 None（`llm_config_export.py:37-41`）→ `config.llm_config_path` 空 → `include_llm=False` → **CACHE_FIRST 纯读 DB、根本不跑 LLM，却因为没 LLM 配置就把已存好的 LLM 字段过滤成 None**。

**实测佐证**：CACHE_FIRST（不 force_refresh、不传 codex token、不清缓存），仅设 deep-provider env 使 `include_llm` 为真 → `dividends_paid normalized_value=929784589.68` 立即读到。差别只在这一个布尔。

**部署事实**：代码默认 `cache_only=True`（走 CACHE_ONLY，安全），**但 `.env.example` / `docker-compose.yml` 设 `FINANCIAL_REPORT_CACHE_ONLY=false`**，实际部署跑 CACHE_FIRST——所以下文 DB-miss 抛错路径生产可达，不能靠默认 CACHE_ONLY 侥幸。

## 目标

把「读取已缓存的 LLM 字段」与「能否跑新 LLM 补充」解耦：CACHE 命中纯读 DB 的 LLM 字段不应依赖 `llm_config_path`。让日常 backend 分析（CACHE 命中）直接拿到 DB 里早已正确的 normalized 值，无需 force_refresh / codex token / 清缓存。

## 关键约束：上游 DB-miss 断言（不可绕过）

上游 `client.py:419-426`，**进入 pipeline 前**（CACHE_FIRST miss 或 FORCE_REFRESH）有硬断言：

```python
if include_llm_supplement and self.config.llm_config_path is None:
    raise ExtractorError(reason="llm_config_missing", ...)
```

含义：
- **CACHE 命中**：client `:409-417` 直接读 DB 返回，**不到断言**——`include_llm_supplement=True` + 无 `llm_config_path` 安全，读到 DB 的 LLM 字段。✅（正是本 PR 主目标）
- **CACHE miss / FORCE_REFRESH**：要跑 pipeline，`include_llm_supplement=True` + 无 `llm_config_path` → **抛 `llm_config_missing`**。需下游处理。

## 设计与语义（已与用户确认）

`adapter.py:254-306` 改动（一处文件，三处）：

```python
# (1) 能否跑新 LLM 补充：需要 LLM 配置
can_run_llm = bool(self.config.include_llm_supplement and self.config.llm_config_path)
extractor_config = ExtractorConfig(
    llm_config_path=_optional_path(self.config.llm_config_path),
    cache_root=_optional_path(self.config.extractor_cache_root),
    pdf_resolver=self.resolve_pdf if can_run_llm else None,            # 跑 pipeline 才需要
    subscription_token=self.subscription_token,
)
extraction = client.get_extraction(
    company=ticker, market=market, period_end=resolved_period_end,
    include_llm_supplement=self.config.include_llm_supplement,         # (2) 读取：仅看配置意图
    refresh_policy=self._refresh_policy(RefreshPolicy),
)
...
# 仅对 LLM/pipeline 相关、provider-only 可绕过的 reason 才降级重试
_LLM_RETRYABLE_REASONS = {"llm_config_missing", "pdf_not_found", "fetch_failed", "evaluate_failed"}

except ExtractorError as exc:
    reason = getattr(exc, "reason", "extractor_error")
    if self.config.include_llm_supplement and reason in _LLM_RETRYABLE_REASONS:   # (3)(4)
        try:
            extraction = client.get_extraction(..., include_llm_supplement=False, ...)
            return self._result_from_extraction(..., warnings=["llm_supplement_failed; retried provider-only: ..."])
        except ExtractorError as exc2:                                            # (5) 不吞第二次错误
            reason2 = getattr(exc2, "reason", "retry_failed")
            return FinancialReportAdapterResult(available=False,
                errors=[f"{reason}: {exc}", f"{reason2}: {exc2}"], ...)            # first + second 都暴露
    return FinancialReportAdapterResult(available=False, errors=[f"{reason}: {exc}"], ...)
```

改动职责：
1. **`pdf_resolver` 用 `can_run_llm`**：无 `llm_config_path` 跑不了新 LLM（保持现状）。
2. **`get_extraction(include_llm_supplement=self.config.include_llm_supplement)`**：读取仅看配置意图，不依赖 `llm_config_path`。CACHE 命中即读到 DB 的 LLM 字段。
3. **except 重试条件 `include_llm` → `self.config.include_llm_supplement`**：原 except 块（`adapter.py:275-306`）已有 provider-only 重试机制；改名后必须用读取意图作条件，否则 `llm_config_path` 空时 `can_run_llm=False` 会跳过重试、让 DB-miss 直接报错（比现状更糟）。
4. **重试白名单 `reason in _LLM_RETRYABLE_REASONS`**（review #2）：只对 LLM/pipeline 相关、provider-only 能绕过的 reason 降级；`unsupported_market`/`db_not_initialized`/`no_db_row`/`unknown_field` 等 provider-only 也救不了，不做无意义二次调用、直接返回原错误。`fetch_failed`/`evaluate_failed` 是上游 `client.py:472-475` run_pipeline 失败时动态生成的 reason。
5. **不隐藏第二次错误**（review #1）：原 `except Exception: pass` 会丢掉 provider-only 重试自身的真实失败（如第二次 `fetch_failed`/`db_not_initialized`），最终只返回首个 `llm_config_missing`、误导排查。改为捕获第二次 `ExtractorError` 并把 first+second 两个 reason 都放进 `errors`。

数据流：
| 场景 | 行为 |
|---|---|
| CACHE 命中 + config 有 llm | 读 DB（含 LLM 字段），不变 |
| CACHE 命中 + config 空 llm | **读 DB 的已缓存 LLM 字段**（修复主路径，不到断言） |
| CACHE miss / FORCE_REFRESH + config 有 llm | 跑 pipeline + LLM，不变 |
| CACHE miss / FORCE_REFRESH + config 空 llm | client 抛 `llm_config_missing` → except 捕获 → `include_llm_supplement=False` 重试 → **provider-only 成功 + warning** |
| `include_llm_supplement=False` | 读取不含 LLM 字段（不变） |

## 下游影响

- 日常 backend 分析（CACHE 命中、deep env 可能缺失的路径）从「LLM 字段被过滤成 None」变为「读到 DB 已缓存的 normalized 值」。turtle/穿透回报率两条路径（PR #34 已对接 normalized_value）随之拿到分红/派息/回购，相应解除 `non_decisionable`。
- CACHE miss + 无 llm_config：原 `include_llm=False` 直接 provider-only；新路径经「抛 `llm_config_missing` → 重试」。**provider-only 成功时结果一致**（含 provider 字段、无 LLM 字段），仅多一次重试 + warning；**provider-only 也失败时**返回 first(`llm_config_missing`)+second 双错误（review #1），不隐藏 provider-only 真实失败。
- provider 字段、有 config 时跑 LLM 的行为不变。`subscription_token`（codex）穿透不受影响（独立传参）。

## 非目标

- 不改上游 `financial-report-llm-extractor`（client 过滤逻辑与断言保持；退化在下游 except 兜底）。
- 不引入新配置项（复用 `include_llm_supplement`，避免语义重叠）。
- 不解决 deep-provider env 注入稳定性（方案 ④b，独立运维问题）。
- 不处理其他仍缺字段（`tax_rate`/折旧/受限现金，`00003 §3` 的独立 follow-up）。

## 测试

1. **更新现有测试** `tests/unit/test_financial_report_adapter.py::test_adapter_does_not_request_llm_supplement_without_llm_config`（:258-276）：config `include_llm_supplement=True` + `llm_config_path=""` 的断言从 `include_llm_supplement is False` 改为 **`is True`**；`pdf_resolver is None` 保持。（测试名也宜改为反映新语义，如 `..._reads_llm_fields_without_llm_config`。）
2. **新单测-跑 LLM 仍需 config**：config `include_llm_supplement=True` + `llm_config_path` 非空 → `pdf_resolver` 非 None。
3. **新单测-DB-miss 重试 provider-only**：FakeClient 首次 `get_extraction` 抛 `ExtractorError(reason="llm_config_missing")`、第二次（`include_llm_supplement=False`）返回 provider-only extraction → 断言 adapter 返回 `available=True` + warning 含 "retried provider-only"，且第二次调用 `include_llm_supplement=False`。
6. **新单测-重试也失败、暴露第二次错误**（review #1）：FakeClient 首次抛 `llm_config_missing`、第二次抛 `fetch_failed` → 断言 `available=False` 且 `errors` **同时含** `llm_config_missing` 与 `fetch_failed`（不能只返回首个，否则隐藏 provider-only 真实失败原因）。
7. **新单测-非可降级 reason 不重试**（review #2）：FakeClient 首次抛 `unsupported_market` → 断言**不发生第二次** `get_extraction` 调用、`errors` 含 `unsupported_market`。
4. **新单测-显式关闭**：config `include_llm_supplement=False` → `get_extraction` 收到 `include_llm_supplement=False`。
5. **integration（默认跳过）**：CACHE_FIRST 无 force_refresh、无 codex token、设 deep env 使 llm_config 生成→ 实则验证「命中读 LLM 字段」；另跑一次「不设任何 llm_config」确认 CACHE 命中仍读到（已预演）。

## 风险

- **except 兜底依赖**：CACHE-miss 修复完全靠 `adapter.py` 的 except 重试块。若该块未来被重构删除，DB-miss + 无 config 会变成硬报错——实现时在重试条件处加注释锁定该不变量。
- **行为变化可见性**：之前因 env 缺失「静默无 LLM 字段、available=True」的命中路径，现在会读出 LLM 字段——属预期修复，PR 说明需点出（避免被误认为引入新数据）。
- **配套**：更正 `docs/analysis/00003 §2` 的根因（不是缓存不同步，是 include_llm 退化 + DB-miss 断言）。
