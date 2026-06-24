# FRC 缓存读取与跑 LLM 解耦（include_llm 退化修复）

- 日期：2026-06-24
- 范围：单个 PR
- 边界：`tradingagents/dataflows/financial_reports/adapter.py`（Apache 2.0）

## 背景

下游日常 backend 分析（CACHE_FIRST）读不到上游已归一的 LLM 字段（`dividends_paid`/`repurchase_of_stock`/`stock_based_compensation` 等的 `normalized_value`），导致 turtle `value_report` 缺分红/派息、`non_decisionable`。

**根因（已实测确认，纠正 `docs/analysis/00003 §2` 的错误诊断）**：不是缓存多层不同步——上游 `extracted.db` 里的值一直是对的（`dividends_paid normalized_value=929784589.68`，DB / `.cache/runs/evaluation.json` / `tmp/runs/evaluation.json` 三层一致）。真正根因在下游 `adapter.py:254`：

```python
include_llm = bool(self.config.include_llm_supplement and self.config.llm_config_path)
```

这个布尔同时控制两件本应独立的事：
1. **`:266` 读取过滤** `get_extraction(include_llm_supplement=include_llm)`：client 在 `include_llm_supplement=False` 时把所有 `llm_supplement_present` 字段主动替换成 None（上游 `client.py:672-686`，`reason="llm_supplement_filtered"`）。
2. **`:258` pdf_resolver** `pdf_resolver=self.resolve_pdf if include_llm else None`：跑 pipeline（提取新数据）才需要。

**退化链**：日常路径若 deep-provider env（`TRADINGAGENTS_DEEP_PROVIDER/MODEL`）缺失 → `materialize_extractor_llm_config` 返回 None（`llm_config_export.py:37-41`）→ `config.llm_config_path` 空 → `include_llm=False` → **CACHE_FIRST 纯读 DB、根本不跑 LLM，却因为没 LLM 配置就把已存好的 LLM 字段过滤成 None**。

**实测佐证**：CACHE_FIRST（不 force_refresh、不传 codex token、不清缓存），仅设 deep-provider env 使 `include_llm` 为真 → `dividends_paid normalized_value=929784589.68` 立即读到。差别只在 `include_llm` 这一个布尔。

## 目标

把「读取已缓存的 LLM 字段」与「能否跑新 LLM 补充」解耦：CACHE 命中纯读 DB 的 LLM 字段不应依赖 `llm_config_path`。让日常 backend 分析（CACHE 命中）直接拿到 DB 里早已正确的 normalized 值，无需 force_refresh / codex token / 清缓存。

## 设计与语义（已与用户确认）

`adapter.py:254-267` 把一个 `include_llm` 拆成两个语义清晰的判断：

```python
# 能否跑新 LLM 补充：需要 LLM 配置（无 config 跑不了 LLM）
can_run_llm = bool(self.config.include_llm_supplement and self.config.llm_config_path)
extractor_config = ExtractorConfig(
    llm_config_path=_optional_path(self.config.llm_config_path),
    cache_root=_optional_path(self.config.extractor_cache_root),
    pdf_resolver=self.resolve_pdf if can_run_llm else None,         # 跑 pipeline 才需要
    subscription_token=self.subscription_token,
)
extraction = client.get_extraction(
    company=ticker,
    market=market,
    period_end=resolved_period_end,
    include_llm_supplement=self.config.include_llm_supplement,      # 读取：仅看配置意图，不依赖 llm_config_path
    refresh_policy=self._refresh_policy(RefreshPolicy),
)
```

- **读取（`include_llm_supplement`）** = `self.config.include_llm_supplement`（默认 True，来自 `.env FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT`）。CACHE 命中纯读 DB，DB 有 LLM 字段就返回，不再被过滤。
- **跑新 LLM（`pdf_resolver`）** = `can_run_llm`（= `include_llm_supplement and llm_config_path`）。无 `llm_config_path` 仍跑不了 LLM 补充——保持现状。

语义对照：
| 配置 | 旧行为 | 新行为 |
|---|---|---|
| `include_llm_supplement=True` + `llm_config_path` 有 | 读 LLM 字段 + 可跑 LLM | 不变 |
| `include_llm_supplement=True` + `llm_config_path` 空 | **不读 LLM 字段**（过滤成 None） | **读 DB 已缓存的 LLM 字段**；CACHE miss 时跑 provider-only |
| `include_llm_supplement=False` | 不读 LLM 字段 | 不读 LLM 字段（不变） |

## 边界

CACHE miss（DB 无该 period → 要跑 pipeline）+ `llm_config_path` 空 → `pdf_resolver=None` 但 `include_llm_supplement=True`：

- 预期：client 跑 **provider-only** pipeline（无 PDF resolver → 跳过 LLM 补充），DB 写 provider 字段；读取层 `include_llm_supplement=True` 但该 LLM 字段 DB 无 → 返回 None。无 LLM 数据但不报错。
- **实现首步必须验证**：构造此组合（`include_llm_supplement=True` + `pdf_resolver=None` + CACHE miss）确认 client 不抛错。若 client 强制要求 `include_llm_supplement` 与 pdf_resolver 一致，则改为：仅在 CACHE_FIRST/CACHE_ONLY 路径解耦，FORCE_REFRESH 维持原 `include_llm`（见非目标/风险）。

## 下游影响

- 日常 backend 分析（CACHE 命中、deep env 可能缺失的路径）从「LLM 字段被过滤成 None」变为「读到 DB 已缓存的 normalized 值」。turtle/穿透回报率两条路径（已在 PR #34 对接 normalized_value）随之拿到分红/派息/回购，`non_decisionable` 因数据缺失的部分解除。
- provider 字段、跑新 LLM 的行为不变。

## 非目标

- 不改上游 `financial-report-llm-extractor`（client 过滤逻辑保持）。
- 不引入新配置项（复用 `include_llm_supplement`，避免语义重叠）。
- 不解决 deep-provider env 注入稳定性（方案 ④b，独立运维问题）。
- 不改 FORCE_REFRESH 语义（仍 `can_run_llm` 控制是否跑 LLM）。
- 不处理其他仍缺字段（`tax_rate`/折旧/受限现金，`00003 §3` 的独立 follow-up）。

## 测试

1. **单测（hermetic，mock client）**：mock `FinancialReportClient`，config `include_llm_supplement=True` + `llm_config_path=""`，断言 `get_extraction` 收到 `include_llm_supplement=True`、`ExtractorConfig.pdf_resolver is None`。
2. **单测-跑 LLM 仍需 config**：config `include_llm_supplement=True` + `llm_config_path` 非空 → `pdf_resolver` 非 None（`can_run_llm` 为真）。
3. **单测-显式关闭**：config `include_llm_supplement=False` → `get_extraction` 收到 `include_llm_supplement=False`。
4. **边界验证（实现首步，实跑）**：CACHE miss + `include_llm_supplement=True` + `pdf_resolver=None`，确认 client 不抛错（跑 provider-only）。
5. **integration（默认跳过）**：CACHE_FIRST 无 force_refresh、无 codex token → `get_annual_report_data('603345','CN','2024-12-31')` 的 `dividends_paid.normalized_value` 非空（已预演验证）。

## 风险

- **边界假设**：若 client 对 `include_llm_supplement=True` + `pdf_resolver=None` + CACHE miss 抛错，退化为「仅 CACHE_FIRST/CACHE_ONLY 解耦，FORCE_REFRESH 用原 `include_llm`」——边界验证（测试 4）先于实现确认。
- **行为变化可见性**：之前因 env 缺失而「静默无 LLM 字段」的路径，现在会读出 LLM 字段——属预期修复，但需在 PR 说明（避免被误认为引入新数据）。
