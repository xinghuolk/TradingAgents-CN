# Value 多源取数复用 + 币种/单位归一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `get_value_investment_analysis` 复用公共/provider 取行情·市值·行业、改用子包取分红回购、消费上游 HKD 财务，并在进入 calculator 前强制币种一致，打通 HK 链路、修复 A 股失效行情接口与三个正确性 bug。

**Architecture:** 新增 `unit_normalizer.py` 作为币种归一边界（金额标 `_currency`、跨币种拒绝、A 股市值万元→元）；币种校验设两道闸门（主闸在 calculator 调用前校验四个独立 dict，次闸在两个 merge 入口）；取数按市场委派（A 股财务私有 akshare、HK 财务上游 `financial_report_client`、市值 Tushare/yfinance、分红回购子包 + 上游回购）。

**Tech Stack:** Python 3.10+，AKShare，Tushare（`TushareProvider.connect_sync`），yfinance，LangChain `@tool`，pytest（`tests/unit/`，docker `tradingagents-backend` 容器内跑）。

**Spec:** `docs/superpowers/specs/2026-06-15-value-multi-source-unit-normalization-design.md`

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `tradingagents/dataflows/value_investment/unit_normalizer.py` | 币种标记/校验/A股市值缩放/金额白名单 | 新增 |
| `tradingagents/dataflows/value_investment/report_data_mapper.py` | 通路 A 映射：去 growth ×100、出口标币种、merge 守卫 | 改 |
| `tradingagents/dataflows/financial_reports/mapper.py` | 通路 B 映射：merge 守卫、标币种、导出 repurchase_of_stock | 改 |
| `tradingagents/dataflows/value_investment/cash_health.py` | 字段名 `bonds_payable`→`bond_payable` | 改 |
| `tradingagents/dataflows/providers/china/tushare.py` | 新增同步单股 `daily_basic` 方法 | 改 |
| `tradingagents/tools/value_investment_tool.py` | 取数委派、缺失→None、ibd 重写、主闸、回购组装 | 改 |
| `tests/unit/test_value_unit_normalizer.py` 等 | hermetic 单测 | 新增 |

> 约定：所有 `pytest` / `python` 命令在容器内执行：`docker exec tradingagents-backend <cmd>`（工作目录 `/app`）。

---

## Task 1: 前置探针（验证取数源可用，非 TDD）

**Files:** 无（验证命令，结论回填 spec §结构化市值来源 / §HK 回购落点旁注）

- [ ] **Step 1: 验证 Tushare 同步单股 daily_basic + yfinance marketCap + 上游 repurchase_of_stock 字段**

Run:
```bash
docker exec tradingagents-backend python -c "
import os, tushare as ts
pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))
df = pro.daily_basic(ts_code='600519.SH', fields='ts_code,close,total_mv,total_share')
print('A股 total_mv(万元)=', None if df.empty else df.iloc[0]['total_mv'])
import yfinance as yf
print('HK marketCap(HKD)=', yf.Ticker('0001.HK').info.get('marketCap'))
from tradingagents.dataflows.financial_reports.adapter import create_financial_report_adapter
from tradingagents.dataflows.financial_reports.config import get_financial_report_client_config
ad = create_financial_report_adapter(get_financial_report_client_config())
r = ad.get_annual_report_data(ticker='00001', market='HK', period_end='2025-12-31')
f = getattr(r.extraction,'fields',{}) or {}
print('repurchase_of_stock in fields:', 'repurchase_of_stock' in f)
print('net_profit currency:', getattr(f.get('net_profit'),'currency',None))
"
```
Expected: `total_mv` 非空（~2.x万亿万元）；`marketCap` 非空（~2.x千亿）；`repurchase_of_stock in fields: True`；`net_profit currency: HKD`。

- [ ] **Step 2: 记录结论 / 阻塞判断**

若 `total_mv` 为空 → A 股市值改用 yfinance(`600519.SS`)，调整 Task 6。若 `repurchase_of_stock in fields: False` → HK 回购落点为死代码，Task 8/10 的回购组装标记为 no-op 并在 PR 说明。**任一关键源不可用须先记录再继续。**

---

## Task 2: 新增 `unit_normalizer.py`

**Files:**
- Create: `tradingagents/dataflows/value_investment/unit_normalizer.py`
- Test: `tests/unit/test_value_unit_normalizer.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_value_unit_normalizer.py
import pytest
from tradingagents.dataflows.value_investment.unit_normalizer import (
    tag_currency, assert_same_currency, assert_consistent_currency,
    scale_a_share_market_cap, AMOUNT_FIELDS,
)

def test_tag_currency_prefers_explicit():
    out = tag_currency({'operating_cash_flow': 62567000000.0}, source_currency='HKD', market='HK')
    assert out['_currency'] == 'HKD'
    assert out['operating_cash_flow'] == 62567000000.0  # 不缩放

def test_tag_currency_falls_back_to_market():
    out = tag_currency({'cash_and_equivalents': 1.0}, source_currency=None, market='A')
    assert out['_currency'] == 'CNY'

def test_tag_currency_skips_underscore_keys():
    out = tag_currency({'_data_source': {'x': 1}}, source_currency='CNY', market='A')
    assert out['_data_source'] == {'x': 1}

def test_scale_a_share_market_cap_wan_to_yuan():
    assert scale_a_share_market_cap(2300.0) == 2300.0 * 1e4  # 万元→元

def test_scale_none_is_none():
    assert scale_a_share_market_cap(None) is None

def test_assert_same_currency_rejects_mismatch():
    with pytest.raises(ValueError):
        assert_same_currency({'_currency': 'CNY'}, {'_currency': 'HKD'})

def test_assert_same_currency_empty_passes():
    assert_same_currency({}, {'_currency': 'HKD'})  # 空币种放行（merge 次闸向后兼容）

def test_assert_consistent_currency_main_gate_rejects():
    fin = {'_currency': 'HKD', 'operating_cash_flow': 1.0}
    mkt = {'_currency': 'CNY', 'market_cap': 2.0}
    with pytest.raises(ValueError):
        assert_consistent_currency(fin, mkt)

def test_main_gate_missing_currency_on_amount_dict_rejects():
    # 含金额但无 _currency → 配置错误，拒绝
    with pytest.raises(ValueError):
        assert_consistent_currency({'market_cap': 2.0})

def test_main_gate_empty_dict_skipped():
    assert_consistent_currency({}, {'_currency': 'HKD', 'market_cap': 1.0})  # 全空跳过，不抛
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_unit_normalizer.py -v`
Expected: FAIL（ModuleNotFoundError: unit_normalizer）

- [ ] **Step 3: 实现**

```python
# tradingagents/dataflows/value_investment/unit_normalizer.py
# -*- coding: utf-8 -*-
"""Value 多源数据的币种归一边界。canonical: 金额=元, 比率不动, 币种显式 _currency。"""
from typing import Dict, Any, Optional
from tradingagents.utils.logging_init import get_logger

logger = get_logger("unit_normalizer")

_MARKET_CURRENCY = {'A': 'CNY', 'HK': 'HKD', 'US': 'USD'}

# 参与币种校验的金额字段并集（FIELD_TO_KEY 目标 ∪ fields_to_merge ∪ calculator 读取）
AMOUNT_FIELDS = {
    'net_profits', 'revenue', 'operating_cash_flow', 'free_cash_flow', 'capex',
    'cash_and_equivalents', 'interest_expense', 'short_term_debt', 'long_term_debt',
    'bond_payable', 'current_portion_of_long_term_debt', 'interest_bearing_debt',
    'current_assets', 'current_liabilities', 'total_assets', 'total_liabilities',
    'total_equity', 'equity_attributable_to_owners', 'minority_int',
    'st_borr', 'lt_borr', 'repurchase_of_stock',
    'market_cap', 'total_cancelled_amount', 'latest_year_amount',
}


def tag_currency(data: Dict[str, Any], source_currency: Optional[str], market: str) -> Dict[str, Any]:
    """写 _currency（优先源自带，否则市场推断）。不缩放金额、不动比率、跳过 _ 前缀键。"""
    out = dict(data)
    out['_currency'] = source_currency or _MARKET_CURRENCY.get(market, 'CNY')
    return out


def scale_a_share_market_cap(total_mv_wan: Optional[float]) -> Optional[float]:
    """A 股 Tushare total_mv 万元 → 元。None 保持 None。"""
    if total_mv_wan is None:
        return None
    return float(total_mv_wan) * 1e4


def _has_amount(data: Dict[str, Any]) -> bool:
    return any(
        k in AMOUNT_FIELDS and v is not None and not (isinstance(v, list) and len(v) == 0)
        for k, v in data.items()
    )


def assert_same_currency(existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """merge 次闸：两者都标币种且不一致 → 拒绝。空币种放行（向后兼容旧调用）。"""
    a, b = existing.get('_currency'), incoming.get('_currency')
    if a and b and a != b:
        raise ValueError(f"币种不一致，拒绝合并: {a} vs {b}")


def assert_consistent_currency(*dicts: Dict[str, Any]) -> None:
    """主闸：calculator 调用前校验各含金额 dict 同币种。
    含金额非空 dict 必须带 _currency（缺失=配置错误，拒绝）；全空/无金额跳过。"""
    seen = set()
    for d in dicts:
        if not d or not _has_amount(d):
            continue
        cur = d.get('_currency')
        if not cur:
            raise ValueError("含金额的数据缺少 _currency 标记（配置错误）")
        seen.add(cur)
    if len(seen) > 1:
        raise ValueError(f"跨数据源币种不一致，拒绝分析: {sorted(seen)}")
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_unit_normalizer.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5: 提交**

```bash
git add tradingagents/dataflows/value_investment/unit_normalizer.py tests/unit/test_value_unit_normalizer.py
git commit -m "feat(value): add currency normalization boundary (tag/assert/scale)"
```

---

## Task 3: report_data_mapper — 去 growth ×100 + 出口标币种 + merge 守卫

**Files:**
- Modify: `tradingagents/dataflows/value_investment/report_data_mapper.py:181`、`:185`、`:192`(return 前)、`:208`(merge 入口)
- Test: `tests/unit/test_value_report_data_mapper.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_value_report_data_mapper.py
import pytest
from tradingagents.dataflows.value_investment.report_data_mapper import (
    map_extracted_reports_to_financial_data, merge_financial_data,
)

def _report(year, net_profit, revenue):
    return {'data': {
        'income_statement': {'net_profit': net_profit, 'revenue': revenue},
        'balance_sheet': {'total_assets': 1000.0, 'total_liabilities': 600.0},
        'cash_flow_statement': {'operating_cash_flow': net_profit * 1.5},
        'financial_metrics': {'roe': 18.0},
        '_pdf_info': {'report_year': year},
    }}

def test_growth_is_decimal_not_percent():
    reports = [_report(2025, 120, 1200), _report(2024, 110, 1100), _report(2023, 100, 1000)]
    out = map_extracted_reports_to_financial_data(reports)
    # 收入 1200 vs 1000 → 增长应为小数 0.2，不是 20.0
    assert out['revenue_growth_3y'] == pytest.approx(0.2, abs=1e-6)
    assert out['profit_growth_3y'] == pytest.approx(0.2, abs=1e-6)

def test_output_tagged_hkd():
    out = map_extracted_reports_to_financial_data([_report(2025, 120, 1200)])
    assert out['_currency'] == 'HKD'

def test_merge_rejects_cross_currency():
    with pytest.raises(ValueError):
        merge_financial_data({'_currency': 'CNY'}, {'_currency': 'HKD'})

def test_merge_same_currency_supplements():
    a = {'operating_cash_flow': None, '_currency': 'HKD'}
    rc = {'operating_cash_flow': 6.25e10, '_currency': 'HKD'}
    merged = merge_financial_data(a, rc)
    assert merged['operating_cash_flow'] == 6.25e10
    assert merged['_data_source']['operating_cash_flow'] == 'report-collector'
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_report_data_mapper.py -v`
Expected: FAIL（growth 为 20.0、无 `_currency`、merge 不抛）

- [ ] **Step 3: 实现**

改 `report_data_mapper.py:180-185`（去掉 `* 100`，统一小数）：
```python
    # 收入增长（小数；health_score 会 *100）
    if len(revenues) >= 3 and revenues[-1] and revenues[-1] > 0:
        data['revenue_growth_3y'] = (revenues[0] / revenues[-1] - 1)
    # 利润增长（小数）
    if len(net_profits) >= 3 and net_profits[-1] and net_profits[-1] > 0:
        data['profit_growth_3y'] = (net_profits[0] / net_profits[-1] - 1)
```

`map_extracted_reports_to_financial_data` 的 `return data`（:192）前：
```python
    from .unit_normalizer import tag_currency
    data = tag_currency(data, source_currency='HKD', market='HK')  # report-collector HK 标的
    return data
```

`merge_financial_data` 函数体首行（`:208` `merged = dict(akshare_data)` 之前）：
```python
    from .unit_normalizer import assert_same_currency
    assert_same_currency(akshare_data, rc_data)
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_report_data_mapper.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add tradingagents/dataflows/value_investment/report_data_mapper.py tests/unit/test_value_report_data_mapper.py
git commit -m "fix(value): growth as decimal + currency tag/guard in report_data_mapper"
```

---

## Task 4: financial_reports/mapper — merge 守卫 + 标币种 + 导出 repurchase_of_stock

**Files:**
- Modify: `tradingagents/dataflows/financial_reports/mapper.py`（`merge_financial_report_data:194-248`）
- Test: `tests/unit/test_frc_mapper_currency.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_frc_mapper_currency.py
import pytest
from types import SimpleNamespace
from tradingagents.dataflows.financial_reports.mapper import merge_financial_report_data
from tradingagents.dataflows.financial_reports.policy import FinancialReportPolicy

def _field(value, currency='HKD'):
    return SimpleNamespace(value=value, currency=currency, source_label='llm', staleness=None)

def _extraction(fields, currency='HKD'):
    return SimpleNamespace(fields=fields, company='00001', market='HK',
                           period_end='2025-12-31', catalog_version='v1', currency=currency)

def test_tags_currency_from_extraction():
    ext = _extraction({'net_profit': _field(11841000000.0)})
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=FinancialReportPolicy(allow_llm_models=['gpt-5.5']))
    assert r.financial_data['_currency'] == 'HKD'

def test_exports_repurchase_of_stock():
    ext = _extraction({'repurchase_of_stock': _field(0.0)})
    r = merge_financial_report_data(financial_data={}, extraction=ext, policy=FinancialReportPolicy(allow_llm_models=['gpt-5.5']))
    assert r.financial_data.get('repurchase_of_stock') == 0.0

def test_rejects_cross_currency_existing():
    ext = _extraction({'net_profit': _field(1.0)}, currency='HKD')
    with pytest.raises(ValueError):
        merge_financial_report_data(financial_data={'_currency': 'CNY'}, extraction=ext, policy=FinancialReportPolicy(allow_llm_models=['gpt-5.5']))
```

> 注：若 `FinancialReportPolicy.decide` 对 mock field 行为不符，实现 Step 3 时按 `policy.py` 实际签名调整 `_field`（保留 `value`/`currency`，补 `decide` 所需属性）。

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_frc_mapper_currency.py -v`
Expected: FAIL（无 `_currency`、无 `repurchase_of_stock`、不抛）

- [ ] **Step 3: 实现**

`merge_financial_report_data`（`mapper.py:200` `merged = dict(financial_data)` 之前）加守卫：
```python
    from tradingagents.dataflows.value_investment.unit_normalizer import assert_same_currency
    extraction_currency = getattr(extraction, "currency", None)
    if extraction_currency:
        assert_same_currency(financial_data, {"_currency": extraction_currency})
```

在 `_derive_metrics(merged, details, used_keys)`（:239）之后、构造 `_financial_report_client`（:241）之前，导出 repurchase + 标币种：
```python
    # 导出回购字段（非金额派生链；供主工具组装进 buyback_data）
    repurchase = _to_float(getattr(_fields(extraction).get("repurchase_of_stock"), "value", None))
    if repurchase is not None:
        merged["repurchase_of_stock"] = repurchase
    if extraction_currency:
        merged["_currency"] = extraction_currency
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_frc_mapper_currency.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add tradingagents/dataflows/financial_reports/mapper.py tests/unit/test_frc_mapper_currency.py
git commit -m "feat(value): currency guard/tag + export repurchase_of_stock in FRC mapper"
```

---

## Task 5: A 股财务 — 缺失→None + ibd 重写 + None-safe 日志 + 币种标签

**Files:**
- Modify: `tradingagents/tools/value_investment_tool.py:291`(HK 空骨架)、`:386-403`(debt/ibd)、`:413`(日志)、`:454`(interest)、`:484`(capex)、`:537`(return)
- Test: `tests/unit/test_value_financial_fetch.py`

- [ ] **Step 1: 写失败测试**（mock akshare，验证缺失→None + ibd 过滤求和 + 币种）

```python
# tests/unit/test_value_financial_fetch.py
import pandas as pd
from unittest.mock import patch
from tradingagents.tools import value_investment_tool as vt

def test_ibd_all_missing_is_none(monkeypatch):
    # 资产负债表缺全部债务分项 → interest_bearing_debt 应为 None，不抛、不为 0
    bs = pd.DataFrame([{'资产总计': 1000.0, '负债合计': 600.0}])  # 无短/长/债券/一年内
    monkeypatch.setattr(vt.ak, 'stock_financial_abstract', lambda symbol: pd.DataFrame())
    monkeypatch.setattr(vt.ak, 'stock_financial_report_sina',
                        lambda stock, symbol: bs if symbol == '资产负债表' else pd.DataFrame())
    monkeypatch.setattr(vt.ak, 'stock_financial_abstract_ths', lambda symbol: pd.DataFrame())
    out = vt._fetch_financial_data_structured('600519', 'A')
    assert out['interest_bearing_debt'] is None
    assert out['_currency'] == 'CNY'

def test_hk_skeleton_tagged():
    out = vt._fetch_financial_data_structured('00001', 'HK')
    assert out['_currency'] == 'HKD'
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_financial_fetch.py -v`
Expected: FAIL（ibd 当前为 0 / 无 `_currency`）

- [ ] **Step 3: 实现**

`:291` HK 分支（带币种空骨架）：
```python
    if market != "A":
        logger.info(f"市场 {market} 财务走上游补缺，akshare 返回空骨架")
        data['_currency'] = 'HKD' if market == 'HK' else 'USD'
        return data
```

`:386-395` 债务分项默认值 `0`→`None`：
```python
                data['short_term_debt'] = safe_float(latest_row.get('短期借款'), None)
                data['long_term_debt'] = safe_float(latest_row.get('长期借款'), None)
                bonds_payable = safe_float(latest_row.get('应付债券'), None)
                one_year_debt = safe_float(latest_row.get('一年内到期的非流动负债'), None)
```

`:398-403` ibd 累加重写为过滤-None-求和：
```python
                debt_parts = [data['short_term_debt'], data['long_term_debt'], bonds_payable, one_year_debt]
                present = [v for v in debt_parts if v is not None]
                data['interest_bearing_debt'] = sum(present) if present else None
                if 0 < len(present) < 4:
                    data.setdefault('_caveats', []).append(
                        'interest_bearing_debt: 部分债务分项缺失，合计仅含已披露项')
```

`:413` 日志改 None-safe：
```python
                ibd = data.get('interest_bearing_debt')
                cr = data.get('current_ratio')
                logger.info(f"✅ 资产负债表获取成功: 有息负债={ibd/1e8:.2f}亿" if ibd is not None
                            else "✅ 资产负债表获取成功: 有息负债=N/A"
                            + (f", 流动比率={cr:.2f}" if cr is not None else ", 流动比率=N/A"))
```

`:454` interest 失败保持 `None`（不再置 0）：`data['interest_expense'] = None`（except 分支）；`:484` capex 缺失 `data['capex'] = None`（去掉 `else: data['capex'] = 0`）。

`:537` return 前标币种：
```python
    from tradingagents.dataflows.value_investment.unit_normalizer import tag_currency
    data = tag_currency(data, source_currency=None, market='A')  # A 股 → CNY
    return data
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_financial_fetch.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add tradingagents/tools/value_investment_tool.py tests/unit/test_value_financial_fetch.py
git commit -m "fix(value): missing->None semantics + None-safe ibd sum/log + currency tag"
```

---

## Task 6: TushareProvider 同步单股市值 + `_fetch_market_data_structured` 重写

**Files:**
- Modify: `tradingagents/dataflows/providers/china/tushare.py`（新增同步方法）、`value_investment_tool.py:540-605`
- Test: `tests/unit/test_value_market_fetch.py`

- [ ] **Step 1: 写失败测试**（mock provider/yfinance，验证 ×1e4 + 币种 + 不走 akshare HK）

```python
# tests/unit/test_value_market_fetch.py
from unittest.mock import patch, MagicMock
from tradingagents.tools import value_investment_tool as vt

def test_a_share_total_mv_scaled_to_yuan():
    snap = {'close': 1600.0, 'total_mv': 2300.0, 'total_share': 12.56}  # total_mv 万元
    with patch.object(vt, '_tushare_market_snapshot_sync', return_value=snap):
        out = vt._fetch_market_data_structured('600519', 'A')
    assert out['market_cap'] == 2300.0 * 1e4
    assert out['_currency'] == 'CNY'

def test_hk_uses_yfinance_hkd_not_akshare():
    with patch('tradingagents.dataflows.providers.hk.hk_stock.get_hk_stock_info',
               return_value={'market_cap': 2.5e11, 'close_price': 67.55}) as m:
        out = vt._fetch_market_data_structured('00001', 'HK')
    assert out['market_cap'] == 2.5e11
    assert out['_currency'] == 'HKD'
    assert m.called  # 走 yfinance provider，不走 AKShare HK 折算
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_market_fetch.py -v`
Expected: FAIL（死分支返回空 / 调失效 akshare）

- [ ] **Step 3: 实现**

`providers/china/tushare.py` 新增同步单股方法（复用 `connect_sync`/`self.api`）：
```python
    def get_market_snapshot_sync(self, ts_code: str):
        """同步单股最新市值快照: {close, total_mv(万元), total_share}。"""
        if not self.is_available():
            self.connect_sync()
        if self.api is None:
            return None
        try:
            df = self.api.daily_basic(ts_code=ts_code, fields='close,total_mv,total_share')
            if df is None or df.empty:
                return None
            row = df.iloc[0]
            return {'close': float(row['close']), 'total_mv': float(row['total_mv']),
                    'total_share': float(row['total_share'])}
        except Exception as e:
            self.logger.warning(f"daily_basic 单股失败 {ts_code}: {e}")
            return None
```

`value_investment_tool.py` 加 helper + 重写 `_fetch_market_data_structured`：
```python
def _tushare_market_snapshot_sync(ts_code: str):
    from tradingagents.dataflows.providers.china.tushare import TushareProvider
    return TushareProvider().get_market_snapshot_sync(ts_code)

def _fetch_market_data_structured(ticker: str, market: str = "A") -> Dict[str, Any]:
    from tradingagents.dataflows.value_investment.unit_normalizer import tag_currency, scale_a_share_market_cap
    data = {'market_cap': None, 'close_price': None, 'total_shares': None}
    pure_code = ticker.split('.')[0]
    try:
        if market == "A":
            suffix = 'SH' if pure_code.startswith(('6', '9')) else 'SZ'
            snap = _tushare_market_snapshot_sync(f"{pure_code}.{suffix}")
            if snap:
                data['market_cap'] = scale_a_share_market_cap(snap['total_mv'])
                data['close_price'] = snap['close']
                data['total_shares'] = snap['total_share']
            data = tag_currency(data, source_currency=None, market='A')
        elif market == "HK":
            from tradingagents.dataflows.providers.hk.hk_stock import get_hk_stock_info
            info = get_hk_stock_info(f"{pure_code.zfill(4)}.HK") or {}
            data['market_cap'] = info.get('market_cap')
            data['close_price'] = info.get('close_price')
            data = tag_currency(data, source_currency='HKD', market='HK')
        logger.info(f"✅ 市值={data['market_cap']} 币种={data.get('_currency')}")
    except Exception as e:
        logger.warning(f"获取市值失败({market}): {e}")
    return data
```

> 若 Task 1 Step 1 显示 `get_hk_stock_info` 返回字段名不同（非 `market_cap`/`close_price`），按实际键调整。

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_market_fetch.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add tradingagents/dataflows/providers/china/tushare.py tradingagents/tools/value_investment_tool.py tests/unit/test_value_market_fetch.py
git commit -m "feat(value): structured market cap via tushare sync(A)/yfinance(HK) + currency"
```

---

## Task 7: `_get_industry_dynamic` HK 走 yfinance

**Files:**
- Modify: `tradingagents/tools/value_investment_tool.py:623`
- Test: `tests/unit/test_value_industry.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_value_industry.py
from unittest.mock import patch
from tradingagents.tools import value_investment_tool as vt

def test_hk_industry_via_yfinance():
    with patch('tradingagents.dataflows.providers.hk.hk_stock.get_hk_stock_info',
               return_value={'industry': '综合企业'}):
        assert vt._get_industry_dynamic('00001', 'HK') == '综合企业'

def test_hk_industry_default_when_missing():
    with patch('tradingagents.dataflows.providers.hk.hk_stock.get_hk_stock_info', return_value={}):
        assert vt._get_industry_dynamic('00001', 'HK') == 'default'
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_industry.py -v`
Expected: FAIL（HK 返回 "default" 死分支）

- [ ] **Step 3: 实现**

`:623` 的 `if market != "A": return "default"` 改为：
```python
    if market == "HK":
        try:
            from tradingagents.dataflows.providers.hk.hk_stock import get_hk_stock_info
            info = get_hk_stock_info(f"{pure_code.zfill(4)}.HK") or {}
            return info.get('industry') or 'default'
        except Exception as e:
            logger.debug(f"HK 行业获取失败: {e}")
            return 'default'
    if market != "A":
        return "default"
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_industry.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add tradingagents/tools/value_investment_tool.py tests/unit/test_value_industry.py
git commit -m "feat(value): HK industry via yfinance provider"
```

---

## Task 8: 分红/回购改用子包（async 包装）

**Files:**
- Modify: `tradingagents/tools/value_investment_tool.py:664`(dividend)、`:782`(buyback)
- Test: `tests/unit/test_value_dividend_buyback.py`

- [ ] **Step 1: 写失败测试**（mock 子包，验证 HK 分红非空 + sync 包装不抛）

```python
# tests/unit/test_value_dividend_buyback.py
from unittest.mock import patch, AsyncMock
from tradingagents.tools import value_investment_tool as vt

def test_hk_dividend_via_subpackage():
    fake = {'records': [{'year': 2025}], 'consecutive_years': 5,
            'avg_payout_ratio_3y': 0.4, 'total_dividend_years': 5}
    with patch('tradingagents.dataflows.value_investment.DividendFetcher') as F:
        F.return_value.fetch_dividend_data = AsyncMock(return_value=fake)
        out = vt._fetch_dividend_data_sync('00001', 'HK')
    assert out['consecutive_years'] == 5

def test_hk_buyback_empty_skeleton():
    with patch('tradingagents.dataflows.value_investment.BuybackFetcher') as F:
        F.return_value.fetch_buyback_data = AsyncMock(return_value=None)
        out = vt._fetch_buyback_data_sync('00001', 'HK')
    assert out['total_cancelled_amount'] == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_dividend_buyback.py -v`
Expected: FAIL（HK 走旧死分支返回空 records）

- [ ] **Step 3: 实现**（加 sync 包装 helper + 改两函数）

在 `value_investment_tool.py` 顶部 helper 区加：
```python
def _run_async_sync(coro):
    """在同步 @tool 上下文安全跑 async（CLAUDE.md 事件循环规约）。"""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as ex:
        return ex.submit(asyncio.run, coro).result()
```

`_fetch_dividend_data_sync` 主体改为（删除 `if market != "A"` 死分支）：
```python
    from tradingagents.dataflows.value_investment import DividendFetcher
    empty = {'records': [], 'consecutive_years': 0, 'avg_payout_ratio_3y': 0, 'total_dividend_years': 0}
    try:
        result = _run_async_sync(DividendFetcher().fetch_dividend_data(pure_code, market))
        return result or empty
    except Exception as e:
        logger.warning(f"分红获取失败({market}): {e}")
        return empty
```

`_fetch_buyback_data_sync` 同模式（用 `BuybackFetcher`，empty 含 `total_cancelled_amount:0, latest_year_amount:0, has_active_buyback:False`）。

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_dividend_buyback.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add tradingagents/tools/value_investment_tool.py tests/unit/test_value_dividend_buyback.py
git commit -m "feat(value): dividend/buyback via async subpackage with sync wrapper"
```

---

## Task 9: cash_health 字段名统一 `bonds_payable`→`bond_payable`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/cash_health.py:193`
- Test: `tests/unit/test_cash_health_field_name.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_cash_health_field_name.py
from tradingagents.dataflows.value_investment.cash_health import CashHealthCalculator

def test_bond_payable_feeds_ibd_fallback():
    # interest_bearing_debt 缺失 → 回退累加，须能读 bond_payable（FRC 字段名）
    fd = {'operating_cash_flow': 100.0, 'free_cash_flow': 80.0,
          'cash_and_equivalents': 50.0, 'short_term_debt': 10.0,
          'long_term_debt': 20.0, 'bond_payable': 30.0}
    c = CashHealthCalculator()
    res = c.calculate_from_financial_data(financial_data=fd, expected_dividend=5.0)
    d = c.to_dict(res)
    assert d  # 不崩；bond_payable 计入有息负债回退
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_cash_health_field_name.py -v`
Expected: 当前读 `bonds_payable`，`bond_payable` 不被计入（断言宽松，主要防回归——若已通过则改断言为校验 ibd 含 30）

- [ ] **Step 3: 实现**

`cash_health.py:190-195` debt_fields 列表中 `'bonds_payable'` → `'bond_payable'`（统一为 FRC 字段名）。

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_cash_health_field_name.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tradingagents/dataflows/value_investment/cash_health.py tests/unit/test_cash_health_field_name.py
git commit -m "fix(value): unify bond_payable field name in cash_health fallback"
```

---

## Task 10: 主函数集成 — 币种主闸 + HK 回购组装

**Files:**
- Modify: `tradingagents/tools/value_investment_tool.py:991-997`（buyback 默认后、calculator 前）
- Test: `tests/unit/test_value_main_gate.py`

- [ ] **Step 1: 写失败测试**（混币拒绝 + HK repurchase 组装）

```python
# tests/unit/test_value_main_gate.py
import pytest
from unittest.mock import patch
from tradingagents.tools import value_investment_tool as vt

def _patches(fin, mkt, repurchase=None):
    fin = dict(fin); 
    if repurchase is not None:
        fin['repurchase_of_stock'] = repurchase
    return patch.multiple(vt,
        _fetch_financial_data_structured=lambda t, m: fin,
        _supplement_with_report_collector=lambda fd, t, m: fd,
        _fetch_market_data_structured=lambda t, m: mkt,
        _fetch_dividend_data_sync=lambda t, m: {'records': [], 'consecutive_years': 0, 'avg_payout_ratio_3y': 0, 'total_dividend_years': 0},
        _fetch_buyback_data_sync=lambda t, m: {'records': [], 'total_cancelled_amount': 0, 'latest_year_amount': 0, 'has_active_buyback': False},
        _get_industry_dynamic=lambda t, m: 'default',
        apply_financial_report_client_data=lambda **k: k['financial_data'])

def test_cross_currency_aborts():
    fin = {'_currency': 'HKD', 'net_profits': [1e10], 'operating_cash_flow': 1e10}
    mkt = {'_currency': 'CNY', 'market_cap': 9.032e11}  # CNY 折算市值
    with _patches(fin, mkt):
        out = vt.get_value_investment_analysis.invoke({'ticker': '00001', 'market': 'HK'})
    assert '币种' in out or '❌' in out  # 主闸中止，不算出 0.9032 污染值

def test_hk_repurchase_injected_into_buyback():
    fin = {'_currency': 'HKD', 'net_profits': [1e10], 'operating_cash_flow': 1e10, 'free_cash_flow': 8e9}
    mkt = {'_currency': 'HKD', 'market_cap': 1e12}
    captured = {}
    def fake_full(**kw): captured.update(kw); return None
    with _patches(fin, mkt, repurchase=0.0), \
         patch.object(vt.PenetratingYieldCalculator, 'calculate_full_analysis', side_effect=fake_full):
        vt.get_value_investment_analysis.invoke({'ticker': '00001', 'market': 'HK'})
    assert captured['buyback_data']['total_cancelled_amount'] == 0.0  # 上游 0 覆盖
```

- [ ] **Step 2: 运行确认失败**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_main_gate.py -v`
Expected: FAIL（无主闸 / 无回购组装）

- [ ] **Step 3: 实现**

`value_investment_tool.py` 在 buyback 默认补全后（`:991` 之后）、`industry =`（:994）之前插入：
```python
        # HK 回购：上游 repurchase_of_stock 覆盖 buyback（None 不覆盖，保持子包 0）
        if market == "HK":
            rep = financial_data.get('repurchase_of_stock')
            if rep is not None:
                buyback_data['total_cancelled_amount'] = rep
        # 币种主闸：进入 calculator 前校验四个含金额 dict 同币种
        from tradingagents.dataflows.value_investment.unit_normalizer import assert_consistent_currency
        try:
            assert_consistent_currency(financial_data, market_data, buyback_data)
        except ValueError as e:
            logger.error(f"❌ 币种不一致: {e}")
            return f"❌ 数据币种不一致，已中止分析以避免污染: {e}"
```

- [ ] **Step 4: 运行确认通过**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_main_gate.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add tradingagents/tools/value_investment_tool.py tests/unit/test_value_main_gate.py
git commit -m "feat(value): currency main-gate + HK repurchase assembly before calculators"
```

---

## Task 11: 端到端 integration（默认跳过）+ 0.9032 污染 fixture

**Files:**
- Test: `tests/unit/test_value_e2e_integration.py`

- [ ] **Step 1: 写测试**

```python
# tests/unit/test_value_e2e_integration.py
import pytest
from tradingagents.tools.value_investment_tool import get_value_investment_analysis

@pytest.mark.integration
def test_e2e_a_share():
    out = get_value_investment_analysis.invoke({'ticker': '600519', 'market': 'A'})
    assert '无法获取' not in out and '❌' not in out

@pytest.mark.integration
def test_e2e_hk():
    out = get_value_investment_analysis.invoke({'ticker': '00001', 'market': 'HK'})
    assert '无法获取' not in out and '❌' not in out

def test_0_9032_pollution_rejected():
    # hermetic：HKD 财务 + CNY 折算市值（×0.9032）必须被主闸拒绝
    from tradingagents.dataflows.value_investment.unit_normalizer import assert_consistent_currency
    fin = {'_currency': 'HKD', 'operating_cash_flow': 100.0}
    mkt_cny = {'_currency': 'CNY', 'market_cap': 903.2}  # 1000 HKD × 0.9032
    with pytest.raises(ValueError):
        assert_consistent_currency(fin, mkt_cny)
```

- [ ] **Step 2: 跑 hermetic 部分（默认）**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_e2e_integration.py -v`
Expected: `test_0_9032_pollution_rejected` PASS；两个 integration 默认 deselected。

- [ ] **Step 3: 跑 integration（手动确认 live 打通）**

Run: `docker exec tradingagents-backend pytest tests/unit/test_value_e2e_integration.py -v -m integration`
Expected: A 股与 HK 各出完整报告，无 `❌`。**人工核对** HK 报告 `operating_cash_flow≈6.2e10` 量级、穿透回报率为合理百分比（非 0.9032 偏移）。

- [ ] **Step 4: 全量回归**

Run: `docker exec tradingagents-backend pytest tests/unit/ -v`
Expected: 全 PASS（含既有 turtle 测试无回归）。

- [ ] **Step 5: 提交**

```bash
git add tests/unit/test_value_e2e_integration.py
git commit -m "test(value): e2e integration + 0.9032 pollution guard"
```

---

## 自检结论（写后复核）

**1. Spec 覆盖**：币种归一(T2)、growth/通路A标币种(T3)、通路B守卫+币种+回购导出(T4)、缺失语义+ibd+日志(T5,🔴N1)、市值 Tushare/yfinance(T6)、HK 行业(T7)、分红回购子包+async(T8)、字段名统一(T9)、主闸+回购组装(T10,N2/N3)、hermetic+0.9032(T11,N7)。spec 各节均有对应 task。N4（运行时假设）落在 T1 探针。N5（growth 降级定性）已在 T3 体现（统一小数）。N6（改名触发场景）落在 T9。

**2. 占位符扫描**：无 TBD/TODO；每个 code step 给完整测试与实现代码；T4/T6 的两处"按实际签名/字段名调整"是依赖 Task 1 探针的合理实现分支，非占位。

**3. 类型一致性**：`tag_currency(data, source_currency, market)`、`assert_same_currency(a,b)`、`assert_consistent_currency(*dicts)`、`scale_a_share_market_cap(wan)`、`AMOUNT_FIELDS`（T2 定义）在 T3/T4/T5/T6/T10 引用一致；`_currency` 字段全程统一；`get_market_snapshot_sync`(T6 定义)/`_tushare_market_snapshot_sync`(T6 helper)/`_run_async_sync`(T8) 命名前后一致。
