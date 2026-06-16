# [Follow-up] HK 价值分析穿透回报率=0 — 分红未消费上游数据

- 日期：2026-06-16
- 状态：**Open**（待 follow-up，超出币种归一改造范围）
- 关联：分支 `feat/value-multi-source-currency-normalization`（币种归一改造）；记忆 `hk-penetrating-yield-zero-dividend-gap`
- 优先级：中（HK 链路已通、币种已正确，但核心价值信号失真）
- 说明：本仓 GitHub Issues 已禁用，以本文档作为 issue 记录。

## 现象

币种归一改造打通 HK 链路后，`get_value_investment_analysis('00001', 'HK')` 能出完整报告、币种正确（市值 2675 亿 HKD、无 0.9032 污染），**但穿透回报率 = 0.00%**。

## 根因

穿透回报率 = 分红收益率 + 回购收益率（`penetrating_yield.py`）：

1. **回购**：00001 上游 `repurchase_of_stock = None/0`，合法为 0。
2. **分红**：`DividendFetcher._fetch_hk_stock_dividend`（`tradingagents/dataflows/value_investment/dividend_fetcher.py`）走 `ak.stock_hk_dividend`，**对 00001 取不到历史分红记录** → `dividend_data.avg_payout_ratio_3y = 0` → 分红收益率 = 0。

**架构缺口**：穿透回报率的分红部分**只消费 `dividend_data`**（来自 `ak.stock_hk_dividend`），**不消费上游 `financial_data` 里已备齐的分红数据**：
- `dps = 1.602 HK$/股`
- `dividends_paid = -8518`（百万 HKD 口径已归一为元）
- `net_profit = 11841` → 可派生 payout ≈ 8518/11841 ≈ 0.72

即上游 financial-report-llm-extractor 已提取的分红字段，在穿透回报率计算链路里被旁路了。

## 建议方案（follow-up PR）

让 HK 分红在 `ak.stock_hk_dividend` 取不到时，**从上游 `financial_data` 派生兜底**：
- 优先：`dividends_paid / net_profit` 作为 payout_ratio；
- 或：`dps × 总股本 / 市值` 作为分红收益率（注意同币种 HKD、总股本对齐）。

落点候选：`_fetch_dividend_data_sync`（HK 分支补上游兜底），或 `PenetratingYieldCalculator` 增加 `financial_data` 分红回退。须保持币种一致（纳入主闸）。

## 非目标

- 不在币种归一 PR 内处理（范围隔离）。
- 不改 A 股分红逻辑。
