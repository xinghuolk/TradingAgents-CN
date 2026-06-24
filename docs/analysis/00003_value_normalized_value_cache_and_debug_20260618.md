# Value normalized_value 链路：缓存持久化问题 + 调试指南

- 日期：2026-06-18
- 状态：链路代码已通（PR #34）；缓存持久化为运维待办
- 关联：上游 `money-unit-normalization-funnel`、下游迁移指南 2026-06-18、本仓 PR #34
- 前序分析：`00002_hk_penetrating_yield_dividend_gap_20260616.md`

## 1. 链路现状（已打通）

上游对 money 字段单位归一化（提供 `normalized_value` + `canonical_unit`）后，下游两条消费路径均已对接（PR #34）：

| 路径 | 文件 | 用途 | 状态 |
|---|---|---|---|
| ① 穿透回报率工具 | `financial_reports/mapper.py::merge_financial_report_data` | `get_value_investment_analysis` | ✅ 优先 normalized_value + canonical_unit |
| ② turtle value_report | `value_investment/turtle/report_adapter.py::_adapt_value` | `value_analyst`（turtle） | ✅ 优先 normalized_value，跳过 raw unit 解析 |

**端到端验证（603345，force_refresh + codex 穿透）**：`dividends_paid`/`buyback_amount`/`dividend_payout_ratio_current_year`(=0.783) 全部 `reliability=reliable`，不再因 "unsupported unit 元/ones" 降级为 display_only。turtle 的 `payout_M` 核心链路打通。

## 2. 已修复：CACHE_FIRST 读不到 normalized（根因是 include_llm 退化，非缓存不同步）

> **更正**：本节早先诊断为「缓存多层不同步、DB 旧」是**错误的**。后续实测 + 上游代码核实证明：上游 DB 里的值一直是对的，根因在下游 adapter 的 `include_llm` 退化。已由 PR `feat/frc-cache-read-llm-decouple`（spec `docs/superpowers/specs/2026-06-24-frc-cache-read-llm-decouple-design.md`）修复。

**现象**：backend 日常分析走 CACHE_FIRST 读到 LLM 字段（`dividends_paid` 等）`normalized_value=None`。

**真正根因（已实测确认）**：下游 `adapter.py` 的 `include_llm = include_llm_supplement and llm_config_path` 把「读取已缓存 LLM 字段」耦合到「能否跑新 LLM」。CACHE_FIRST 纯读 DB、根本不跑 LLM，却因 deep-provider env 缺失 → `llm_config_path` 空 → `include_llm=False` → client 把 `llm_supplement_present` 字段过滤成 None（上游 `client.py:672-686`）。**DB 里的值一直是对的**（DB / `.cache/evaluation.json` / `tmp/runs/evaluation.json` 三层一致，`dividends_paid normalized_value=929784589.68`），是读取侧被过滤掉了。

**实测佐证**：CACHE_FIRST（不 force_refresh、不传 codex token、不清缓存），仅设 deep-provider env 使 `include_llm` 为真 → `dividends_paid normalized_value` 立即读到。

**修复**：解耦读取与跑 LLM——读取用 `config.include_llm_supplement`、跑新 LLM 用 `can_run_llm`（= `and llm_config_path`）；CACHE-miss 触发上游 `client.py:421` 的 `llm_config_missing` 断言时，经 adapter except 降级 provider-only 重试（限定 reason 白名单、不吞第二次错误）。

**历史背景（非根因，保留供参考）**：下游 client cache_root（`tmp/.cache`）与上游 operator 重跑（`tmp/runs/`）确实是两套独立 run 存储、不自动同步；force_refresh 会更新 `.cache/evaluation.json`。这些是真实的缓存机制，但**不是**本问题的根因——问题在 `include_llm` 过滤，与缓存是否同步无关。原列的「清缓存 / 统一 cache_root / force_refresh 预热」三方向因此**均不解决本问题**（详见 spec 的方案评估）。

> 注：force_refresh 重提取走 codex LLM，需 DB 里有可用 codex 订阅 token（见 §4.2）。

## 3. 其他仍缺失字段（独立 follow-up，非本次范围）

`TurtleFacts.status` 仍 `degraded`，因这些字段上游也未提取出有效值（与分红/派息无关）：
`tax_rate`（catalog 无，但有 `c_paid_for_taxes`）、`depreciation_amortization`（unresolved_conflict）、`restricted_cash`、`capitalized_interest`、`capitalized_rd` 等。各自需上游补抽或下游派生，单独跟进。

## 4. 调试指南（让后续调试更方便）

### 4.1 一键诊断脚本

`scripts/diagnose_value_extraction.py` 封装了「codex token 穿透 + force_refresh + 跑 FRC/turtle + 打印关键字段」，避免每次手写长命令：

```bash
# 看缓存现状（CACHE_FIRST，快，不跑 LLM）
docker exec -w /app tradingagents-backend python scripts/diagnose_value_extraction.py 603345 CN

# force_refresh 重提取（穿透 codex token 跑 LLM，慢）
docker exec -w /app tradingagents-backend python scripts/diagnose_value_extraction.py 603345 CN --force

# 跑 turtle facts，看各字段 reliability + status
docker exec -w /app tradingagents-backend python scripts/diagnose_value_extraction.py 603345 A --force --turtle
```

### 4.2 codex 订阅 token（穿透上游跑 LLM）

- DB 表 `user_oauth_credentials`，`provider=codex`。
- resolve：`oauth_service.resolve(collection, user_id, 'codex')`（自动刷新；refresh_token 一次性轮换，长期不用会失效需重新授权）。
- 穿透：`create_financial_report_adapter(config, subscription_token=token)` → 上游 extractor。
- 触发条件：`resolve_injected_codex_token` 要求 `TRADINGAGENTS_DEEP_PROVIDER=codex`；`materialize_extractor_llm_config` 要求 `TRADINGAGENTS_DEEP_PROVIDER/MODEL` env。
- 定时刷新保活：PR #33 的 `oauth_token_refresh` job（每天扫描，防 refresh_token 闲置过期）。

### 4.3 缓存清理（强制下游重建）

```bash
# 清单个标的的 client 缓存
rm -rf /home/like/git/financial-report-llm-extractor/tmp/.cache/runs/603345_*_CN
# 清 client DB 缓存（影响全部，谨慎）
rm /home/like/git/financial-report-llm-extractor/tmp/.cache/extracted.db
```

### 4.4 字段消费契约（排查"字段缺失/错值"时先看这里）

- **金额**：读 `field.normalized_value`（已归一同币种绝对值，元），**不要**读 raw `field.value`（可能万元/千元）。
- **币种**：读 `field.canonical_unit`（标准 CNY/HKD/USD），**不要**读 `field.currency`（可能"人民币"）。
- **None 处理**：`normalized_value=None` → 不参与计算（text/无值/不可归一）。
- **display_only 判定**：上游 `is_reliable=False` + `raw_bucket` → policy 判 display_only。归一后单位被支持 → `is_reliable=True`。
- **两条路径都要查**：穿透回报率工具走 mapper，turtle value_report 走 report_adapter——同一字段问题需在两处确认。

### 4.5 环境注意

- 代码改 `tradingagents/`、`app/` 通过 docker 挂载实时生效，**不需 rebuild**；让运行中的 backend 用新代码需 `docker restart tradingagents-backend`（非 rebuild）。
- 容器内 pytest 是临时 `pip install`，重启后需重装。
- `docker exec` 独立进程**没有** config_bridge 注入的 LLM env / DB 连接——跑 LLM 字段需脚本内 `bridge_config_to_env()` + 设 deep provider env（诊断脚本已封装）。
