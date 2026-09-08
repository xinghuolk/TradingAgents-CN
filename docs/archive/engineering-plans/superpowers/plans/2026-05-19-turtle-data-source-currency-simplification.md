# Turtle Data Source and Currency Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Turtle v0.15 use conservative data-source boundaries and simpler currency handling: extractor for report facts, market providers for market facts, same-currency native calculations, and no reliable HK payout/buyback facts from weak yfinance action semantics.

**Architecture:** Keep the existing Turtle modules, but tighten responsibilities. `report_adapter.py` derives report-based payout facts and broadens deterministic unit aliases. `market_adapter.py` stops fetching HK payout/buyback action facts from yfinance. `calculations.py` chooses target currency from formula inputs only and ignores unrelated money fields/FX metadata.

**Tech Stack:** Python 3.11, pytest, dataclasses, existing `financial-report-llm-extractor` public-client shape via fake test objects, existing Turtle dataclasses.

---

## File Map

- Modify `tradingagents/dataflows/value_investment/turtle/report_adapter.py`
  - Extend field aliases for `dividends_paid`.
  - Accept HKD/USD base unit aliases in `_field_unit`.
  - Derive a caveated report-based payout proxy from reliable `dividends_paid` and `net_profit`.
- Modify `tradingagents/dataflows/value_investment/turtle/market_adapter.py`
  - Keep HK market-cap fetch.
  - Stop using HK yfinance dividend/buyback helpers for Turtle action facts.
- Modify `tradingagents/dataflows/providers/hk/hk_stock.py`
  - Remove Turtle-facing `get_hk_dividend_data` / `get_hk_buyback_data` helpers added for the temporary HK action path.
  - Keep `get_hk_stock_info` and quote-related behavior.
- Modify `tradingagents/dataflows/value_investment/turtle/calculations.py`
  - Limit currency target selection to Turtle formula money fields.
  - Ignore unrelated FX metadata when same-currency formula inputs can compute natively.
- Modify tests:
  - `tests/unit/test_turtle_report_adapter.py`
  - `tests/unit/test_turtle_market_adapter.py`
  - `tests/unit/test_turtle_calculations.py`

## Task 1: Stop Promoting HK yfinance Action Data

**Files:**
- Modify: `tests/unit/test_turtle_market_adapter.py`
- Modify: `tradingagents/dataflows/value_investment/turtle/market_adapter.py`
- Modify: `tradingagents/dataflows/providers/hk/hk_stock.py`

- [ ] **Step 1: Replace the HK action routing test with conservative missing-action behavior**

In `tests/unit/test_turtle_market_adapter.py`, replace `test_get_turtle_market_facts_routes_hk_actions_to_hk_providers` with:

```python
def test_get_turtle_market_facts_does_not_promote_hk_yfinance_actions(monkeypatch):
    def reject_legacy_hk_action_fetch(ticker, market):
        raise AssertionError("HK action facts must not use the A-share dividend/buyback fetchers")

    def reject_hk_action_provider(ticker):
        raise AssertionError("HK yfinance action facts must not be promoted to Turtle facts")

    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_dividend_data_sync",
        reject_legacy_hk_action_fetch,
    )
    monkeypatch.setattr(
        "tradingagents.tools.value_investment_tool._fetch_buyback_data_sync",
        reject_legacy_hk_action_fetch,
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.providers.hk.hk_stock.get_hk_stock_info",
        lambda ticker: {"market_cap": 2_000_000_000_000, "price": 400.0},
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.providers.hk.hk_stock.get_hk_dividend_data",
        reject_hk_action_provider,
        raising=False,
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.providers.hk.hk_stock.get_hk_buyback_data",
        reject_hk_action_provider,
        raising=False,
    )

    facts = get_turtle_market_facts("0700.HK", "HK", "stock_connect")

    assert facts.fields["market_cap"].value.currency == "HKD"
    assert "dividend_avg_payout_ratio_3y" not in facts.fields
    assert "buyback_amount" not in facts.fields
    caveats = " ".join(facts.caveats)
    assert "dividend data missing" in caveats
    assert "buyback data missing" in caveats
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py::test_get_turtle_market_facts_does_not_promote_hk_yfinance_actions -q
```

Expected: fail because `get_turtle_market_facts()` still calls HK action provider wrappers.

- [ ] **Step 3: Implement conservative HK action handling**

In `tradingagents/dataflows/value_investment/turtle/market_adapter.py`:

- Delete `_fetch_hk_dividend_data()`.
- Delete `_fetch_hk_buyback_data()`.
- Change `_fetch_turtle_dividend_data()` so HK returns `None`.
- Change `_fetch_turtle_buyback_data()` so HK returns `None`.

The final helper behavior should be:

```python
def _fetch_turtle_dividend_data(ticker: str, market: str) -> dict[str, Any] | None:
    if _is_hk_market(market):
        return None

    from tradingagents.tools.value_investment_tool import _fetch_dividend_data_sync

    return _fetch_dividend_data_sync(ticker, market)


def _fetch_turtle_buyback_data(ticker: str, market: str) -> dict[str, Any] | None:
    if _is_hk_market(market):
        return None

    from tradingagents.tools.value_investment_tool import _fetch_buyback_data_sync

    return _fetch_buyback_data_sync(ticker, market)
```

In `tradingagents/dataflows/providers/hk/hk_stock.py`:

- Remove `get_dividend_data()`, `get_buyback_data()`, `_dividend_records()`, `_cashflow_row()`, `_year_from_cashflow_column()`, `_calculate_consecutive_years()`.
- Remove module-level `get_hk_dividend_data()` and `get_hk_buyback_data()`.
- Remove `payout_ratio` and `dividend_rate` from `get_stock_info()` so HK provider does not advertise Turtle action semantics.
- Remove `_normalize_ratio()` if no remaining code calls it.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py::test_get_turtle_market_facts_does_not_promote_hk_yfinance_actions -q
```

Expected: pass.

- [ ] **Step 5: Run full market adapter tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add tradingagents/dataflows/value_investment/turtle/market_adapter.py tradingagents/dataflows/providers/hk/hk_stock.py tests/unit/test_turtle_market_adapter.py
git commit -m "fix: stop promoting hk action provider facts"
```

## Task 2: Derive Caveated Report Payout Proxy

**Files:**
- Modify: `tests/unit/test_turtle_report_adapter.py`
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`

- [ ] **Step 1: Add report-derived payout proxy tests**

Append these tests to `tests/unit/test_turtle_report_adapter.py`:

```python
def test_report_adapter_derives_caveated_single_year_payout_proxy():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HKD", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    payout = facts.fields["dividend_avg_payout_ratio_3y"]
    assert payout.value == 0.35
    assert payout.source_reference == "dividends_paid p.7; net_profit p.7"
    assert payout.caveat == "single-year report payout proxy; not a 3-year average"
    assert "single-year report payout proxy; not a 3-year average" in facts.caveats
```

```python
def test_report_adapter_does_not_derive_payout_from_unreliable_dividend_field():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField(
            "dividends_paid",
            Decimal("-35"),
            currency="HKD",
            unit="million",
            is_reliable=False,
            source="llm",
            confidence="llm_supplement",
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "dividend_avg_payout_ratio_3y" not in facts.fields
```

```python
def test_report_adapter_does_not_derive_payout_from_mixed_currency_fields():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
        "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="CNY", unit="million"),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert "dividend_avg_payout_ratio_3y" not in facts.fields
    assert "report payout proxy skipped: currency mismatch" in facts.caveats
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py::test_report_adapter_derives_caveated_single_year_payout_proxy tests/unit/test_turtle_report_adapter.py::test_report_adapter_does_not_derive_payout_from_unreliable_dividend_field tests/unit/test_turtle_report_adapter.py::test_report_adapter_does_not_derive_payout_from_mixed_currency_fields -q
```

Expected: fail because report adapter does not derive `dividend_avg_payout_ratio_3y`.

- [ ] **Step 3: Add alias and helper functions**

In `tradingagents/dataflows/value_investment/turtle/report_adapter.py`:

1. Add alias:

```python
TURTLE_FIELD_ALIASES = {
    "capital_expenditures": "capex",
    "cash_and_equivalents": "cash",
    "money_cap": "cash",
    "dividends_paid": "dividends_paid",
}
```

2. Add a module constant near aliases:

```python
PAYOUT_PROXY_CAVEAT = "single-year report payout proxy; not a 3-year average"
```

3. Add helper after `_append_caveat()`:

```python
def _reliable_money_field(fields: dict[str, TurtleFactValue], name: str) -> TurtleFactValue | None:
    field = fields.get(name)
    if field is None or field.reliability != "reliable":
        return None
    if not isinstance(field.value, MoneyAmount) or field.value.reliability != "reliable":
        return None
    return field
```

4. Add payout derivation helper:

```python
def _derive_report_payout_proxy(
    fields: dict[str, TurtleFactValue],
    caveats: list[str],
) -> None:
    if "dividend_avg_payout_ratio_3y" in fields:
        return

    dividend = _reliable_money_field(fields, "dividends_paid")
    profit = _reliable_money_field(fields, "net_profit")
    if dividend is None or profit is None:
        return

    dividend_money = dividend.value
    profit_money = profit.value
    if dividend_money.currency.upper() != profit_money.currency.upper():
        _append_caveat(caveats, "report payout proxy skipped: currency mismatch")
        return

    try:
        dividend_amount = abs(float(dividend_money.to_hundred_million(target_currency=dividend_money.currency).value))
        profit_amount = float(profit_money.to_hundred_million(target_currency=profit_money.currency).value)
    except (TypeError, ValueError, OverflowError):
        _append_caveat(caveats, "report payout proxy skipped: invalid money value")
        return

    if profit_amount <= 0:
        _append_caveat(caveats, "report payout proxy skipped: non-positive net_profit")
        return

    fields["dividend_avg_payout_ratio_3y"] = TurtleFactValue(
        name="dividend_avg_payout_ratio_3y",
        value=dividend_amount / profit_amount,
        source_label="financial-report-client",
        source_reference=f"{dividend.source_reference}; {profit.source_reference}",
        reliability="reliable",
        caveat=PAYOUT_PROXY_CAVEAT,
    )
    _append_caveat(caveats, PAYOUT_PROXY_CAVEAT)
```

5. Before returning from `build_report_facts_from_extraction()`, call:

```python
    _derive_report_payout_proxy(adapted, caveats)
```

Place this call after the field loop and before `metadata = {...}`.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py::test_report_adapter_derives_caveated_single_year_payout_proxy tests/unit/test_turtle_report_adapter.py::test_report_adapter_does_not_derive_payout_from_unreliable_dividend_field tests/unit/test_turtle_report_adapter.py::test_report_adapter_does_not_derive_payout_from_mixed_currency_fields -q
```

Expected: pass.

- [ ] **Step 5: Run full report adapter tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add tradingagents/dataflows/value_investment/turtle/report_adapter.py tests/unit/test_turtle_report_adapter.py
git commit -m "feat: derive turtle payout from report facts"
```

## Task 3: Fix Currency Target Selection

**Files:**
- Modify: `tests/unit/test_turtle_calculations.py`
- Modify: `tradingagents/dataflows/value_investment/turtle/calculations.py`

- [ ] **Step 1: Add tests for unrelated money fields and unrelated FX**

Append these tests to `tests/unit/test_turtle_calculations.py`:

```python
def test_compute_turtle_signals_ignores_unrelated_money_field_currency_for_hkd_native_calculation():
    hkd_report = {
        "net_profit": money("net_profit", 100, "report.net_profit", currency="HKD"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="HKD"),
        "capex": money("capex", 20, "report.capex", currency="HKD"),
        "cash": money("cash", 500, "report.cash", currency="HKD"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="HKD"),
        "revenue": money("revenue", 999, "report.revenue", currency="USD"),
    }
    hkd_market = {
        "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
    ))

    assert signals.status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["owner_earnings"].unit == "hundred_million HKD"
    assert "FX rate required for HKD:CNY" not in signals.caveats
```

```python
def test_compute_turtle_signals_ignores_unrelated_fx_rates_for_hkd_native_calculation():
    hkd_report = {
        "net_profit": money("net_profit", 100, "report.net_profit", currency="HKD"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="HKD"),
        "capex": money("capex", 20, "report.capex", currency="HKD"),
        "cash": money("cash", 500, "report.cash", currency="HKD"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="HKD"),
    }
    hkd_market = {
        "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
    }

    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields=hkd_report,
        market_fields=hkd_market,
        report_metadata={"fx_rates": {"USD:CNY": 7.2}},
    ))

    assert signals.status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["owner_earnings"].unit == "hundred_million HKD"
    assert "FX rate required for HKD:CNY" not in signals.caveats
```

```python
def test_compute_turtle_signals_mixed_formula_currencies_require_relevant_fx():
    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields={
            "net_profit": money("net_profit", 100, "report.net_profit", currency="CNY"),
            "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf", currency="CNY"),
            "capex": money("capex", 20, "report.capex", currency="CNY"),
            "cash": money("cash", 500, "report.cash", currency="CNY"),
            "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt", currency="CNY"),
        },
        market_fields={
            "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
            "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
        },
        report_metadata={"fx_rates": {"USD:CNY": 7.2}},
    ))

    assert signals.status == "non_decisionable"
    assert signals.results["R"].value is None
    assert "market_cap" in signals.results["R"].missing_inputs
    assert "FX rate required for HKD:CNY" in signals.caveats
```

- [ ] **Step 2: Run tests to verify at least the first two fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py::test_compute_turtle_signals_ignores_unrelated_money_field_currency_for_hkd_native_calculation tests/unit/test_turtle_calculations.py::test_compute_turtle_signals_ignores_unrelated_fx_rates_for_hkd_native_calculation tests/unit/test_turtle_calculations.py::test_compute_turtle_signals_mixed_formula_currencies_require_relevant_fx -q
```

Expected: first two fail with native HKD calculation blocked; mixed-currency test may already pass.

- [ ] **Step 3: Limit currency detection to formula money fields**

In `tradingagents/dataflows/value_investment/turtle/calculations.py`, add constants near `_fx_rates()`:

```python
FORMULA_MONEY_FIELDS = (
    "net_profit",
    "operating_cash_flow",
    "capex",
    "cash",
    "interest_bearing_debt",
    "market_cap",
    "buyback_amount",
)
```

Replace `_money_fact_currencies()` with:

```python
def _money_fact_currencies(facts: TurtleFacts, names: Iterable[str] = FORMULA_MONEY_FIELDS) -> set[str]:
    currencies: set[str] = set()
    for name in names:
        for fact in _field_candidates(facts, name):
            if not isinstance(fact.value, MoneyAmount):
                continue
            if fact.reliability != "reliable" or fact.value.reliability != "reliable":
                continue
            if isinstance(fact.value.value, bool) or not isinstance(fact.value.value, (int, float)):
                continue
            if not math.isfinite(float(fact.value.value)):
                continue
            currencies.add(fact.value.currency.upper())
            break
    return currencies
```

Replace `_money_target_currency()` with:

```python
def _money_target_currency(facts: TurtleFacts) -> str:
    currencies = _money_fact_currencies(facts)
    if len(currencies) == 1:
        return next(iter(currencies))
    return "CNY"
```

This deliberately ignores unrelated FX rates when all formula money facts share one currency. Mixed-currency cases still choose CNY and require relevant `SOURCE:CNY` rates during `to_hundred_million()`.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py::test_compute_turtle_signals_ignores_unrelated_money_field_currency_for_hkd_native_calculation tests/unit/test_turtle_calculations.py::test_compute_turtle_signals_ignores_unrelated_fx_rates_for_hkd_native_calculation tests/unit/test_turtle_calculations.py::test_compute_turtle_signals_mixed_formula_currencies_require_relevant_fx -q
```

Expected: pass.

- [ ] **Step 5: Run all calculation tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py -q
```

Expected: all tests pass. If `test_compute_turtle_signals_converts_hk_money_with_report_fx_rates` still expects FX conversion for all-HKD inputs, update that test to represent mixed-currency conversion instead:

```python
def test_compute_turtle_signals_converts_mixed_hkd_money_with_report_fx_rates():
    signals = compute_turtle_signals(base_facts(
        market="HK",
        report_fields={
            "net_profit": money("net_profit", 92, "report.net_profit", currency="CNY"),
            "operating_cash_flow": money("operating_cash_flow", 110.4, "report.ocf", currency="CNY"),
            "capex": money("capex", 18.4, "report.capex", currency="CNY"),
            "cash": money("cash", 460, "report.cash", currency="CNY"),
            "interest_bearing_debt": money("interest_bearing_debt", 46, "report.debt", currency="CNY"),
        },
        market_fields={
            "market_cap": money("market_cap", 1000, "market.market_cap", currency="HKD"),
            "buyback_amount": money("buyback_amount", 10, "market.buyback", currency="HKD"),
        },
        report_metadata={"fx_rates": {"HKD:CNY": 0.92}},
    ))

    assert signals.status == "complete"
    assert signals.results["R"].value == pytest.approx(5.0)
    assert signals.results["GG"].value == pytest.approx(5.0)
    assert any("FX HKD:CNY=0.92" in source for source in signals.results["R"].sources)
```

- [ ] **Step 6: Commit**

Run:

```bash
git add tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_calculations.py
git commit -m "fix: choose turtle currency from formula inputs"
```

## Task 4: Accept HKD/USD Unit Aliases in Report Adapter

**Files:**
- Modify: `tests/unit/test_turtle_report_adapter.py`
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`

- [ ] **Step 1: Add unit alias test**

Append this parameterized test to `tests/unit/test_turtle_report_adapter.py`:

```python
import pytest
```

If `pytest` is already imported by a previous task, do not add a duplicate import.

```python
@pytest.mark.parametrize(
    ("currency", "unit", "expected_unit"),
    [
        ("HKD", "HKD", "yuan"),
        ("HKD", "HK$", "yuan"),
        ("HKD", "港元", "yuan"),
        ("HKD", "HK$ million", "million"),
        ("HKD", "HKD'000", "thousand"),
        ("USD", "USD", "yuan"),
        ("USD", "US$", "yuan"),
        ("USD", "US$ million", "million"),
        ("CNY", "RMB million", "million"),
    ],
)
def test_report_adapter_accepts_common_currency_unit_aliases(currency, unit, expected_unit):
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("123"), currency=currency, unit=unit),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    field = facts.fields["net_profit"]
    assert isinstance(field.value, MoneyAmount)
    assert field.value.currency == currency
    assert field.value.unit == expected_unit
    assert field.reliability == "reliable"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py::test_report_adapter_accepts_common_currency_unit_aliases -q
```

Expected: fail for HKD/USD base-unit aliases.

- [ ] **Step 3: Extend `_field_unit()` aliases**

In `tradingagents/dataflows/value_investment/turtle/report_adapter.py`, update `_field_unit()` to normalize common aliases:

```python
def _field_unit(field: Any) -> MoneyUnit | None:
    raw = str(getattr(field, "unit", "") or "").strip()
    lowered = raw.lower()
    compact = lowered.replace(" ", "")

    if lowered in {"yuan", "rmb", "cny", "hkd", "hk$", "usd", "us$"} or raw in {"港元", "港币", "美元", "人民币"}:
        return "yuan"
    if lowered in {"thousand", "rmb'000", "hkd'000", "usd'000", "000", "千元"}:
        return "thousand"
    if compact in {"rmb000", "hkd000", "usd000"}:
        return "thousand"
    if "million" in lowered or raw in {"百万", "百万元"}:
        return "million"
    if raw in {"万元"}:
        return "ten_thousand"
    if lowered in {"ten_thousand", "ten thousand"}:
        return "ten_thousand"
    if lowered in {"hundred_million", "hundred million"} or raw in {"亿元"}:
        return "hundred_million"
    return None
```

Keep the return type as `MoneyUnit | None`.

- [ ] **Step 4: Run focused test**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py::test_report_adapter_accepts_common_currency_unit_aliases -q
```

Expected: pass.

- [ ] **Step 5: Run all report adapter tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add tradingagents/dataflows/value_investment/turtle/report_adapter.py tests/unit/test_turtle_report_adapter.py
git commit -m "fix: accept report currency unit aliases"
```

## Task 5: Full Regression and PR Update

**Files:**
- Verify only unless failures require fixes.

- [ ] **Step 1: Run focused Turtle suite**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_facts.py tests/unit/test_turtle_report_adapter.py tests/unit/test_turtle_market_adapter.py tests/unit/test_turtle_calculations.py tests/unit/test_turtle_decision.py tests/unit/test_turtle_value_analyst_integration.py tests/unit/test_value_analyst_entry.py tests/unit/test_financial_report_integration.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git diff --stat origin/main...HEAD
git status --short
```

Expected:

- only intentional Turtle/report/HK provider/test/spec/plan files are changed relative to the PR branch history;
- working tree has no tracked modifications after commits;
- untracked `.codex/` may remain and must not be committed.

- [ ] **Step 4: Push branch**

Run:

```bash
git push origin feat/turtle-v015-flow-design
```

Expected: remote PR branch updates successfully.

- [ ] **Step 5: Summarize**

Final response should include:

- commits created;
- tests run and pass counts;
- whether `.codex/` remains untracked;
- the conservative behavior change: HK yfinance payout/buyback no longer become reliable Turtle facts.
