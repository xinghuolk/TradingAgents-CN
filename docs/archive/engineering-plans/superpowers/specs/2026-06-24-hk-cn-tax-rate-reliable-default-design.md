# 港股通 / A股 tax_rate 可靠默认化（解除 value 决策阻断）

- 日期：2026-06-24
- 范围：单个 PR
- 生产代码边界：`tradingagents/dataflows/value_investment/turtle/market_adapter.py`（Apache 2.0，唯一改动的生产文件）
- PR 文件范围：上述生产文件 + 单测 `tests/unit/`（turtle market_adapter 相关）
- 关联：`docs/analysis/00004_600519_value_field_coverage_20260624.md §5b`（决策层阻断分析）

## 背景

完整 value 流程（`prepare_turtle_analysis_payload → compute_turtle_signals`）对 600519 实测 `signals.status = non_decisionable`：核心回报率公式 `R`/`GG`/`HH` 因 `tax_rate` 不可用而无法计算。根因在 `market_adapter.py`：**税率值算对了，但 reliability 被判 `display_only`，导致下游公式拒绝使用**。

`build_market_facts`（`market_adapter.py:257-271`）现有三分支：

```python
tax_rate_known = _is_known_tax_rate_combination(market, active_channel)
if not tax_rate_known:                       # 未知组合
    tax_rate_reliability = "display_only"; ... "tax_rate unknown"
elif not channel_is_explicit:                # 已知但 holding_channel 非显式传入  ← 阻断根因
    tax_rate_reliability = "display_only"; ... "pass holding_channel explicitly"
else:                                        # 已知且显式
    tax_rate_reliability = "reliable"
```

日常 value 分析（`prepare_turtle_analysis_payload`）默认不传 `holding_channel`，于是 `active_channel = default_holding_channel(market)`（A股 `long_term_domestic`、HK `stock_connect`）——命中**第二分支**：组合已知、税率有政策依据，却因「非显式」被判 `display_only`，阻断 R/GG/HH。

## 税率业务规则（依据财税〔2014〕81号文）

内地个人投资者红利税：

| 公司类型 | 港股通 | 香港账户（离岸） |
|---|---|---|
| H股 | 20% | 10% |
| 红筹（已计提10%企税，如腾讯） | 20% | 0% |
| 红筹（未计提10%企税，如中国移动） | 28% | 10% |
| 香港本地股/外资股 | 20% | 0% |

- **港股通默认就是 20%**（81号文），不分 H股/红筹。28% 仅「红筹+未计提10%企税」特例，判定需逐期股息公告的「代扣所得税」标志，**注册地不足以判定**。
- A股长期持有（>1年）红利税 0%。

现有 `default_tax_rate`（`market_adapter.py:178`）的 `direct_h_share → 0.28` 分支**语义错误**：28% 不是「直接持有H股」，而是港股通持未计提红筹；H股无论港股通(20%)还是离岸(10%)都不是 28%。

## 目标

让政策上有可靠默认的 `(market, 默认渠道)` 组合在 `holding_channel` 非显式时也给 `reliable`，从而解除日常 value 分析的 `non_decisionable`：

- A股 `long_term_domestic` → 0%，`reliable`，**无 caveat**（长期持有税率确定）。
- HK `stock_connect` → 20%，`reliable`，**带 caveat**（港股通默认，离岸/未计提红筹税率不同）。

## 非目标

- 不做离岸渠道（H股10% / 红筹0或10% / 本地股0）。
- 不做 28% 精化（依赖每期股息公告「代扣所得税」标志，数据可得性未确认）。
- 不改 US `w8ben` 行为（保持现有：显式 `reliable`、非显式 `display_only`）。
- 不改任何公式计算逻辑（`calculations.py`）、不改 `default_holding_channel`（`facts.py`）、不改 `turtle_analysis_tool`。

## 设计

唯一改动 `market_adapter.py`，三处：

### (1) 新增「政策可靠默认税率」判定

```python
def _reliable_default_tax(market: str, holding_channel: str) -> tuple[float, str | None] | None:
    """政策上可靠的默认 (market, 渠道) 组合 → (tax_rate, caveat)；否则 None。

    caveat=None 表示无条件可靠；非 None 表示可靠但需披露渠道假设。
    税率值复用 default_tax_rate，保证唯一来源（DRY）。
    """
    normalized_market = _normalize_market(market)
    normalized_channel = (holding_channel or "").strip().lower()
    if normalized_market in {"A", "CN", "CHINA"} and normalized_channel == "long_term_domestic":
        return (default_tax_rate(market, holding_channel), None)
    if normalized_market in {"HK", "HKG"} and normalized_channel == "stock_connect":
        return (
            default_tax_rate(market, holding_channel),
            "tax_rate 按港股通渠道默认 20%；离岸账户或未计提10%企税的红筹股税率不同，"
            "如适用请显式传 holding_channel",
        )
    return None
```

### (2) `build_market_facts` 税率段（`:257-279`）重写

「政策可靠默认」优先拦截 A股/港股通；其余市场（当前 US）走原有 known/explicit 旧逻辑，行为不变：

```python
reliable_default = _reliable_default_tax(market, active_channel)
if reliable_default is not None:
    # A股 long_term_domestic / 港股通 stock_connect：非显式也 reliable（政策默认有依据）
    tax_rate_value, tax_rate_caveat = reliable_default
    tax_rate_reliability = "reliable"
    if tax_rate_caveat:
        _append_caveat(caveats, tax_rate_caveat)
else:
    tax_rate_value = default_tax_rate(market, active_channel)
    tax_rate_known = _is_known_tax_rate_combination(market, active_channel)
    if not tax_rate_known:
        tax_rate_reliability = "display_only"
        tax_rate_caveat = f"tax_rate unknown for {market}:{active_channel}"
        _append_caveat(caveats, tax_rate_caveat)
    elif not channel_is_explicit:
        tax_rate_reliability = "display_only"
        tax_rate_caveat = (
            f"tax_rate uses default holding_channel '{active_channel}' for {market}; "
            "pass holding_channel explicitly to enable computation"
        )
        _append_caveat(caveats, tax_rate_caveat)
    else:
        tax_rate_reliability = "reliable"
        tax_rate_caveat = None

fields["tax_rate"] = _field(
    "tax_rate", tax_rate_value, "holding_channel.default_tax_rate",
    caveat=tax_rate_caveat, reliability=tax_rate_reliability,
)
```

### (3) 清理 `default_tax_rate` / `_is_known_tax_rate_combination`

- `default_tax_rate`：**移除 `direct_h_share → 0.28` 分支**（语义错误）。A股 `0.0`、HK `stock_connect → 0.20`、US `w8ben → 0.10` 不变。
- `_is_known_tax_rate_combination`：HK 已知组合从 `{stock_connect, direct_h_share}` 收为 `{stock_connect}`（`direct_h_share` 移除）。注：HK `stock_connect` 与 A股 `long_term_domestic` 现由 `_reliable_default_tax` 前置拦截，不再经此函数；此函数实际仅服务 US 路径，HK/A 分支保留是为防御直接调用者。

## 数据流

| 场景 | tax_rate 值 | reliability | caveat | R/GG/HH |
|---|---|---|---|---|
| A股默认渠道（long_term_domestic，非显式） | 0.0 | reliable | 无 | ✅ 可算，**解除 non_decisionable** |
| 港股通默认渠道（stock_connect，非显式） | 0.20 | reliable | 有（港股通假设） | ✅ 可算（caveat 致 facts degraded，但 signals 不再 non_decisionable） |
| 显式传 stock_connect | 0.20 | reliable | 有 | ✅ 同上 |
| US w8ben 显式 | 0.10 | reliable | 无 | 不变 |
| US w8ben 非显式 | 0.10 | display_only | 有 | 不变 |
| 未知组合（如 HK + 乱填） | default_tax_rate fallback | display_only | "unknown" | 维持阻断（正确） |

> 注：解除的是 `tax_rate` 这一项的阻断。600519 仍有 `buyback_amount_3y_avg`/`payout_3y_avg` 等多年均值缺口（`00004 §5b`），R/GG 可能仍 degraded，但不再因 tax_rate 整体 `non_decisionable`。

## 下游影响

- 日常 value 分析（CACHE 命中、不传 holding_channel）的 A股/港股通标的，`tax_rate` 从 `display_only` 变 `reliable`，R/GG/HH 从 `non_decisionable` 变为可计算（degraded 或 complete）。属预期修复。
- 显式传 `holding_channel` 的调用方行为不变（A股/港股通仍 reliable；港股通新增 caveat 属增量披露）。
- `direct_h_share` 渠道被移除：若有调用方显式传 `direct_h_share`，将从「reliable 28%」变为「display_only + unknown」——这是有意修正（28% 语义本就错误）；离岸/红筹精确档留待后续渠道改造。

## 测试

`tests/unit/` 新增/更新 turtle market_adapter 单测：

1. **A股默认渠道**：`build_market_facts(market="A", holding_channel=None, ...)` → `tax_rate` reliability=`reliable`、值=0.0、**无 tax_rate caveat**。
2. **港股通默认渠道**：`market="HK", holding_channel=None` → reliability=`reliable`、值=0.20、caveat 含「港股通」。
3. **港股通显式**：`holding_channel="stock_connect"` → 同 #2。
4. **US w8ben 非显式不变**：`market="US", holding_channel=None` → `display_only`（回归保护，确认未误伤 US）。
5. **US w8ben 显式不变**：`holding_channel="w8ben"` → `reliable`。
6. **未知组合**：`market="HK", holding_channel="foobar"` → `display_only` + "unknown"。
7. **direct_h_share 已移除**：`holding_channel="direct_h_share"` → `display_only`（不再是 reliable 28%）。
8. **（端到端，可选）** `prepare_turtle_analysis_payload("600519","A",...)` → `signals.results["R"].status != "non_decisionable"`，且 `R.missing_inputs` 不含 `tax_rate`。

## 风险

- **误伤 US**：重写若把 US 也卷入 `_reliable_default_tax` 会改变其 reliability。设计上 US 不进可靠默认表、走原 else 分支，#4/#5 回归测试锁定。
- **显式 direct_h_share 行为变化**：从 reliable 28% 变 display_only，需在 PR 说明（有意修正错误值，非回归）。
- **缓存**：`market_adapter` 不经 FRC 缓存，改动即时生效，无缓存同步问题。
