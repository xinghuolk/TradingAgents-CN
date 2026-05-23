# Spec 3：Turtle v0.15 Data-Source Quality（FX + 溯源）设计

> 状态：设计已与用户确认（2026-05-23）。下一步转 writing-plans。
> 工作分支：`feat/turtle-spec3-data-source-quality`（基于 main，Spec 1 PR#8 + Spec 5 PR#9 已 merge）。
> 路线图：`docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md` Spec 3。
> 上游对比：`docs/tech_reviews/2026-05-21-pr7-turtle-calculation-and-source-review.md` 的 A.3 / B.2 / B.4。

## 1. 目标与范围

打通 Turtle v0.15 的跨币 FX 通道，并补齐市场数据与 FX 的来源可追溯性。

**In scope：**

1. **A.3 跨币 FX 通道** —— 选定 yfinance FX 对作为 provider，填充 `facts.report.metadata["fx_rates"]`，明确 pair 方向约定，打通 `to_hundred_million` 跨币换算。
2. **B.2 市场 source_reference 缺 provider** —— `build_market_facts` 携带数据来源 provider + fetched_at 进 source_reference。
3. **B.4 FX metadata 缺 provider/timestamp** —— FX provenance（provider / as_of / fetched_at / rate）写入 `metadata["fx_rates_meta"]`。
4. **FX 与 market_cap 的 as-of 对齐约束**（关键，见 §3）。

**Out of scope：**

- **承诺支付率字段** —— 归属 Spec 2 的 optional enhancement / backlog；待 Spec 2 的 M 算法 `max(min(3y avg, 承诺), 新信号)` 字段语义与缺失降级口径定清楚后，再判断是否升级成独立 mini-spec。本 Spec 不引入任何承诺支付率提取。
- **历史 market_cap 抓取** —— 当前 adapter 只能取最新快照，不在本 Spec 引入历史行情回填。

## 2. 架构与模块边界

**方案 A（采用）：独立 `fx.py` 模块 + orchestrator 装配。**

新增 `tradingagents/dataflows/value_investment/turtle/fx.py`，提供 I/O 隔离、可 mock 的纯函数。装配发生在 `tradingagents/tools/turtle_analysis_tool.py::prepare_turtle_analysis_payload`——这是已有的 report + market facts 装配点：扫描两侧 money fact 的币种，调 fx 模块，把 `fx_rates` + provenance 注入 `report.metadata`，再构 `TurtleFacts` 并 `compute_turtle_signals`。

**已否决：**

- 方案 B（FX 内联进 `market_adapter`）：与行情抓取耦合；且 market_adapter 拿不到报表币种，无法判断需要哪些 pair。
- 方案 C（`calculations._fx_rates` 惰性抓取）：calculations 层须保持纯函数无 I/O，惰性抓取破坏可测性、无缓存、重复网络调用。

## 3. as-of 对齐约束（关键）

R = (报表币种的收益 numerator) / (交易币种的 market_cap)。换算时 **market_cap 与 FX 必须共享同一个 as-of**，其唯一真实来源是 **market_cap 快照实际代表的日期**：

- 不得用旧 `trade_date` 配今天的 market_cap；也不得用今天的 FX 配旧 market_cap。
- 当前 HK（yfinance latest info）与 A股（`_fetch_market_data_structured` latest）adapter **只能取最新快照** → `market_as_of` = fetch date（≈今天）。FX 锚定此 `market_as_of`，**不是**传入的 `trade_date`。
- `trade_date` 仍用于报表 `period_end` 推断（`infer_turtle_period_end`），但**不**作为 FX 锚点。
- 结构上保留扩展性：若将来某 adapter 能返回历史 as-of 的 market_cap，FX 自动跟随该 as-of（fx 模块按 as_of 取最近 ≤ as_of 的汇率）。本 Spec 不实现历史行情抓取。
- 当 `market_as_of` ≠ 请求的 `trade_date` 时，补一条 caveat：说明 market_cap 是当前快照、非 `trade_date` 当日历史值，FX 已对齐快照日期。

## 4. FX 模块（`turtle/fx.py`）

```python
@dataclass(frozen=True)
class FxQuote:
    pair: str          # 归一化后的 "FROM:TO"，如 "HKD:CNY"
    rate: float        # 1 FROM 兑 rate 个 TO
    provider: str      # "yfinance"
    as_of: str         # 实际取到的汇率日期 (YYYY-MM-DD，最近 ≤ 请求 as_of 的交易日)
    fetched_at: str    # 拉取时刻 ISO timestamp


def fetch_fx_rate(from_currency: str, to_currency: str, as_of_date: str) -> FxQuote | None:
    # from==to → rate 1.0（identity，不发网络）
    # symbol = f"{FROM}{TO}=X"，如 "HKDCNY=X"
    # yfinance 惰性 import（与 hk_stock 一致）
    # .history(start=as_of-7d, end=as_of+1d) → 取 date ≤ as_of 的最后一行 close
    #   （周末/节假日自动落到最近前一交易日）
    # 窗口内无数据 / 异常 → 返回 None（不抛）
    ...


def resolve_fx_rates(
    currencies: Iterable[str], target: str, as_of_date: str
) -> tuple[dict[str, float], dict[str, dict], list[str]]:
    # 对每个 normalize(ccy) != normalize(target) 的币种取 pair
    # 成功：fx_rates["FROM:TO"] = rate；fx_rates_meta["FROM:TO"] = {provider, as_of, fetched_at, rate}
    # 失败：追加 caveat（"FX FROM:TO 取数失败，跨币计算降级"）
    # 返回 (fx_rates, fx_rates_meta, caveats)
    ...
```

`pair` 方向约定（与现有 `to_hundred_million` 一致）：`fx_rates["HKD:CNY"]` = 1 HKD 兑多少 CNY，乘到 HKD 数值上得 CNY。

## 5. 币种归一化

报表币种来自 extractor、仅 `.upper()`，可能是 `"RMB"` / `"HK$"` / `"US$"` 等非 ISO；市场币种由 `_currency_for_market` 给出已是 ISO（HK→HKD，其余→CNY）。pair key 必须用 ISO，否则 `to_hundred_million` 拼出的 `"RMB:CNY"` 与 yfinance / fx_rates 对不上。

在 `facts.py` 新增共享 helper：

```python
def normalize_currency(currency: str) -> str:
    # RMB / 人民币 / CNY → CNY；HK$ / HKD / 港币 → HKD；US$ / USD / 美元 → USD
    # 其余 → currency.upper()
```

- `to_hundred_million` 拼 pair 前对 `self.currency` 与 `target_currency` 归一化（对 `to_hundred_million` 的改动）。
- `resolve_fx_rates` 与 `prepare_turtle_analysis_payload` 的币种收集复用同一函数。
- **`calculations.py::_money_fact_currencies`（行 75）必须改用 `normalize_currency`，不能再用原始 `fact.value.currency.upper()`。** 否则 `"HK$"` 与 `"HKD"` 会被收成两种币 → `_money_target_currency`（行 80-84）判定为多币 → 强制 target=CNY → 纯 HKD 股的 `net_profit`/`market_cap` 双双触发 FX；一旦 FX 拉取失败，本可在 HKD 下算出的 R/GG 被错误降级。同理 `"RMB"` 单币种归一后 = `"CNY"`，`_money_target_currency` 返回 `"CNY"` → FormulaResult.unit 显示与已归一金额一致（避免「unit 显示 RMB 但金额已是 CNY」的错位）。这是本 Spec 对 `calculations.py` 的**唯一**改动；`_fx_rates` 等其余逻辑不动。
- **不**修改各 fact 构造点的 currency 字段（避免大面积 ripple）；归一化只发生在比较 / pair 拼接 / 币种收集处。

## 6. 数据流与注入

`prepare_turtle_analysis_payload` 内（build report + market facts 之后、构 TurtleFacts 之前）：

1. `market_as_of = market_facts.metadata.get("market_as_of")`（由 `build_market_facts` 写入的日期串 `YYYY-MM-DD`；实时快照 = fetch date）。
   - **缺失策略（兼容旧 payload / 测试替身）**：若 `market_as_of` 为空，**用 fetch date（今天）作为 FX 锚点并追加一条 caveat**（"market_as_of 缺失，FX 已对齐拉取日"）。**严禁 fallback 到 `trade_date`**——那会违反 §3 的 as-of 约束（旧 trade_date 配当前快照）。选 fetch date 而非「跳过 FX 直接降级」，是为了不让缺元数据的旧对象在 FX 可用时被无谓降级。
2. 收集 report + market 两侧 money fact 的**归一化去重**币种集合（含 `report.historical` 各期的 money fact）→ `currencies = _collect_currencies(report, market_facts)`。
3. **仅当 `len(currencies) >= 2` 时才拉 FX**：`fx_rates, fx_rates_meta, fx_caveats = resolve_fx_rates(currencies, "CNY", market_as_of)`；否则 `fx_rates, fx_rates_meta, fx_caveats = {}, {}, []`（**不拉、不加 caveat**）。
   - **理由（关键，避免误降级）**：calculations 的 `_money_target_currency`（`calculations.py:80`）是**逐 formula group** 选 target——若某 group 归一后只有单一币种（如纯港币股 `net_profit`/`market_cap` 同为 HKD），它选 native HKD、**根本不需要 FX**。全局只有单一归一币种（无论 CNY 还是纯 HKD/USD）意味着所有 group 都统一币种、零跨币 → 不应拉 FX，更不应在 yfinance 失败时加「跨币降级」caveat 把本可计算的结果连累降级。≥2 种币种才意味着至少一个 group 会回退到 CNY target、确有 `*:CNY` 需求；此时 FX 失败的 caveat 才名副其实（对应 group 会经现成级联降级，详见 §8）。
4. 若 `market_as_of` 与 `trade_date` 不一致，追加 snapshot caveat（§3）。
5. 重建 report 注入 metadata：

```python
report = TurtleReportFacts(
    fields=report.fields,
    metadata={**report.metadata, "fx_rates": fx_rates, "fx_rates_meta": fx_rates_meta},
    caveats=[*report.caveats, *fx_caveats],
    status=report.status,
    historical=report.historical,
)
```

6. 照旧构 `TurtleFacts` + `compute_turtle_signals`。

`report.historical` 各期的 money 也用同一 `market_as_of` 的 FX 换算（`_money_hm_report_3y_avg` 走 `_fx_rates(facts)` 读同一 `fx_rates`）——与 as-of 决策一致；加一句 caveat 说明历史各期统一用快照日 FX。

## 7. 溯源标注（B.2 + B.4）

- **B.2 市场**：`build_market_facts` 新增 `provider` 参数（HK 走 `info["source"]` 如 `yfinance_hk`；A股走对应 provider，缺失则 `"unknown"`），并计算 `market_as_of` + `fetched_at`。market_cap 等 money fact 的 `source_reference` 改为 `"market_data.market_cap; provider=yfinance_hk; fetched_at=<ISO>"`。`market_as_of` 写入 `TurtleMarketFacts.metadata`。行情是实时快照，as-of 语义即 fetch date。
  - **A股 provider 必须落地**：`_fetch_market_data_structured`（`value_investment_tool.py:553`）当前返回的 dict **无 `source` 键**，若实现只在 market_adapter 读 `market_data["source"]`，A股 主路径会变成 `provider="unknown"`、B.2 形同虚设。因此 **`market_adapter._fetch_turtle_market_data` 的 A股 分支在拿到 structured data 后须 `data.setdefault("source", "akshare.stock_individual_info_em")`**（即 `_fetch_market_data_structured` 实际调用的 endpoint），再交给 `build_market_facts`。不改 `value_investment_tool.py`，把 provenance 契约收在 turtle 边界内。
- **B.4 FX**：provenance 进 `metadata["fx_rates_meta"]`（provider / as_of / fetched_at / rate），供 Spec 4 中间页展示；`to_hundred_million` 现有的 `"; FX HKD:CNY=0.92"` source_reference 行保留。

`TurtleMarketFacts` 需新增 `metadata: dict[str, Any]` 字段（对齐 `TurtleReportFacts`）：`__post_init__` 深拷贝、`to_dict` 增加 `"metadata"` 键。**两处反序列化重建路径都必须透传 `metadata`**，否则 `market_as_of` / provider 会在该路径被丢、B.2/B.4 provenance 到不了分析师 prompt 与 Spec 4：
- `turtle_analysis_tool._market_facts()`（强转分支）补传 `metadata`。
- `value_analyst._plain_turtle_report_prompt()`（`value_analyst.py:192-196`）从 tool payload 重建 `TurtleMarketFacts` 时补 `metadata`，但用**可选读取**：`metadata=dict(market_payload.get("metadata") or {})`。**不**用 strict `_required_mapping`——`market.metadata` 是新增字段，旧 payload / 测试替身（如 `tests/unit/test_value_analyst_payload_propagation.py:66` 的 `"market": {"fields": {}, "caveats": []}`）没有该键，strict 会破坏向后兼容。report 侧保持原 strict 行为不变（report.metadata 一向必有）。

## 8. 失败处理与 status

不新增 hard-fail，复用现成降级级联：

- `_money_hm`（`calculations.py`）已 catch `to_hundred_million` 的 `(TypeError, ValueError, OverflowError)` → 缺 pair 的跨币 fact 自动进 `missing_inputs` → R / GG → `non_decisionable` → 整体降级。
- Spec 3 仅在**确实需要 FX**（全局 ≥2 种归一币种，见 §6 step 3）且 `resolve_fx_rates` 失败时补**解释性 caveat**（"FX HKD:CNY 取数失败，跨币计算降级"），避免用户只看到泛化 missing input。
- **单一归一币种（A股纯 CNY、港股纯 HKD、美股纯 USD）无需 FX → 不拉、不发网络、不加 caveat、不触发任何降级**——calculations 在该 group 选 native target 直接算出结果。

## 9. 测试策略

测试入口 `.venv/bin/python -m pytest tests/unit/`，全程 mock yfinance、零真实网络。

- **`tests/unit/.../test_turtle_fx.py`（新）**：
  - `fetch_fx_rate`：mock `.history` 返回多日 DataFrame → 断言取 ≤ as_of 的最近一行；identity（from==to）返回 rate 1.0 且不发网络；空 DataFrame → None；异常 → None。
  - `resolve_fx_rates`：多币种聚合；部分失败 → 对应 caveat + 成功项仍在 fx_rates。
  - `normalize_currency`：RMB/人民币→CNY、HK$/港币→HKD、US$/美元→USD、未知→upper。
- **集成（`prepare_turtle_analysis_payload`）**：
  - **跨币（混币）**：`net_profit` CNY + `market_cap` HKD + mock FX → `report.metadata["fx_rates"]` 含 `"HKD:CNY"`、`fx_rates_meta` 含 provenance、R 跨币算得出。
  - **纯 HKD 不拉 FX（防误降级回归）**：`net_profit` HKD + `market_cap` HKD（全局单一归一币种）→ **FX provider 完全不被调用**（patch `fetch_fx_rate`/`resolve_fx_rates` 断言未调用）、无 FX caveat、R 在 HKD 下算出；即便此时让 FX mock 抛错也不影响结果。
  - FX 取数失败（混币场景）→ 依赖该 pair 的 R `non_decisionable` + fx caveat 在场。
  - `market_as_of` ≠ `trade_date` → snapshot caveat 在场。
  - `market_as_of` 缺失（构造无该 metadata 的 market facts）→ FX 锚定 fetch date、补「market_as_of 缺失」caveat、**不**用 trade_date。
- **`calculations`**：构造 `net_profit` currency=`"HK$"` + `market_cap` currency=`"HKD"`（纯港币、无 fx_rates）→ `_money_fact_currencies` 归一为单一 `"HKD"` → `_money_target_currency` 返回 `"HKD"` → R 在 HKD 下算出、**不**因缺 FX 降级；对照组（未归一前的原始 `.upper()`）会误判多币——确保归一化生效。`net_profit` currency=`"RMB"` 单币 → target/unit 归一显示 `"CNY"`。
- **`market_adapter`**：HK market_cap 的 `source_reference` 含 `provider=yfinance_hk` + `fetched_at`；**A股 `_fetch_turtle_market_data` 注入 `source="akshare.stock_individual_info_em"`** → market_cap source_reference 的 `provider` ≠ `unknown`；`TurtleMarketFacts.metadata["market_as_of"]` 写入。
- **`value_analyst`**：(a) 含 `market.metadata.market_as_of` 的 payload 调 `_plain_turtle_report_prompt` → 重建后 `facts.market.metadata` 保留 `market_as_of`（防 market provenance 在分析师路径被丢）；(b) **向后兼容**：market **无 `metadata` 键**的旧 payload 不报错、`facts.market.metadata == {}`（对应可选读取，见 §7）。
- **`facts.py`**：`to_hundred_million` 归一化——currency `"RMB"` + target `"CNY"` 归一后同为 CNY → 不查 FX、直接免换算（不再误拼 `"RMB:CNY"`）；真正的 `HKD → CNY` 仍查 `fx_rates["HKD:CNY"]`；既有 FX 用例不回归。

## 10. 改动清单

- **新增**
  - `tradingagents/dataflows/value_investment/turtle/fx.py`
  - `tests/unit/test_turtle_fx.py`（与现有 `tests/unit/test_turtle_*.py` 扁平同级）
- **修改**
  - `facts.py`：`normalize_currency()` helper；`to_hundred_million` pair 归一化；`TurtleMarketFacts` 增 `metadata` 字段（`__post_init__` + `to_dict`）。
  - `calculations.py`：`_money_fact_currencies`（行 75）改用 `normalize_currency`（详见 §5 High 修订）。`_fx_rates` 等不动。
  - `tools/turtle_analysis_tool.py`：`prepare_turtle_analysis_payload` 注入 FX（含 `market_as_of` 缺失策略）；`_market_facts()` 强转补传 `metadata`。
  - `agents/analysts/value_analyst.py`：`_plain_turtle_report_prompt`（行 192-196）重建 `TurtleMarketFacts` 时补传 `metadata`。
  - `market_adapter.py`：`build_market_facts` 增 `provider` 参数 + `market_as_of` / `fetched_at`；source_reference 增 provider/timestamp；HK 路径透传 `info["source"]`；`_fetch_turtle_market_data` A股 分支 `setdefault("source", "akshare.stock_individual_info_em")`。
  - `turtle/__init__.py`：按需导出 `fetch_fx_rate` / `resolve_fx_rates` / `normalize_currency`。
  - 既有 turtle 测试：market facts `to_dict` 新增 `metadata` 键为 additive，更新断言。

## 11. 与其他 Spec 的关系

- 与 Spec 2 / Spec 4 / Spec 5 文件零重叠，可并行。
- Spec 4（中间页）将读取 `fx_rates_meta` 与市场 source_reference 展示数据来源——本 Spec 提供的 provenance 是其数据基础。
- 承诺支付率（→ Spec 2）不在此。
