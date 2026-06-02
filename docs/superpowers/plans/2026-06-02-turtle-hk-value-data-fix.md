# Turtle HK Value Data Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HK value-investment Turtle calculations consume verified extractor money fields whose `unit` is `raw`, derive `interest_bearing_debt`, and pass an explicit HK holding channel.

**Architecture:** Keep currency authority in `currency`; treat `unit` only as a scale factor. Map verified `raw` money values to `MoneyAmount(unit="yuan", currency=<field.currency>)`, derive report-side aggregate debt from reliable money primitives, and resolve HK default holding channel before market facts are built.

**Tech Stack:** Python, pytest, Turtle value-investment adapters.

---

### Task 1: Raw Money Unit Adaptation

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`
- Test: `tests/unit/test_turtle_report_adapter.py`

- [ ] Add a failing test showing `FakeField(unit="raw", currency="HKD")` becomes a reliable `MoneyAmount` with `currency="HKD"` and `unit="yuan"`.
- [ ] Run `uv run pytest tests/unit/test_turtle_report_adapter.py::test_report_adapter_treats_raw_money_as_absolute_amount_with_field_currency -q` and confirm it fails because `raw` is unsupported.
- [ ] Update `_field_unit()` so `raw` maps to `yuan`; do not change `_field_currency()`.
- [ ] Re-run the focused test and `uv run pytest tests/unit/test_turtle_report_adapter.py -q`.

### Task 2: Report-Side Interest-Bearing Debt Derivation

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`
- Test: `tests/unit/test_turtle_report_adapter.py`

- [ ] Add a failing test where reliable `st_borr`, `lt_borr`, and `bond_payable` produce reliable `interest_bearing_debt`.
- [ ] Run the focused test and confirm `interest_bearing_debt` is absent.
- [ ] Add a focused derivation helper after payout derivation; require all three inputs to be reliable `MoneyAmount` in the same normalized currency.
- [ ] Re-run the focused test and the full turtle report adapter tests.

### Task 3: Explicit HK Holding Channel At Tool Entry

**Files:**
- Modify: `tradingagents/tools/turtle_analysis_tool.py`
- Test: `tests/unit/test_turtle_value_analyst_integration.py`

- [ ] Add or update a failing test showing `prepare_turtle_analysis_payload(..., market="HK", holding_channel=None)` passes `stock_connect` to `get_turtle_market_facts`.
- [ ] Run the focused test and confirm it currently receives `None`.
- [ ] Resolve HK missing holding channel to `default_holding_channel("HK")` before building context and market facts; preserve explicit caller-provided values.
- [ ] Re-run the focused integration tests.

### Task 4: Verification

**Files:**
- No new files.

- [ ] Run `uv run pytest tests/unit/test_turtle_report_adapter.py tests/unit/test_turtle_value_analyst_integration.py tests/unit/test_turtle_calculations.py tests/unit/test_turtle_market_adapter.py -q`.
- [ ] Inspect `git diff --stat` and `git diff` for accidental unrelated edits.
