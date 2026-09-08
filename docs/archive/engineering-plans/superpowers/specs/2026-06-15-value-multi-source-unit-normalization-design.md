# Value 分析多源取数复用 + 币种/单位归一

- 日期：2026-06-15
- 范围：单个 PR
- 边界：`tradingagents/tools/value_investment_tool.py`、`tradingagents/dataflows/value_investment/`、`tradingagents/dataflows/financial_reports/mapper.py`、`tradingagents/dataflows/providers/china/tushare.py`（Apache 2.0）

## 背景

上游 `financial-report-llm-extractor` 已把 00001.HK 的价值投资字段备齐（上游项目文档 `/home/like/git/financial-report-llm-extractor/docs/company-analysis/00001_hk_live_value_gap_status_20260612.md`：57/68 present），但下游 `get_value_investment_analysis` 消费不到。2026-06-15 docker `tradingagents-backend` 容器内实测确认根因：

1. **value 工具私有另起炉灶**：5 个取数函数全部硬编码 `if market != "A"` 挡掉 HK——`_fetch_financial_data_structured:291`、`_fetch_market_data_structured:561`、`_get_industry_dynamic:623`、`_fetch_dividend_data_sync:681`、`_fetch_buyback_data_sync:803`。该工具 0 引用公共统一层。
2. **A 股行情接口失效**：`_fetch_market_data_structured` 直连 `ak.stock_individual_info_em` / `ak.stock_zh_a_spot_em`，二者当前 `RemoteDisconnected`（域名本身可达，接口级故障）→ 600519 财报四表成功、卡在市值。
3. **公共层本可用**：实测 `get_hk_stock_data_unified('0001.HK')` 正常返回 00001.HK 行情。

### 实测确立的单位/币种事实（设计基础）

- **上游 `field.value` 已是「元」（raw）且自带币种**：实测 00001.HK 通路 B（`financial_report_client` 包）——`net_profit=11,841,000,000`、`operating_cash_flow=62,567,000,000`、`cash=143,748,000,000`，每个 field 带 `unit='raw'`、`currency='HKD'`。上游 markdown 的"百万 HKD"只是展示单位。**上游金额不需要数量级缩放。**
- **A 股私有 akshare 财务 = 元 / CNY**（实测 600519 CFO≈2.69e10 元）。上游与 A 股财务同数量级（元），差异只在币种（HKD vs CNY）。
- **两条上游通路**（底层同一 extractor）：通路 B（`apply_financial_report_client_data:960` → `financial_reports/mapper.py::merge_financial_report_data`，**无条件**，当前唯一生效）；通路 A（`_supplement_with_report_collector:957` → `value_investment/report_data_mapper.py::merge_financial_data`，有条件，REST 服务 `192.168.10.250:8001` 当前 `available=False` 不触发，单位待实测）。

### 真实风险 = 币种，不是数量级

上游金额已是元、与 A 股同数量级，唯一会错算的是 **HKD vs CNY 混用**：`penetrating_yield=(分红+回购)/market_cap`、`fcf_yield=fcf/market_cap` 的分子（财务 HKD）与分母（市值）若币种不一致（AKShare HK 给 CNY 折算值，PR #23 实证 00001 五簇比率=CNY/HKD 0.9032）→ 结果被 0.9032 污染。**唯一真实数量级缩放**：A 股市值 Tushare `total_mv`（万元）→ 元 ×1e4。

## 目标

1. 按市场把取数委派到正确的源（行情/市值/行业复用公共层与现有 provider；A 股财务保留私有 akshare；HK 财务绝对值走上游 `financial_report_client`；分红/回购改用 value 子包 + 上游回购）。
2. 建立**币种归一边界**：金额标币种（优先源自带 `currency`），**在进入 calculator 前统一校验四个数据 dict 同币种**，跨币种拒绝；HK 市值/分红强制 HKD 源。数量级仅 A 股市值 ×1e4。
3. 顺带修复阻碍正确性的现有 bug：growth 双重 ×100、缺失当 0、字段名不一致、HK 回购无落点。

## 设计与语义（已与用户确认 + 逐条代码核实）

### 数据来源归属矩阵

| 类别 | A 股来源 | HK 来源 | 归属 |
|---|---|---|---|
| 行情/市值 | TushareProvider 同步单股 `daily_basic`(万元→元, CNY) | yfinance `info['marketCap']`(元, HKD) | 复用 provider |
| 基础信息/行业 | `get_china_stock_info_unified` | yfinance `get_hk_stock_info`(akshare HK info 无 industry) | 复用公共层 |
| 结构化财务（绝对值） | **保留私有 akshare**(元/CNY) | **上游 `financial_report_client`**(元/HKD, 通路 B) | A 股私有 / HK 上游 |
| 财务比率 | 私有 akshare 派生 | 上游派生(mapper.py:138-191) | 口径见下 |
| 分红 | `DividendFetcher`(A) | `DividendFetcher._fetch_hk_stock_dividend`(HKD) | 改用子包 |
| 回购 | `BuybackFetcher`(A) | 子包返 0 → **上游 `repurchase_of_stock`**(见 HK 回购落点) | 子包(A) + 上游(HK) |
| 价值计算 | 纯 value 逻辑 | 纯 value 逻辑 | 自有，不动 |

**反直觉点**：A 股结构化财务**不复用**公共 unified 层（只返回格式化字符串不可数值计算）；私有 akshare 财报接口实测正常，保留（仅改缺失语义 + 加币种标签）。

### 币种校验：两道闸门（核实 #1）

`PenetratingYieldCalculator.calculate_full_analysis` 分别接收 `financial_data`/`dividend_data`/`buyback_data`/`market_data` 四个**独立 dict**（penetrating_yield.py:206/228/231），它们**从不 merge** → 仅在 merge 层校验币种**挡不住** `financial_data(HKD)` 与 `market_data(CNY)` 混用。故设两道闸门：

- **主闸（必须，新增）**：`get_value_investment_analysis` 在调用 `py_calculator.calculate_full_analysis(...)`（value_investment_tool.py:998）与 `cash_calculator.calculate_from_financial_data(...)`（:1015）**之前**，调 `assert_consistent_currency(financial_data, market_data, buyback_data)`（凡含金额的 dict；`dividend_data` 只含每股值无顶层金额，**排除**），币种不一致则**中止分析并返回明确错误**（不出报告）。这是唯一能拦截跨 dict 混币的位置。
- **空币种语义（必须定义，N2）**：**含金额且非空**的 dict 必须带 `_currency`——缺失视为**配置错误，拒绝并明确报错**（不静默放行，否则污染漏网）；**全空 / 无金额**的 dict（如上游不可用时的空骨架）跳过校验。配套要求：`_fetch_market_data_structured` / `_fetch_financial_data_structured` 在**所有成功返回路径**（含 `:415 except` 分支兜底后）都必须标 `_currency`，否则主闸不可靠。
- **次闸**：两个 merge 入口（`merge_financial_data`、`merge_financial_report_data`）`assert_same_currency(existing, incoming)`，拦截 `financial_data` 内部多源混币。

### 逐字段 canonical 口径表（核实 #3）

| 字段 | canonical 口径 | 依据 |
|---|---|---|
| 所有金额（OCF/capex/cash/债务/market_cap/buyback…） | **元** + `_currency` | 上游/akshare 已是元 |
| `roe_avg_3y` | **百分数**（18.0） | `health_score.py:107` `roe>=20` |
| `debt_ratio` | **小数**（0.62） | `health_score` 直接用；akshare `/100` |
| `current_ratio` | 倍数（7.06） | 现状 |
| `revenue_growth_3y` / `profit_growth_3y` | **小数**（0.12 表示 12%） | `health_score.py:232/247` 对其 `*100`；内部计算 `:213/227` 也产小数 |
| `penetrating_yield` / `dividend_yield` / `fcf_yield` | 百分数 | `health_score.py:313/328` |

**修复**：`report_data_mapper.py:181/185` 现把 `revenue_growth_3y/profit_growth_3y` 写成**百分数**（`*100`），与 health_score 期望的小数冲突（双重 ×100）→ **去掉该 `*100`**，统一为小数。归一层**只作用于金额与币种，不触碰比率**（比率口径由各产出点遵守本表）。

> **定性（N5）**：此双重 ×100 **当前不可触发**——通路 A（report-collector，唯一产百分数 growth 的路径）当前 `available=False`；A 股 akshare 路径从不设置 growth（`value_investment_tool.py:271-272` 初始化 None 后无赋值，由 health_score 内部自算为小数）。故属**启用通路 A 前必须先修的潜在 bug**，非"当前报告数字已被污染"。

### HK 回购落点（核实 #2）

实测上游 extraction **有** `repurchase_of_stock` field，但 FRC `FIELD_TO_KEY`(mapper.py:19-34) **未映射**它 → 即使有值也进不了 `financial_data`，而 `PenetratingYield` 只读 `buyback_data.total_cancelled_amount`（独立 dict，penetrating_yield.py:228）。故 HK 回购"由上游补"当前**无代码落点**。

> **运行时假设（N4）**：`repurchase_of_stock` 在本仓代码中**仅** turtle 适配器（`turtle/report_adapter.py:31` 映射 `"repurchase_of_stock":"buyback_amount"`）引用，不在 `CORE_FIELDS`/`FIELD_TO_KEY`，无 catalog/schema 保证上游一定 emit。"上游会输出该字段"是**单次容器实测**结论（且本次 value=None）。**实现首步须在容器内确认 `repurchase_of_stock` 出现在 `extraction.fields`**，否则此落点为死代码；字段名以 turtle 适配器同名 key 为依据。

落点设计：

1. **FRC mapper 导出**：在 `merge_financial_report_data` 把 `repurchase_of_stock`（及 `buyback_cancellation_progress` 文本）从 `extraction.fields` 读出，单独写入 `financial_data['repurchase_of_stock']`（带币种 HKD），**不**进 `FIELD_TO_KEY` 的金额派生链（非资产负债/损益科目，不参与 `_derive_*`）。
2. **主工具组装（无条件覆盖，N3）**：HK 路径下，`get_value_investment_analysis` 在 `buyback_data` 构造后（:991 之后、:998 PY 消费前），**无条件**用 `financial_data['repurchase_of_stock']` 覆盖 `buyback_data['total_cancelled_amount']`——值为 `None` 则**不覆盖**（保持子包返回的 0）；为数值（含 0）则覆盖。**不**依赖"子包回购为空"判定（HK 子包返回的是 `total_cancelled_amount=0` 的非空 dict，:804-809，"为空"语义会误判）。
3. **None vs 0**：实测 00001 `repurchase_of_stock=None`（缺失，supplement 未命中），**不是** 0。`None`→不覆盖（视为缺失）；明确披露 `0`→覆盖为 0（合法无回购）。**禁止把 None 当 0。**
4. **币种**：回购金额 HKD，与 `market_cap`(HKD) 一致，纳入主闸校验。

### 缺失语义：缺失=None，披露0=0.0（核实 #4，修正"A 股不动"）

现有 A 股 `_fetch_financial_data_structured` 把**源缺失**当 0：`short_term_debt/long_term_debt/bonds_payable/一年内到期负债` `safe_float(...,0)`（:386-395）、`capex` 缺失=0（:484）、`interest_expense` 失败=0（:454）。这让现金健康度偏乐观，且与缺失语义冲突。**本 PR 必须修正**（不再属"A 股逻辑不动"）：

- 上述字段 `safe_float(..., None)` —— 源缺失返回 `None`，不臆造 0。
- `capex` 缺失 → None（`cash_health.py:178-180` 已有 `CFO-FCF` 兜底）。
- 这与 HK（上游 absence-zero 由上游裁决，下游不臆造）口径一致。

> **🔴 必须同步重写 `interest_bearing_debt` 累加（否则改 None 即崩）**：现状 `value_investment_tool.py:398-403` 用裸 `+`（`short_term_debt + long_term_debt + bonds_payable + one_year_debt`），任一为 None → `TypeError`，被 `:415 except` 吞掉导致**整段资产负债表静默全失败**。必须改为过滤 None 后求和：
> ```python
> debt_parts = [data['short_term_debt'], data['long_term_debt'], bonds_payable, one_year_debt]
> present = [v for v in debt_parts if v is not None]
> data['interest_bearing_debt'] = sum(present) if present else None
> if 0 < len(present) < 4:   # 部分缺失，附口径偏差 caveat
>     data.setdefault('_caveats', []).append('interest_bearing_debt: 部分债务分项缺失，合计仅含已披露项')
> ```
> **并同步修 `:413` 日志行**（现 `data.get('interest_bearing_debt',0)/1e8:.2f` 与 `current_ratio','N/A'):.2f` 在 None 时崩）→ 改为 None-safe 格式化（先判 None 再格式化，或用 `{x if x is not None else 'N/A'}` 不带 `:.2f`）。

### 字段白名单并集 + 字段名统一（核实 #6）

归一/币种标记的金额白名单 = 三处真实字段名的**并集**，并**统一字段名**：

- FRC `FIELD_TO_KEY` 目标 keys（mapper.py:20-34）：`net_profits`/`operating_cash_flow`/`capex`/`cash_and_equivalents`/`current_assets`/`current_liabilities`/`total_assets`/`total_liabilities`/`equity_attributable_to_owners`/`minority_int`/`st_borr`/`lt_borr`/`bond_payable`，及派生 `total_equity`/`interest_bearing_debt`/`free_cash_flow`。
- `report_data_mapper.fields_to_merge`（:212-233）同名金额字段。
- calculator 实际读取：`cash_health.py:190-195` 回退读 `short_term_debt`/`long_term_debt`/**`bonds_payable`**/`current_portion_of_long_term_debt`；`penetrating_yield` 读 `net_profits`/`free_cash_flow` + `buyback_data.total_cancelled_amount`/`latest_year_amount`；`health_score` 读 `revenue`。

**字段名冲突**：`cash_health.py:193` 读 `bonds_payable`（带 s），FRC 写 `bond_payable`（无 s）→ 统一为 **`bond_payable`**，并修 `cash_health.py:193` 的回退读法（或在归一层提供别名归并）。白名单需覆盖 `st_borr/lt_borr` 等 FRC 派生输入。归一**只作用于白名单 + 跳过所有 `_` 前缀键**。

> **触发场景（N6）**：`cash_health.py:190-202` 的回退累加（读 `bond_payable`）**仅当 `interest_bearing_debt is None` 时**才执行（:188）。A 股主路径已在 `value_investment_tool.py:398` 预算 `interest_bearing_debt`（非 None），**不走**回退；只有 HK/FRC 未派生出 `interest_bearing_debt` 时才走回退读 `bond_payable`。故此改名实际影响 HK/FRC 缺 ibd 派生的场景，A 股主路径不受影响——测试据此设计，勿误判为影响 A 股。

### 结构化市值来源（核实 #5）

- **A 股**：给 `TushareProvider` 新增**同步单股**方法（如 `get_market_snapshot_sync(ts_code)`），复用现有 `connect_sync()`(:88) 设置的 `self.api`，调 `self.api.daily_basic(ts_code=..., fields='close,total_mv,total_share')`，`total_mv` 原生**万元** ×1e4 → 元。**不**裸 `ts.pro_api()`（绕开 token/DB/env/连接状态）、**不**复用 async 全市场 `get_daily_basic`(:597)、**不**读 MongoDB `stock_basic_info.total_mv`（那里是亿元）。
- **HK**：复用 `providers/hk/hk_stock.py::get_hk_stock_info`（`:136-144` 用 `ticker.info['marketCap']`，HKD 元，直通）。**不用** `fast_info`（项目 0 处使用）。
- 实现首步在容器内验证两者可用非空；Tushare 不可用回退 yfinance(`600519.SS`)。

### 分红/回购子包 + HK 行业

- 分红/回购改用 `DividendFetcher`(A+HK) / `BuybackFetcher`(A)，替换私有 `_fetch_*_sync`。两子包为 **async**，主函数是同步 `@tool`，须按 CLAUDE.md `asyncio.to_thread` 模式包装，确认 graph 调用栈无运行中 loop。HK 回购走上游（见上）。
- **HK 行业**走 yfinance（`get_hk_stock_info` 的 `industry`）：akshare HK info（`improved_hk.py:763-776`）无 `industry`，仅 yfinance 路径（`hk_stock.py:148`）有。

## 下游影响

- calculator 公式与比率阈值**不变**；新增进入前的币种一致校验 + growth 口径修正 + 缺失语义修正，消除 0.9032 污染与现金健康乐观偏差。
- HK：财务(上游 HKD)+市值(yfinance HKD)+分红(`ak.stock_hk_dividend` HKD)+回购(上游 HKD)全链路 HKD，可出完整报告。
- A 股：市值改 Tushare，可出完整报告。

## 架构与实现位置

- **新增** `value_investment/unit_normalizer.py`：`tag_currency(data, source, market)`（优先源自带 `currency`）、`assert_same_currency(a, b)`、`assert_consistent_currency(*dicts)`（主闸）、`scale_a_share_market_cap(total_mv_wan)`、金额白名单常量。
- **改** `financial_reports/mapper.py`：入口 `assert_same_currency`；用 extraction `currency` 标 `_currency`；导出 `repurchase_of_stock` 到 `financial_data`。
- **改** `value_investment/report_data_mapper.py`：入口 `assert_same_currency`；出口标 `_currency`；**去掉 growth `*100`**（:181/185）。
- **改** `value_investment_tool.py`：`_fetch_market_data_structured`(去死分支、A→TushareProvider 同步单股×1e4、HK→yfinance info、标币种)；`_get_industry_dynamic`(HK→yfinance)；`_fetch_dividend_data_sync`/`_fetch_buyback_data_sync`(改子包 + 标币种)；`_fetch_financial_data_structured`(**缺失→None** + **重写 `:398-403` ibd 累加为过滤-None-求和** + **修 `:413` None-safe 日志** + 出口标 `_currency='CNY'`、HK 带币种空骨架)；主函数(calculator 入口 `assert_consistent_currency` 主闸 + 无条件组装上游 `repurchase_of_stock` 进 `buyback_data`)。
- **改** `value_investment/cash_health.py`：`bonds_payable`→`bond_payable` 字段名统一（:193）。
- **改** `providers/china/tushare.py`：新增同步单股 `daily_basic` 方法。

## 非目标

- 不改 calculator 公式与阈值；**不把比率改成小数**（仅 growth 统一为小数以修双重 ×100）。
- 不对上游/akshare 金额做数量级缩放（已是元）；仅 A 股市值 ×1e4。
- 不改公共统一数据层（`interface.py`/`data_source_manager.py`）。
- 不实现 HK 回购的独立抓取（用上游 `repurchase_of_stock`）。
- 不引入 CNY↔HKD 汇率换算（跨币种是拒绝/换源）。
- **A 股 akshare 取数结构保留，但缺失语义（缺失→None）与 growth 口径属本 PR 修正范围**（不再是"完全不动"）。

## 测试（hermetic 单测为主，核实 #7）

> 默认 pytest 跳过 integration，故核心回归用 **mock 取数**，不依赖容器/Tushare/yfinance/AKShare/上游。

1. **币种主闸**：`assert_consistent_currency(financial_data{_currency:'HKD'}, market_data{_currency:'CNY'})` 抛 `ValueError`；同币种放行。
2. **0.9032 污染固定 fixture**：HKD 财务（保守利润 100）+ HKD 市值 1000 → 穿透率 10%；若 market_data 为 CNY 折算市值 903.2，**主闸必须拒绝**（不是算出 11.07%）。
3. **merge 拒绝混币**：`merge_financial_data`/`merge_financial_report_data` CNY+HKD → `ValueError`。
4. **A 股市值缩放**：同步单股 mock 返回 `total_mv=2300`(万元) → `market_cap=2.3e7`(元) `_currency='CNY'`。
5. **HK 市值源**：`_fetch_market_data_structured('00001','HK')` 走 yfinance/HKD，**断言不调用 AKShare HK 折算源**。
6. **growth 口径**：mapper 产出 `revenue_growth_3y` 为小数（0.12）；经 health_score `*100` 得 12，评分正确（防双重 ×100 回归）。
7. **缺失语义**：mock A 股 debt 全缺失 → `interest_bearing_debt is None`（非 0）；capex 缺失 → None。
8. **字段名统一**：FRC 写 `bond_payable`，cash_health 回退能读到（验证别名/改名生效）。
9. **HK 回购落点**：上游 `repurchase_of_stock=0` → 进 `buyback_data.total_cancelled_amount=0`；`=None` → 不计入。
10. **A 股回归**：`_fetch_financial_data_structured('600519','A')` 返回非空 CFO、`_currency='CNY'`（mock akshare）。
11. **端到端（标 `@pytest.mark.integration`，默认跳过）**：容器内 `00001`(HK)/`600519`(A) 各出完整报告，无 `❌ 无法获取市值`，日志无币种 error。

## 风险与开放问题

- **通路 A（report-collector REST）当前不可达**，单位无法实测（高置信度元）；启用前须实测核对。当前生效路径通路 B（元/HKD）。
- **上游 `repurchase_of_stock` 依赖 LLM supplement**：实测本次为 None（未命中）；落点须正确处理 None vs 0，不得臆造。
- **A 股缺失语义修正影响面**：改 `,0)`→`,None)` 可能让部分现金健康度评分变化（更保守、更真实）——属预期修正，需在 PR 说明并跑 A 股回归。
- **Tushare daily_basic**：单股同步原生调用，注意配额；不可用回退 yfinance(A 股)。
- **yfinance 港股 marketCap 限频/实时性**；**HK 分红币种**须确认 HKD；**总股本对齐分红总额**须同币种。
- **事件循环（N7，plan 阶段须定死）**：`DividendFetcher.fetch_dividend_data`(dividend_fetcher.py:86)/`BuybackFetcher.fetch_buyback_data`(buyback_fetcher.py:84) 为 async，主函数是同步 `@tool`。writing-plans 前须确认 value 工具是否曾在 async 上下文（FastAPI 请求线程）被直调——若是，`asyncio.run` 仍会撞 CLAUDE.md 记录的事件循环冲突。plan 须定死包装方式（候选：`asyncio.to_thread(lambda: asyncio.run(coro))`，或复用 `analysis_service` 现有 sync 包装 helper），不留临场决定。
