# 港股通 / A股 tax_rate 可靠默认化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 A股 `long_term_domestic`(0%) 与港股通 `stock_connect`(20%) 在 `holding_channel` 非显式时也给 `reliable`，解除日常 value 分析中 R/GG/HH 因 `tax_rate` 不可用而 `non_decisionable`。

**Architecture:** 单文件改动 `tradingagents/dataflows/value_investment/turtle/market_adapter.py`：新增 `_reliable_default_tax` 政策可靠默认判定，在 `build_market_facts` 税率段优先拦截 A股/港股通；US 走原 known/explicit 旧逻辑不变；清理语义错误的 `direct_h_share=0.28` 分支。不改公式计算、不改 `default_holding_channel`、不改 `turtle_analysis_tool`。

**Tech Stack:** Python 3.11，pytest（容器内跑：`docker exec -w /app tradingagents-backend python -m pytest ...`）。

**Spec:** `docs/superpowers/specs/2026-06-24-hk-cn-tax-rate-reliable-default-design.md`

---

## File Structure

- **Modify** `tradingagents/dataflows/value_investment/turtle/market_adapter.py`
  - `default_tax_rate`（:178-191）：移除 `direct_h_share → 0.28` 分支
  - `_is_known_tax_rate_combination`（:194-204）：HK 收为 `{stock_connect}`
  - **新增** `_reliable_default_tax`（紧邻上述两函数之后）
  - `build_market_facts` 税率段（:257-279）：重写 reliability/caveat 判定
- **Modify** `tests/unit/test_turtle_market_adapter.py`
  - 更新 `test_default_tax_rate_by_holding_channel`（:10-14，`direct_h_share` 断言）
  - 新增 7 个 tax_rate reliability/caveat 测试

---

### Task 1: tax_rate 可靠默认化（函数 + build_market_facts + 单测）

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/market_adapter.py:178-279`
- Test: `tests/unit/test_turtle_market_adapter.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_turtle_market_adapter.py` 末尾追加（沿用文件现有 `build_market_facts(...)` 调用模式，必填参数 ticker/market/holding_channel/market_data/dividend_data/buyback_data/industry）：

```python
def _mk(market, holding_channel):
    return build_market_facts(
        ticker="TEST",
        market=market,
        holding_channel=holding_channel,
        market_data={"market_cap": 1_000_000_000, "close_price": 10.0},
        dividend_data={"avg_payout_ratio_3y": 0.4, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="测试",
        rf_rate=0.025,
    )


def test_a_share_default_channel_tax_rate_reliable_no_caveat():
    facts = _mk("A", None)  # holding_channel 非显式 → 默认 long_term_domestic
    tr = facts.fields["tax_rate"]
    assert tr.value == 0.0
    assert tr.reliability == "reliable"
    assert tr.caveat is None
    assert not any("tax_rate" in c for c in facts.caveats)


def test_hk_stock_connect_default_channel_reliable_with_caveat():
    facts = _mk("HK", None)  # 非显式 → 默认 stock_connect
    tr = facts.fields["tax_rate"]
    assert tr.value == 0.20
    assert tr.reliability == "reliable"
    assert tr.caveat is not None and "港股通" in tr.caveat
    assert any("港股通" in c for c in facts.caveats)


def test_hk_stock_connect_explicit_reliable_with_caveat():
    facts = _mk("HK", "stock_connect")
    tr = facts.fields["tax_rate"]
    assert tr.value == 0.20
    assert tr.reliability == "reliable"
    assert "港股通" in (tr.caveat or "")


def test_us_w8ben_default_channel_stays_display_only():
    facts = _mk("US", None)  # 非显式 → 默认 w8ben；US 不在可靠默认表，走旧逻辑
    tr = facts.fields["tax_rate"]
    assert tr.reliability == "display_only"


def test_us_w8ben_explicit_reliable():
    facts = _mk("US", "w8ben")
    tr = facts.fields["tax_rate"]
    assert tr.value == 0.10
    assert tr.reliability == "reliable"


def test_hk_unknown_channel_display_only():
    facts = _mk("HK", "foobar")
    tr = facts.fields["tax_rate"]
    assert tr.reliability == "display_only"
    assert any("unknown" in c for c in facts.caveats)


def test_hk_direct_h_share_removed_now_display_only():
    # direct_h_share=28% 语义错误已移除：显式传它不再是 reliable 28%
    facts = _mk("HK", "direct_h_share")
    tr = facts.fields["tax_rate"]
    assert tr.reliability == "display_only"
```

- [ ] **Step 2: 跑新测试确认失败**

Run: `docker exec -w /app tradingagents-backend python -m pytest tests/unit/test_turtle_market_adapter.py -k "default_channel or explicit or unknown_channel or direct_h_share_removed" -v`
Expected: FAIL —— `test_a_share_default_channel...`/`test_hk_stock_connect_default_channel...` 因现有「非显式 → display_only」逻辑而失败；`test_hk_direct_h_share_removed...` 因现有 direct_h_share=reliable 28% 而失败。

- [ ] **Step 3: 实现 `_reliable_default_tax` 并清理 `default_tax_rate` / `_is_known_tax_rate_combination`**

替换 `market_adapter.py:178-204` 的两个函数为下面三个（顺序：default_tax_rate → _is_known_tax_rate_combination → _reliable_default_tax）：

```python
def default_tax_rate(market: str, holding_channel: str) -> float:
    """Return default dividend withholding tax by market and holding channel."""
    normalized_market = _normalize_market(market)
    normalized_channel = (holding_channel or "").strip().lower()

    if normalized_market in {"A", "CN", "CHINA"} and normalized_channel == "long_term_domestic":
        return 0.0
    if normalized_market in {"HK", "HKG"}:
        # 港股通默认 20%（财税81号文）。28%（红筹未计提10%企税）依赖每期股息公告，
        # 不在此静态默认表内；离岸渠道留待后续改造。
        return 0.20
    if normalized_market in {"US", "USA"} and normalized_channel == "w8ben":
        return 0.10
    return 0.0


def _is_known_tax_rate_combination(market: str, holding_channel: str) -> bool:
    normalized_market = _normalize_market(market)
    normalized_channel = (holding_channel or "").strip().lower()

    if normalized_market in {"A", "CN", "CHINA"}:
        return normalized_channel == "long_term_domestic"
    if normalized_market in {"HK", "HKG"}:
        return normalized_channel == "stock_connect"
    if normalized_market in {"US", "USA"}:
        return normalized_channel == "w8ben"
    return False


def _reliable_default_tax(market: str, holding_channel: str) -> tuple[float, str | None] | None:
    """政策上可靠的默认 (market, 渠道) 组合 → (tax_rate, caveat)；否则 None。

    命中表示即使 holding_channel 非显式也按政策默认给 reliable。caveat=None 表示
    无条件可靠（A股长期持有 0%）；非 None 表示可靠但需披露渠道假设（港股通 20%）。
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

- [ ] **Step 4: 重写 `build_market_facts` 税率段**

替换 `market_adapter.py:257-279`（从 `tax_rate_known = ...` 到 `fields["tax_rate"] = _field(...)` 结束）为：

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
        "tax_rate",
        tax_rate_value,
        "holding_channel.default_tax_rate",
        caveat=tax_rate_caveat,
        reliability=tax_rate_reliability,
    )
```

- [ ] **Step 5: 更新现有 `test_default_tax_rate_by_holding_channel`**

`tests/unit/test_turtle_market_adapter.py:10-14` 现断言 `default_tax_rate("HK", "direct_h_share") == 0.28`。direct_h_share 分支已移除，HK 任意渠道现统一返回 0.20。把该行改为反映新行为：

```python
def test_default_tax_rate_by_holding_channel():
    assert default_tax_rate("A", "long_term_domestic") == 0.0
    assert default_tax_rate("HK", "stock_connect") == 0.20
    assert default_tax_rate("HK", "direct_h_share") == 0.20  # direct_h_share 分支已移除，回落 HK 默认 0.20
    assert default_tax_rate("US", "w8ben") == 0.10
```

- [ ] **Step 6: 跑全部 market_adapter 测试确认通过**

Run: `docker exec -w /app tradingagents-backend python -m pytest tests/unit/test_turtle_market_adapter.py -v`
Expected: PASS（原有测试 + 7 个新测试 + 更新后的 default_tax_rate 测试，全绿）。

- [ ] **Step 7: 跑 turtle 全量回归确认无连带破坏**

Run: `docker exec -w /app tradingagents-backend python -m pytest tests/unit/ -k turtle -q`
Expected: PASS（calculations/decision/facts/payload 等 turtle 套件不受影响）。

- [ ] **Step 8: Commit**

```bash
git add tradingagents/dataflows/value_investment/turtle/market_adapter.py tests/unit/test_turtle_market_adapter.py
git commit -m "$(cat <<'EOF'
fix(value): reliable default tax_rate for A-share/HK stock-connect

A股 long_term_domestic(0%) 与港股通 stock_connect(20%) 在 holding_channel
非显式时也给 reliable，解除 value 分析 R/GG/HH 因 tax_rate display_only 而
non_decisionable。新增 _reliable_default_tax 政策默认判定（港股通带 caveat、
A股无 caveat），US 走原 known/explicit 逻辑不变。移除语义错误的
direct_h_share=0.28 分支（28% 实为港股通持未计提红筹，非直接持 H股）。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 端到端验证 + 更新 00004 文档

**Files:**
- Modify: `docs/analysis/00004_600519_value_field_coverage_20260624.md`（§5b）

- [ ] **Step 1: 端到端跑 600519 value 流程确认 tax_rate 不再阻断**

Run: `docker exec -w /app tradingagents-backend python scripts/value_flow_probe.py 600519 A --company-name 贵州茅台 2>/dev/null | sed -n '/=====/,$p'`
Expected: `R`/`GG`/`HH` 的 `missing` 不再含 `tax_rate`；`signals` 可能仍 degraded（因 `buyback_amount_3y_avg`/`payout_3y_avg` 多年均值缺口），但 R/HH 不再 non_decisionable（HH 仅缺 tax_rate，应转为可计算）。

- [ ] **Step 2: 把端到端结果更新进 00004 §5b**

在 `docs/analysis/00004_600519_value_field_coverage_20260624.md` 的 §5b 表格后补一段说明 tax_rate 已由 PR（本次）解除阻断，并标注剩余阻断项收敛为多年均值缺口（`buyback_amount_3y_avg`/`dividend_payout_ratio_current_year_3y_avg`）。具体文字：

```markdown
**更新（tax_rate 已解除）**：`tradingagents/.../market_adapter.py` 的 tax_rate 可靠默认化改造后，A股默认渠道 tax_rate=0 reliable、港股通默认 20% reliable+caveat，`HH`（仅缺 tax_rate）转为可计算，`R`/`GG` 的阻断收敛为仅剩多年均值缺口（`buyback_amount_3y_avg` / `dividend_payout_ratio_current_year_3y_avg`，因只有单年报告、缺历史多期）。tax_rate 不再是 600519 的决策阻断项。
```

- [ ] **Step 3: Commit**

```bash
git add docs/analysis/00004_600519_value_field_coverage_20260624.md
git commit -m "$(cat <<'EOF'
docs(value): 00004 update — tax_rate unblocked, residual is multi-year avg

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- 设计 (1) `_reliable_default_tax` → Task 1 Step 3 ✅
- 设计 (2) `build_market_facts` 重写 → Task 1 Step 4 ✅
- 设计 (3) 清理 `default_tax_rate`/`_is_known` → Task 1 Step 3 ✅
- 数据流 6 行场景 → Task 1 Step 1 的 7 个测试逐一覆盖（A股默认/港股通默认/港股通显式/US默认/US显式/未知/direct_h_share）✅
- 测试 #8 端到端（spec 标注可选）→ Task 2 Step 1 ✅
- 风险「误伤 US」→ Task 1 的 `test_us_w8ben_default_channel_stays_display_only` + `test_us_w8ben_explicit_reliable` 锁定 ✅

**Placeholder scan:** 无 TBD/TODO；每个改代码的 step 都含完整代码。

**Type consistency:** `_reliable_default_tax` 返回 `tuple[float, str | None] | None`，Task 1 Step 4 按 `if reliable_default is not None: tax_rate_value, tax_rate_caveat = reliable_default` 解包，一致。`facts.fields["tax_rate"].reliability/.value/.caveat` 与现有 `_field`/`TurtleFactValue` 字段名一致（已核对 market_adapter.py:56-70）。
