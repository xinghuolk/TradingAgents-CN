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

## 2. 待解决：缓存持久化（CACHE_FIRST 读不到 normalized）

**现象**：上游重跑 / 下游 force_refresh 后，`evaluation.json` 已含 `normalized_value`，但 backend 日常分析走 **CACHE_FIRST** 仍读到 `normalized_value=None`。

**根因**：上游 `financial_report_client` 有多层缓存，且存在两套独立 run 存储：

| 存储 | 路径 | 谁写 | 谁读 |
|---|---|---|---|
| client 提取缓存 | `tmp/.cache/runs/<company>_<period_end>_<market>/` | 下游 client 首次提取 | 下游 CACHE_FIRST |
| client DB 缓存 | `tmp/.cache/extracted.db` | 下游 client | 下游读取层（疑似） |
| 上游 operator 重跑 | `tmp/runs/<company>_<year>/` | 上游 `pipeline` | ❌ 下游不读 |

- 下游 client cache_root = `extractor_cache_root = .../tmp/.cache`（非上游 `tmp/runs`）。
- force_refresh 更新了 `.cache` 的 `evaluation.json`（实测 dividends_paid 已变 `llm_supplement_present`+`canonical_unit=CNY`），但 CACHE_FIRST 读取层（`.cache/extracted.db`）未同步 → 仍读旧值。
- force_refresh 重提取时还伴随 `bridge_config_to_env` 的 `MongoDB 数据库未初始化` error（独立进程无 DB 连接），可能影响写回。

**解决方向（三选一，待定）**：
1. 清下游 client 缓存（`tmp/.cache/runs/<company>_*` + `tmp/.cache/extracted.db`），让 client 用新版 extractor 重建。
2. 上游重跑直接写下游 `cache_root`（统一 run 存储）。
3. backend 配 `FINANCIAL_REPORT_FORCE_REFRESH=true` 跑一次预热后改回（注意全局影响 + 需 restart）。

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
