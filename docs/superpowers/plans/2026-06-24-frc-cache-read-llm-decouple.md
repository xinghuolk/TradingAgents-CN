# FRC 缓存读取/跑 LLM 解耦 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 CACHE_FIRST 命中时能读到 DB 里已缓存的上游 LLM 字段（normalized_value），解除「`llm_config_path` 空就把 LLM 字段过滤成 None」的退化。

**Architecture:** 在 `adapter.py::get_annual_report_data` 把一个 `include_llm` 布尔拆成两个语义：读取（`include_llm_supplement` 用配置意图、不依赖 llm_config_path）与跑新 LLM（`pdf_resolver`/`can_run_llm` 仍需 llm_config_path）；CACHE-miss/FORCE_REFRESH 触发上游 `llm_config_missing` 断言时，靠 except 块以 `include_llm_supplement=False` 降级重试 provider-only，且对可降级 reason 白名单限定、不吞第二次错误。

**Tech Stack:** Python，pytest（容器内 `docker exec -w /app tradingagents-backend python -m pytest`），monkeypatch fake extractor。

**Spec:** `docs/superpowers/specs/2026-06-24-frc-cache-read-llm-decouple-design.md`

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `tradingagents/dataflows/financial_reports/adapter.py` | `get_annual_report_data` 的读取/跑LLM解耦 + except 重试增强 + `_LLM_RETRYABLE_REASONS` 常量 | 改（唯一生产文件） |
| `tests/unit/test_financial_report_adapter.py` | 更新 1 个现有测试 + 新增 5 个 | 改 |
| `docs/analysis/00003_value_normalized_value_cache_and_debug_20260618.md` | 更正 §2 根因 | 改 |

> 约定：测试命令 `docker exec -w /app tradingagents-backend python -m pytest <path> -v`。

---

## Task 1: adapter 读取解耦 + except 重试增强

**Files:**
- Modify: `tradingagents/dataflows/financial_reports/adapter.py`（模块级加常量；`get_annual_report_data` 的 `:253-306` try/except）
- Test: `tests/unit/test_financial_report_adapter.py`

- [ ] **Step 1: 更新现有测试 + 写新测试**

`tests/unit/test_financial_report_adapter.py`：把 `test_adapter_does_not_request_llm_supplement_without_llm_config`（:258-276）整体替换为下面 6 个测试（第一个是它的语义反转 + 改名，其余新增）。复用文件已有的 `install_fake_extractor` / `FakeExtraction` / `FakeStaleness` / `FakeClient` / `FakeExtractorError` / `FinancialReportClientConfig`：

```python
def _cfg(include_llm=True, llm_config_path="", cache_only=True, force_refresh=False):
    return FinancialReportClientConfig(
        enabled=True, cache_only=cache_only, force_refresh=force_refresh,
        include_llm_supplement=include_llm, allow_llm_models=("codex",),
        extractor_cache_root="", llm_config_path=llm_config_path, pdf_root="",
    )


def test_reads_llm_fields_without_llm_config(monkeypatch):
    # CACHE 命中读取：include_llm_supplement 跟配置意图(True)，不被 llm_config_path 空拉成 False
    install_fake_extractor(monkeypatch)
    FakeClient.calls.clear()
    adapter = FinancialReportAdapter(config=_cfg(include_llm=True, llm_config_path=""))
    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")
    assert result.available is True
    assert FakeClient.calls[0]["include_llm_supplement"] is True      # 反转：原断言是 False
    assert FakeClient.last_config.kwargs["pdf_resolver"] is None       # 无 llm_config 不跑 LLM


def test_pdf_resolver_present_with_llm_config(monkeypatch):
    install_fake_extractor(monkeypatch)
    FakeClient.calls.clear()
    adapter = FinancialReportAdapter(config=_cfg(include_llm=True, llm_config_path="x"))
    adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")
    assert FakeClient.last_config.kwargs["pdf_resolver"] is not None   # 有 config 才跑 LLM


def test_explicit_disable_does_not_read_llm(monkeypatch):
    install_fake_extractor(monkeypatch)
    FakeClient.calls.clear()
    adapter = FinancialReportAdapter(config=_cfg(include_llm=False, llm_config_path=""))
    adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")
    assert FakeClient.calls[0]["include_llm_supplement"] is False


def test_db_miss_retries_provider_only(monkeypatch):
    # CACHE miss + 无 llm_config → 上游抛 llm_config_missing → 降级 provider-only 重试成功
    install_fake_extractor(monkeypatch)
    import financial_report_llm_extractor.client as fc
    seq = []
    def fake_get(self, **kwargs):
        seq.append(kwargs)
        if len(seq) == 1:
            raise fc.ExtractorError("llm_config_missing", "needs llm_config")
        return FakeExtraction(company=kwargs["company"], market=kwargs["market"],
                              period_end=kwargs["period_end"], staleness=FakeStaleness(), fields={})
    monkeypatch.setattr(fc.FinancialReportClient, "get_extraction", fake_get)
    adapter = FinancialReportAdapter(config=_cfg(include_llm=True, llm_config_path="", cache_only=False))
    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")
    assert result.available is True
    assert len(seq) == 2
    assert seq[1]["include_llm_supplement"] is False
    assert any("retried provider-only" in w for w in result.warnings)


def test_retry_failure_surfaces_both_errors(monkeypatch):
    # 首次 llm_config_missing、重试 provider-only 又抛 fetch_failed → errors 同时含两者
    install_fake_extractor(monkeypatch)
    import financial_report_llm_extractor.client as fc
    seq = []
    def fake_get(self, **kwargs):
        seq.append(kwargs)
        raise fc.ExtractorError("llm_config_missing" if len(seq) == 1 else "fetch_failed", "boom")
    monkeypatch.setattr(fc.FinancialReportClient, "get_extraction", fake_get)
    adapter = FinancialReportAdapter(config=_cfg(include_llm=True, llm_config_path="", cache_only=False))
    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")
    assert result.available is False
    joined = " ".join(result.errors)
    assert "llm_config_missing" in joined and "fetch_failed" in joined


def test_non_retryable_reason_does_not_retry(monkeypatch):
    # unsupported_market 不在白名单 → 不发生第二次调用
    install_fake_extractor(monkeypatch)
    import financial_report_llm_extractor.client as fc
    seq = []
    def fake_get(self, **kwargs):
        seq.append(kwargs)
        raise fc.ExtractorError("unsupported_market", "no")
    monkeypatch.setattr(fc.FinancialReportClient, "get_extraction", fake_get)
    adapter = FinancialReportAdapter(config=_cfg(include_llm=True, llm_config_path="", cache_only=False))
    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")
    assert result.available is False
    assert len(seq) == 1
    assert any("unsupported_market" in e for e in result.errors)
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec -w /app tradingagents-backend python -m pytest tests/unit/test_financial_report_adapter.py -k "reads_llm_fields or pdf_resolver_present or explicit_disable or db_miss or retry_failure or non_retryable" -v`
Expected: FAIL（现状 `include_llm` 在 llm_config 空时为 False → `reads_llm_fields` 断言 True 失败；db_miss 等重试行为未实现）

- [ ] **Step 3: 改实现**

`adapter.py` 模块级（靠近其它常量，如 `_load_extractor_client` 之前）新增：
```python
# provider-only 重试只对这些「跑 LLM/pipeline 相关、可绕过」的 reason 生效；
# unsupported_market / db_not_initialized / no_db_row / unknown_field 等 provider-only 也救不了。
_LLM_RETRYABLE_REASONS = {"llm_config_missing", "pdf_not_found", "fetch_failed", "evaluate_failed"}
```

`get_annual_report_data` 的 `:253-306` try/except 整体替换为：
```python
        try:
            # 能否跑新 LLM 补充：需要 LLM 配置（无 config 跑不了 LLM）
            can_run_llm = bool(self.config.include_llm_supplement and self.config.llm_config_path)
            extractor_config = ExtractorConfig(
                llm_config_path=_optional_path(self.config.llm_config_path),
                cache_root=_optional_path(self.config.extractor_cache_root),
                pdf_resolver=self.resolve_pdf if can_run_llm else None,
                subscription_token=self.subscription_token,
            )
            client = FinancialReportClient(config=extractor_config)
            extraction = client.get_extraction(
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                # 读取：仅看配置意图，不依赖 llm_config_path。CACHE 命中即读 DB 的 LLM 字段。
                include_llm_supplement=self.config.include_llm_supplement,
                refresh_policy=self._refresh_policy(RefreshPolicy),
            )
            return self._result_from_extraction(
                extraction=extraction,
                ticker=ticker,
                market=market,
                period_end=resolved_period_end,
            )
        except ExtractorError as exc:
            reason = getattr(exc, "reason", "extractor_error")
            # CACHE-miss/FORCE_REFRESH 下上游因无 llm_config 抛 llm_config_missing 等：
            # 用读取意图(而非 can_run_llm)作重试条件，否则 llm_config 空时不会重试 → DB-miss 直接报错。
            if self.config.include_llm_supplement and reason in _LLM_RETRYABLE_REASONS:
                try:
                    extraction = client.get_extraction(
                        company=ticker,
                        market=market,
                        period_end=resolved_period_end,
                        include_llm_supplement=False,
                        refresh_policy=self._refresh_policy(RefreshPolicy),
                    )
                    return self._result_from_extraction(
                        extraction=extraction,
                        ticker=ticker,
                        market=market,
                        period_end=resolved_period_end,
                        warnings=[
                            "llm_supplement_failed; retried provider-only: "
                            f"{reason}: {exc}"
                        ],
                    )
                except ExtractorError as exc2:
                    # 不吞第二次错误：provider-only 重试自身失败的真实 reason 也要暴露
                    reason2 = getattr(exc2, "reason", "retry_failed")
                    return FinancialReportAdapterResult(
                        available=False,
                        company=ticker,
                        market=market,
                        period_end=resolved_period_end,
                        extraction=None,
                        warnings=[],
                        errors=[f"{reason}: {exc}", f"{reason2}: {exc2}"],
                    )
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=[],
                errors=[f"{reason}: {exc}"],
            )
```

注意：原 `except Exception as exc:`（`adapter.py:307` 一带，非 ExtractorError 的兜底）保持不变。

- [ ] **Step 4: 运行确认通过**

Run: `docker exec -w /app tradingagents-backend python -m pytest tests/unit/test_financial_report_adapter.py -v`
Expected: PASS（全文件，含改名后的 6 个测试 + 既有 subscription_token/pdf_resolver 等测试无回归）

- [ ] **Step 5: 提交**

```bash
cd /home/like/mycode/finanice/TradingAgents-CN
git add tradingagents/dataflows/financial_reports/adapter.py tests/unit/test_financial_report_adapter.py
git commit -m "fix(frc): decouple cached-LLM-field read from llm_config; whitelist+surface retry

CACHE_FIRST 纯读 DB 不跑 LLM，却因 llm_config_path 空把已缓存 LLM 字段过滤成 None。
拆分：读取用 config.include_llm_supplement，跑新 LLM 用 can_run_llm(=and llm_config_path)。
CACHE-miss 触发上游 llm_config_missing 断言时经 except 降级 provider-only 重试，限定
可降级 reason 白名单、不吞第二次错误。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 更正配套文档 00003 §2

**Files:**
- Modify: `docs/analysis/00003_value_normalized_value_cache_and_debug_20260618.md`（§2）

- [ ] **Step 1: 改 §2 根因**

把 §2「待解决：缓存持久化（CACHE_FIRST 读不到 normalized）」整节的根因从「多层缓存不同步、DB 旧」更正为：**DB 一直是对的；根因是 `adapter.py` 的 `include_llm` 把「读取已缓存 LLM 字段」耦合到「能否跑新 LLM」，`llm_config_path` 空时 CACHE_FIRST 把 LLM 字段过滤成 None**。注明已由 PR（本计划）修复，并保留「两套 run 存储」「force_refresh 实测」作为历史背景但标注「非根因」。§4 调试指南保留。

- [ ] **Step 2: 提交**

```bash
cd /home/like/mycode/finanice/TradingAgents-CN
git add docs/analysis/00003_value_normalized_value_cache_and_debug_20260618.md
git commit -m "docs(value): correct 00003 root cause (include_llm decouple, not cache desync)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 自检结论（写后复核）

**1. Spec 覆盖**：spec 三处改动 → Task1 Step3（pdf_resolver=can_run_llm / get_extraction=config.include_llm_supplement / except 条件 config.include_llm_supplement）；review#2 白名单 → `_LLM_RETRYABLE_REASONS` + `test_non_retryable_reason`；review#1 不吞错误 → except2 分支 + `test_retry_failure_surfaces_both`；spec 测试 1-7 → Task1 的 6 个测试（合并 spec 测试 1/4 为改名测试与 explicit_disable；2→pdf_resolver_present；3→db_miss；6→retry_failure；7→non_retryable；5 integration 因依赖 live 上游缓存，列为手动/后续）；配套文档 → Task2。

**2. 占位符扫描**：无 TBD；每个 code step 给完整测试与实现代码；Task2 的「整节改写」给了明确的新根因表述，非占位。

**3. 类型一致性**：`_cfg(...)` helper、`_LLM_RETRYABLE_REASONS`、`FakeExtraction`/`FakeExtractorError`/`FakeClient.calls`/`last_config.kwargs` 全程一致；`can_run_llm`/`include_llm_supplement` 命名前后统一；except 用 `ExtractorError`（= 测试注入的 `FakeExtractorError`）一致。
