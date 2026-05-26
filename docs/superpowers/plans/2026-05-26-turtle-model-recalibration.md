# Turtle Model Recalibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recalibrate Turtle v0.15 R/GG payout and buyback formulas to use report-side multi-period data, expose `payout_M`, and separate context-only caveats from material formula status.

**Architecture:** Keep the API payload schema unchanged and make the recalibration inside existing Turtle adapters/calculations. `report_adapter.py` derives reliable per-period payout ratios and maps `repurchase_of_stock` to canonical `buyback_amount`; `calculations.py` resolves payout/buyback inputs once, computes formulas from report-side fields only, and aggregates status from core material results instead of `facts.status`; `market_adapter.py` keeps market action records as context while preventing action-data caveats from degrading core model status.

**Tech Stack:** Python 3.10+, frozen dataclasses, pytest, Vue 3/TypeScript frontend verification via existing build/type-check commands.

**Spec:** `docs/superpowers/specs/2026-05-26-turtle-model-recalibration-design.md` at commit `cfe54a62`.

---

## Key Constraints

1. Use the latest Spec 2 revision: commitment payout ratio is not extracted; commitment-not-applied is context-only and does not degrade status when data is complete.
2. Keep `FormulaResult.substitution` as `str`. Do not introduce dict substitution.
3. Do not introduce `TurtleSourceReference` or `Decimal` in calculations. Use existing `list[str]` sources and `float` numeric helpers.
4. `TURTLE_FIELD_ALIASES` direction is extractor field id to Turtle canonical field name: `"repurchase_of_stock": "buyback_amount"`.
5. New payloads must output `results["payout_M"]` and must not output `results["payout_anchor"]`.
6. Market-side payout/buyback facts are context only. R/GG/HH and FX collection must not fall back to market-side `buyback_amount`.
7. `signals.status` must not read `facts.status`; it must be derived from core result statuses plus material caveats. `facts.status` may still be degraded for display-only report fields.
8. Run focused tests after each task. Run frontend `type-check` and `build` at the end because the payload remains structurally generic.

## File Map

- Modify `tradingagents/dataflows/value_investment/turtle/report_adapter.py`: add current-year reliable payout ratio derivation; keep display-only proxy; map `repurchase_of_stock`.
- Modify `tradingagents/dataflows/value_investment/turtle/market_adapter.py`: classify market action caveats as context-only for market status derivation.
- Modify `tradingagents/dataflows/value_investment/turtle/calculations.py`: add internal resolved input structs; compute `payout_M`; use report-side buyback 3-year average; update FX currency collection; aggregate `signals.status` from core material inputs.
- Modify `tradingagents/dataflows/value_investment/turtle/decision.py`: document tax asymmetry, buyback cancellation caveat, commitment hook, and payout_M bias in the prompt.
- Test `tests/unit/test_turtle_report_adapter.py`: report alias and payout derivation.
- Test `tests/unit/test_turtle_market_adapter.py`: context-only market action caveats do not degrade market status.
- Test `tests/unit/test_turtle_calculations.py`: formula migration, status aggregation, buyback semantics, HH behavior.
- Test `tests/unit/test_turtle_payload_fx.py`: report-side buyback triggers FX; market-only buyback does not.
- Test `tests/unit/test_turtle_decision.py`: prompt caveats.
- Test `tests/unit/test_turtle_value_analyst_integration.py`: old `payout_anchor` payload still rehydrates and new `payout_M` rehydrates.
- Verify `frontend/`: `npm run type-check` and `npm run build`.

---

## Task 1: Report Adapter Payout And Buyback Inputs

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`
- Test: `tests/unit/test_turtle_report_adapter.py`

- [ ] **Step 1.1: Write failing tests for reliable payout and buyback alias**

  Append these tests near the existing payout proxy tests in `tests/unit/test_turtle_report_adapter.py`:

  ```python
  def test_report_adapter_derives_reliable_current_year_payout_ratio_and_display_proxy():
      extraction = FakeExtraction(fields={
          "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
          "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HKD", unit="million"),
      })

      facts = build_report_facts_from_extraction(
          extraction=extraction,
          allow_llm_models=(),
          adapter_caveats=[],
      )

      current = facts.fields["dividend_payout_ratio_current_year"]
      assert current.value == 0.35
      assert current.reliability == "reliable"
      assert current.source_reference == "dividends_paid p.7; net_profit p.7"
      assert "derived from report dividends_paid/net_profit" in (current.caveat or "")

      proxy = facts.fields["dividend_payout_ratio_proxy_single_year"]
      assert proxy.value == 0.35
      assert proxy.reliability == "display_only"
      assert proxy.caveat == "single-year report payout proxy; not a 3-year average"


  def test_report_adapter_derives_current_year_payout_with_currency_aliases():
      extraction = FakeExtraction(fields={
          "net_profit": FakeField("net_profit", Decimal("100"), currency="HKD", unit="million"),
          "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HK$", unit="million"),
      })

      facts = build_report_facts_from_extraction(
          extraction=extraction,
          allow_llm_models=(),
          adapter_caveats=[],
      )

      assert facts.fields["dividend_payout_ratio_current_year"].value == 0.35
      assert "report payout proxy skipped: currency mismatch" not in facts.caveats


  def test_report_adapter_maps_repurchase_of_stock_to_buyback_amount():
      extraction = FakeExtraction(fields={
          "repurchase_of_stock": FakeField(
              "repurchase_of_stock",
              Decimal("123000000"),
              currency="HKD",
              unit="yuan",
          ),
      })

      facts = build_report_facts_from_extraction(
          extraction=extraction,
          allow_llm_models=(),
          adapter_caveats=[],
      )

      assert "buyback_amount" in facts.fields
      assert "repurchase_of_stock" not in facts.fields
      assert facts.fields["buyback_amount"].name == "buyback_amount"
      assert facts.fields["buyback_amount"].value.value == 123000000.0
      assert facts.fields["buyback_amount"].value.currency == "HKD"


  def test_report_adapter_skips_current_year_payout_when_profit_non_positive():
      extraction = FakeExtraction(fields={
          "net_profit": FakeField("net_profit", Decimal("0"), currency="HKD", unit="million"),
          "dividends_paid": FakeField("dividends_paid", Decimal("-35"), currency="HKD", unit="million"),
      })

      facts = build_report_facts_from_extraction(
          extraction=extraction,
          allow_llm_models=(),
          adapter_caveats=[],
      )

      assert "dividend_payout_ratio_current_year" not in facts.fields
      assert "dividend_payout_ratio_proxy_single_year" not in facts.fields
      assert "report payout ratio skipped: non-positive net_profit" in facts.caveats


  def test_report_adapter_derives_current_year_payout_for_historical_periods():
      class FakeAdapter:
          def get_annual_report_data(self, **kwargs):
              year = int(kwargs["period_end"][:4])
              ratio_by_year = {
                  2025: Decimal("-50"),
                  2024: Decimal("-40"),
                  2023: Decimal("-30"),
              }
              return FakeAdapterResult(
                  available=True,
                  company=kwargs["ticker"],
                  market=kwargs["market"],
                  period_end=kwargs["period_end"],
                  extraction=FakeExtraction(
                      period_end=kwargs["period_end"],
                      fields={
                          "net_profit": FakeField(
                              "net_profit",
                              Decimal("100"),
                              currency="HKD",
                              unit="million",
                          ),
                          "dividends_paid": FakeField(
                              "dividends_paid",
                              ratio_by_year[year],
                              currency="HKD",
                              unit="million",
                          ),
                      },
                  ),
                  warnings=[],
                  errors=[],
              )

      facts = get_turtle_report_facts(
          ticker="00700",
          market="HK",
          trade_date="2026-05-26",
          adapter=FakeAdapter(),
          allow_llm_models=(),
          history_periods=2,
      )

      assert facts.fields["dividend_payout_ratio_current_year"].value == 0.5
      assert facts.historical["2024-12-31"].fields["dividend_payout_ratio_current_year"].value == 0.4
      assert facts.historical["2023-12-31"].fields["dividend_payout_ratio_current_year"].value == 0.3
  ```

- [ ] **Step 1.2: Run tests to verify failure**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_report_adapter.py \
    -k "current_year_payout or repurchase_of_stock" -v
  ```

  Expected: the new tests fail because `dividend_payout_ratio_current_year` and `repurchase_of_stock` alias do not exist yet.

- [ ] **Step 1.3: Add alias, constants, and normalized currency import**

  In `tradingagents/dataflows/value_investment/turtle/report_adapter.py`, update the imports and constants:

  ```python
  from .facts import (
      MoneyAmount,
      MoneyUnit,
      TurtleFactValue,
      TurtleReportFacts,
      TurtleStatus,
      infer_turtle_period_end,
      normalize_currency,
  )


  TURTLE_FIELD_ALIASES = {
      "capital_expenditures": "capex",
      "cash_and_equivalents": "cash",
      "money_cap": "cash",
      "dividends_paid": "dividends_paid",
      "repurchase_of_stock": "buyback_amount",
  }

  CURRENT_YEAR_PAYOUT_FIELD = "dividend_payout_ratio_current_year"
  CURRENT_YEAR_PAYOUT_CAVEAT = (
      "current-year payout ratio derived from report dividends_paid/net_profit; "
      "not a commitment payout ratio"
  )
  PAYOUT_PROXY_FIELD = "dividend_payout_ratio_proxy_single_year"
  PAYOUT_PROXY_CAVEAT = "single-year report payout proxy; not a 3-year average"
  ```

- [ ] **Step 1.4: Replace payout proxy derivation with dual-field derivation**

  Replace `_derive_report_payout_proxy(...)` with:

  ```python
  def _derive_report_payout_fields(
      fields: dict[str, TurtleFactValue],
      caveats: list[str],
  ) -> None:
      dividend = _reliable_money_field(fields, "dividends_paid")
      profit = _reliable_money_field(fields, "net_profit")
      if dividend is None or profit is None:
          return

      dividend_money = dividend.value
      profit_money = profit.value
      if normalize_currency(dividend_money.currency) != normalize_currency(profit_money.currency):
          _append_caveat(caveats, "report payout ratio skipped: currency mismatch")
          return

      try:
          dividend_amount = abs(float(
              dividend_money.to_hundred_million(
                  target_currency=dividend_money.currency,
              ).value
          ))
          profit_amount = float(
              profit_money.to_hundred_million(
                  target_currency=profit_money.currency,
              ).value
          )
      except (TypeError, ValueError, OverflowError):
          _append_caveat(caveats, "report payout ratio skipped: invalid money value")
          return

      if profit_amount <= 0:
          _append_caveat(caveats, "report payout ratio skipped: non-positive net_profit")
          return

      ratio = dividend_amount / profit_amount
      if not isfinite(ratio):
          _append_caveat(caveats, "report payout ratio skipped: invalid payout ratio")
          return

      source_reference = f"{dividend.source_reference}; {profit.source_reference}"
      rounded_ratio = round(ratio, 12)

      if not _is_reliable_numeric_field(fields.get(CURRENT_YEAR_PAYOUT_FIELD)):
          fields[CURRENT_YEAR_PAYOUT_FIELD] = TurtleFactValue(
              name=CURRENT_YEAR_PAYOUT_FIELD,
              value=rounded_ratio,
              source_label="financial-report-client",
              source_reference=source_reference,
              reliability="reliable",
              caveat=CURRENT_YEAR_PAYOUT_CAVEAT,
          )

      if not isinstance(fields.get(PAYOUT_PROXY_FIELD), TurtleFactValue):
          fields[PAYOUT_PROXY_FIELD] = TurtleFactValue(
              name=PAYOUT_PROXY_FIELD,
              value=rounded_ratio,
              source_label="financial-report-client",
              source_reference=source_reference,
              reliability="display_only",
              caveat=PAYOUT_PROXY_CAVEAT,
          )
          _append_caveat(caveats, PAYOUT_PROXY_CAVEAT)
  ```

  In `build_report_facts_from_extraction(...)`, replace the existing call:

  ```python
      _derive_report_payout_proxy(adapted, caveats)
  ```

  with:

  ```python
      _derive_report_payout_fields(adapted, caveats)
  ```

- [ ] **Step 1.5: Run report adapter tests**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_report_adapter.py -v
  ```

  Expected: all report adapter tests pass. Update the existing expected caveat strings exactly as follows:

  ```python
  assert "report payout ratio skipped: invalid payout ratio" in facts.caveats
  assert "report payout ratio skipped: currency mismatch" in facts.caveats
  ```

- [ ] **Step 1.6: Commit**

  ```bash
  git add tradingagents/dataflows/value_investment/turtle/report_adapter.py tests/unit/test_turtle_report_adapter.py
  git commit -m "feat(turtle): derive report-side payout and buyback inputs"
  ```

---

## Task 2: Market Adapter Context-Only Action Caveats

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/market_adapter.py`
- Test: `tests/unit/test_turtle_market_adapter.py`

- [ ] **Step 2.1: Write failing market status tests**

  Append these tests in `tests/unit/test_turtle_market_adapter.py` near `TestMarketAdapterStatus`:

  ```python
  def test_market_action_missing_caveats_do_not_degrade_market_status():
      facts = build_market_facts(
          ticker="600519",
          market="A",
          holding_channel="long_term_domestic",
          market_data={"market_cap": 200_000_000_000, "close_price": 1500.0},
          dividend_data=None,
          buyback_data=None,
          industry="白酒",
          rf_rate=0.025,
      )

      assert "dividend data missing" in facts.caveats
      assert "buyback data missing" in facts.caveats
      assert facts.status == "complete"


  def test_market_partial_action_caveats_do_not_degrade_market_status():
      facts = build_market_facts(
          ticker="600519",
          market="A",
          holding_channel="long_term_domestic",
          market_data={"market_cap": 200_000_000_000, "close_price": 1500.0},
          dividend_data={"records": None},
          buyback_data={"records": []},
          industry="白酒",
          rf_rate=0.025,
      )

      joined = " ".join(facts.caveats)
      assert "avg_payout_ratio_3y missing" in joined
      assert "dividend records missing" in joined
      assert "buyback_amount missing" in joined
      assert facts.status == "complete"


  def test_market_material_caveat_still_degrades_market_status():
      facts = build_market_facts(
          ticker="600519",
          market="A",
          holding_channel=None,
          market_data={"market_cap": 200_000_000_000, "close_price": 1500.0},
          dividend_data=None,
          buyback_data=None,
          industry="白酒",
          rf_rate=0.025,
      )

      assert any("default holding_channel" in caveat for caveat in facts.caveats)
      assert facts.status == "degraded"
  ```

- [ ] **Step 2.2: Run tests to verify failure**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_market_adapter.py \
    -k "market_action_missing or partial_action or material_caveat" -v
  ```

  Expected: the first two tests fail because any caveat currently makes `TurtleMarketFacts.status` degraded.

- [ ] **Step 2.3: Add context-only caveat classifier**

  In `tradingagents/dataflows/value_investment/turtle/market_adapter.py`, add this constant and helper after `_BUILTIN_FIELDS`:

  ```python
  CONTEXT_ONLY_ACTION_CAVEATS = frozenset({
      "dividend data missing",
      "avg_payout_ratio_3y missing",
      "dividend records missing",
      "buyback data missing",
      "buyback_amount missing",
      "buyback_amount invalid",
  })


  def _is_status_affecting_caveat(caveat: str) -> bool:
      return caveat not in CONTEXT_ONLY_ACTION_CAVEATS
  ```

- [ ] **Step 2.4: Use classifier in market status derivation**

  Replace the status block at the end of `build_market_facts(...)` with:

  ```python
      external_fields = {k: v for k, v in fields.items() if k not in _BUILTIN_FIELDS}
      status_affecting_caveats = [caveat for caveat in caveats if _is_status_affecting_caveat(caveat)]

      if not external_fields:
          status: TurtleStatus = "non_decisionable"
      elif status_affecting_caveats or any(
          f.reliability != "reliable"
          or (isinstance(f.value, MoneyAmount) and f.value.reliability != "reliable")
          or f.caveat
          for f in fields.values()
      ):
          status = "degraded"
      else:
          status = "complete"
  ```

- [ ] **Step 2.5: Run market adapter tests**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_market_adapter.py -v
  ```

  Expected: all market adapter tests pass. If an existing test expected action missing to imply degraded status, change only that status assertion; keep caveat presence assertions.

- [ ] **Step 2.6: Commit**

  ```bash
  git add tradingagents/dataflows/value_investment/turtle/market_adapter.py tests/unit/test_turtle_market_adapter.py
  git commit -m "fix(turtle): keep market action caveats context-only"
  ```

---

## Task 3: Calculation Input Resolvers And `payout_M`

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/calculations.py`
- Test: `tests/unit/test_turtle_calculations.py`

- [ ] **Step 3.1: Refactor calculation test fixtures to report-side payout**

  In `tests/unit/test_turtle_calculations.py`, change `base_facts(...)` defaults so payout comes from report facts, not market facts:

  ```python
  def ratio(name, value, source, reliability="reliable"):
      return TurtleFactValue(
          name=name,
          value=value,
          source_label="fixture",
          source_reference=source,
          reliability=reliability,
      )


  def report_history(*period_ratios, buyback_values=(10, 8)):
      historical = {}
      for offset, payout_ratio in enumerate(period_ratios, start=1):
          fields = {}
          if payout_ratio is not None:
              fields["dividend_payout_ratio_current_year"] = ratio(
                  "dividend_payout_ratio_current_year",
                  payout_ratio,
                  f"report.payout.{offset}",
              )
          if offset - 1 < len(buyback_values) and buyback_values[offset - 1] is not None:
              fields["buyback_amount"] = money(
                  "buyback_amount",
                  buyback_values[offset - 1],
                  f"report.buyback.{offset}",
              )
          historical[f"{2025 - offset}-12-31"] = TurtleReportFacts(fields=fields)
      return historical
  ```

  Update `report_defaults` inside `base_facts(...)`:

  ```python
      report_defaults = {
          "net_profit": money("net_profit", 100, "report.net_profit"),
          "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf"),
          "capex": money("capex", 20, "report.capex"),
          "cash": money("cash", 500, "report.cash"),
          "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt"),
          "dividend_payout_ratio_current_year": ratio(
              "dividend_payout_ratio_current_year",
              0.5,
              "report.payout.latest",
          ),
          "buyback_amount": money("buyback_amount", 12, "report.buyback.latest"),
      }
  ```

  When constructing the report in `base_facts(...)`, pass default history:

  ```python
      report = TurtleReportFacts(
          fields=report_defaults,
          metadata=report_metadata or {},
          historical=report_history(0.4, 0.6),
      )
  ```

  Remove `buyback_amount`, `avg_payout_ratio_3y`, and `dividend_avg_payout_ratio_3y` from `market_defaults`. Keep only:

  ```python
      market_defaults = {
          "market_cap": money("market_cap", 1000, "market.market_cap"),
          "tax_rate": number("tax_rate", 0.2, "market.tax"),
          "rf_rate": number("rf_rate", 0.03, "market.rf"),
      }
  ```

- [ ] **Step 3.2: Write failing payout_M tests**

  Replace old `payout_anchor` tests with these tests:

  ```python
  def test_compute_turtle_signals_calculates_payout_m_r_gg_hh():
      signals = compute_turtle_signals(base_facts())

      assert signals.status == "complete"
      assert "payout_M" in signals.results
      assert "payout_anchor" not in signals.results
      assert signals.results["payout_M"].value == pytest.approx(0.5)
      assert signals.results["payout_M"].status == "complete"
      assert "commitment_ratio=null" in signals.results["payout_M"].substitution
      assert signals.results["R"].value == pytest.approx(4.9)
      assert signals.results["GG"].value == pytest.approx(4.9)
      assert signals.results["HH"].value == pytest.approx(0.0)


  def test_compute_turtle_signals_payout_m_uses_latest_signal_when_above_three_year_average():
      facts = base_facts(report_fields={
          "dividend_payout_ratio_current_year": ratio(
              "dividend_payout_ratio_current_year",
              0.8,
              "report.payout.latest",
          ),
      })

      signals = compute_turtle_signals(facts)

      assert signals.results["payout_M"].value == pytest.approx(0.8)
      assert "payout_3y_avg=0.6" in signals.results["payout_M"].substitution
      assert "latest_signal=0.8" in signals.results["payout_M"].substitution


  def test_compute_turtle_signals_payout_m_ignores_market_payout_fields():
      signals = compute_turtle_signals(base_facts(market_fields={
          "avg_payout_ratio_3y": number("avg_payout_ratio_3y", 0.99, "market.payout"),
          "dividend_avg_payout_ratio_3y": number(
              "dividend_avg_payout_ratio_3y",
              0.88,
              "dividend_data.avg_payout_ratio_3y",
          ),
      }))

      assert signals.results["payout_M"].value == pytest.approx(0.5)
      assert "market.payout" not in signals.results["payout_M"].sources
      assert "dividend_data.avg_payout_ratio_3y" not in signals.results["payout_M"].sources


  def test_compute_turtle_signals_payout_average_is_mean_of_per_year_ratios():
      facts = base_facts(report_fields={
          "dividend_payout_ratio_current_year": ratio(
              "dividend_payout_ratio_current_year",
              0.9,
              "report.payout.latest",
          ),
      })
      report = TurtleReportFacts(
          fields=facts.report.fields,
          metadata=facts.report.metadata,
          historical=report_history(0.3, 0.3),
      )
      facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

      signals = compute_turtle_signals(facts)

      assert signals.results["payout_M"].value == pytest.approx(0.9)
      assert "payout_3y_avg=0.5" in signals.results["payout_M"].substitution


  def test_compute_turtle_signals_payout_m_degraded_with_two_period_average():
      facts = base_facts()
      report = TurtleReportFacts(
          fields=facts.report.fields,
          metadata=facts.report.metadata,
          historical=report_history(0.4, None),
      )
      facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

      signals = compute_turtle_signals(facts)

      assert signals.results["payout_M"].value == pytest.approx(0.45)
      assert signals.results["payout_M"].status == "degraded"
      assert "dividend_payout_ratio_current_year_3y_avg computed from 2/3 periods" in signals.caveats


  def test_compute_turtle_signals_payout_m_degraded_with_latest_only():
      facts = base_facts()
      report = TurtleReportFacts(
          fields=facts.report.fields,
          metadata=facts.report.metadata,
          historical={},
      )
      facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

      signals = compute_turtle_signals(facts)

      assert signals.results["payout_M"].value == pytest.approx(0.5)
      assert signals.results["payout_M"].status == "degraded"
      assert "dividend_payout_ratio_current_year_3y_avg" in signals.results["payout_M"].missing_inputs


  def test_compute_turtle_signals_payout_m_non_decisionable_when_no_report_payout_inputs():
      facts = base_facts()
      report = TurtleReportFacts(
          fields={k: v for k, v in facts.report.fields.items() if k != "dividend_payout_ratio_current_year"},
          metadata=facts.report.metadata,
          historical={},
      )
      facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

      signals = compute_turtle_signals(facts)

      assert signals.status == "non_decisionable"
      assert signals.results["payout_M"].status == "non_decisionable"
      assert "dividend_payout_ratio_current_year" in signals.results["payout_M"].missing_inputs
      assert "dividend_payout_ratio_current_year_3y_avg" in signals.results["payout_M"].missing_inputs
  ```

- [ ] **Step 3.3: Run payout tests to verify failure**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_calculations.py \
    -k "payout_m or calculates_payout" -v
  ```

  Expected: failures reference missing `payout_M`, existing `payout_anchor`, and market payout fallback.

- [ ] **Step 3.4: Add resolver dataclasses and caveat classification helpers**

  In `calculations.py`, add `dataclass` import:

  ```python
  from dataclasses import dataclass
  ```

  Add constants and dataclasses after `FX_RELEVANT_MONEY_FIELDS`:

  ```python
  CURRENT_YEAR_PAYOUT_FIELD = "dividend_payout_ratio_current_year"
  COMMITMENT_CONTEXT_CAVEAT = (
      "commitment payout ratio not extracted; payout_M uses max(payout_3y_avg, latest_signal) "
      "without commitment cap"
  )
  BUYBACK_CANCELLATION_CONTEXT_CAVEAT = (
      "repurchase_of_stock used as buyback_amount input; cancellation progress is not verified"
  )
  MARKET_BUYBACK_EXCLUDED_CAVEAT = (
      "market buyback_amount present but excluded from R/GG; report-side buyback_amount_3y_avg is required"
  )
  PAYOUT_PROXY_CONTEXT_CAVEAT = "single-year report payout proxy; not a 3-year average"

  CONTEXT_ONLY_CAVEATS = frozenset({
      "dividend data missing",
      "avg_payout_ratio_3y missing",
      "dividend records missing",
      "buyback data missing",
      "buyback_amount missing",
      "buyback_amount invalid",
      COMMITMENT_CONTEXT_CAVEAT,
      BUYBACK_CANCELLATION_CONTEXT_CAVEAT,
      MARKET_BUYBACK_EXCLUDED_CAVEAT,
      PAYOUT_PROXY_CONTEXT_CAVEAT,
  })

  CORE_RESULT_KEYS = frozenset({
      "payout_M",
      "R",
      "GG",
      "HH",
      "net_cash_ratio",
      "ev_switch",
      "cash_protection",
      "owner_earnings",
  })


  @dataclass(frozen=True)
  class PayoutInputs:
      payout_3y_avg: float | None
      latest_signal: float | None
      commitment_ratio: float | None
      applied_value: float | None
      sources: list[str]
      missing_inputs: list[str]
      caveats: list[str]
      status: TurtleStatus


  @dataclass(frozen=True)
  class BuybackInputs:
      sources: list[str]
      missing_inputs: list[str]
      caveats: list[str]
      status: TurtleStatus
  ```

  Add helpers near `_append_caveat(...)`:

  ```python
  def _is_context_only_caveat(caveat: str) -> bool:
      return caveat in CONTEXT_ONLY_CAVEATS


  def _material_caveats(caveats: Iterable[str]) -> list[str]:
      return [caveat for caveat in caveats if not _is_context_only_caveat(caveat)]


  def _new_caveats(before: set[str], caveats: list[str]) -> list[str]:
      return [caveat for caveat in caveats if caveat not in before]
  ```

- [ ] **Step 3.5: Add report-only numeric helper and payout resolver**

  Add this helper after `_number(...)`:

  ```python
  def _number_report_latest(
      facts: TurtleFacts,
      name: str,
      caveats: list[str],
  ) -> tuple[float | None, list[str], list[str]]:
      fact = facts.report.fields.get(name)
      if fact is None:
          return None, [], [name]
      if isinstance(fact.value, bool) or not isinstance(fact.value, (int, float)):
          return None, [fact.source_reference], [name]
      if fact.reliability != "reliable":
          _append_caveat(caveats, f"{name} unreliable: {fact.reliability}")
          return None, [fact.source_reference], [name]
      value = float(fact.value)
      if not math.isfinite(value):
          _append_caveat(caveats, f"{name} invalid numeric value")
          return None, [fact.source_reference], [name]
      return value, [fact.source_reference], []
  ```

  Add `_resolve_payout_inputs(...)` after `_buyback_input(...)`:

  ```python
  def _resolve_payout_inputs(facts: TurtleFacts, caveats: list[str]) -> PayoutInputs:
      before = set(caveats)
      payout_3y_avg, avg_sources, missing_avg = _number_report_3y_avg(
          facts,
          CURRENT_YEAR_PAYOUT_FIELD,
          caveats,
      )
      avg_caveats = _new_caveats(before, caveats)

      latest_signal, latest_sources, missing_latest = _number_report_latest(
          facts,
          CURRENT_YEAR_PAYOUT_FIELD,
          caveats,
      )

      _append_caveat(caveats, COMMITMENT_CONTEXT_CAVEAT)
      resolver_caveats = _merge_sources(avg_caveats, [COMMITMENT_CONTEXT_CAVEAT])

      available = [
          value
          for value in (payout_3y_avg, latest_signal)
          if value is not None and math.isfinite(value)
      ]
      applied_value = max(available) if available else None

      missing_inputs: list[str] = []
      if missing_avg:
          missing_inputs = _merge_missing(missing_inputs, missing_avg)
      if missing_latest:
          missing_inputs = _merge_missing(missing_inputs, missing_latest)

      if applied_value is None:
          status: TurtleStatus = "non_decisionable"
      elif missing_avg or missing_latest or _material_caveats(avg_caveats):
          status = "degraded"
      else:
          status = "complete"

      return PayoutInputs(
          payout_3y_avg=payout_3y_avg,
          latest_signal=latest_signal,
          commitment_ratio=None,
          applied_value=applied_value,
          sources=_merge_sources(avg_sources, latest_sources),
          missing_inputs=missing_inputs,
          caveats=resolver_caveats,
          status=status,
      )
  ```

- [ ] **Step 3.6: Replace `payout_anchor` result with `payout_M` result**

  In `compute_turtle_signals(...)`, replace:

  ```python
      payout, payout_sources, missing_payout = _number_alias(
          facts,
          caveats,
          "avg_payout_ratio_3y",
          "dividend_avg_payout_ratio_3y",
      )
  ```

  with:

  ```python
      payout_inputs = _resolve_payout_inputs(facts, caveats)
      payout = payout_inputs.applied_value
      payout_sources = payout_inputs.sources
      missing_payout = payout_inputs.missing_inputs if payout is None else []
  ```

  Replace `results["payout_anchor"] = ...` with:

  ```python
      if payout is None:
          payout_substitution = (
              "payout_M = max(payout_3y_avg, latest_signal); "
              "commitment_ratio=null, commitment_constraint_applied=false"
          )
      else:
          payout_substitution = (
              f"payout_M = max(payout_3y_avg="
              f"{'null' if payout_inputs.payout_3y_avg is None else _fmt(payout_inputs.payout_3y_avg)}, "
              f"latest_signal="
              f"{'null' if payout_inputs.latest_signal is None else _fmt(payout_inputs.latest_signal)}) "
              f"= {_fmt(payout)}; commitment_ratio=null, commitment_constraint_applied=false"
          )

      results["payout_M"] = _result(
          name="payout_M",
          formula="payout_M = max(payout_3y_avg, latest_signal); commitment cap not applied",
          substitution=payout_substitution,
          value=payout,
          unit="ratio",
          sources=payout_sources,
          missing_inputs=payout_inputs.missing_inputs,
          status=payout_inputs.status,
      )
  ```

- [ ] **Step 3.7: Run payout_M tests**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_calculations.py \
    -k "payout_m or calculates_payout" -v
  ```

  Expected: the payout tests pass. Other calculation tests still fail until buyback and status tasks migrate old assumptions.

  Delete these old tests after the new market-ignore coverage above exists:

  ```text
  test_compute_turtle_signals_accepts_integrated_dividend_payout_field
  test_compute_turtle_signals_uses_reliable_later_numeric_alias_when_first_alias_display_only
  ```

  They verify market payout fallback, which Spec 2 intentionally removes.

- [ ] **Step 3.8: Commit**

  ```bash
  git add tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_calculations.py
  git commit -m "feat(turtle): resolve report-side payout_M"
  ```

---

## Task 4: Report-Side Buyback 3-Year Average In R/GG/HH

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/calculations.py`
- Test: `tests/unit/test_turtle_calculations.py`

- [ ] **Step 4.1: Write failing buyback and HH tests**

  Add these tests to `tests/unit/test_turtle_calculations.py`:

  ```python
  def test_compute_turtle_signals_uses_report_side_buyback_3y_average():
      signals = compute_turtle_signals(base_facts())

      assert signals.results["R"].value == pytest.approx(4.9)
      assert signals.results["GG"].value == pytest.approx(4.9)
      assert "buyback_amount_3y_avg" in signals.results["R"].formula
      assert "+ 10" in signals.results["R"].substitution
      assert "market.buyback" not in signals.results["R"].sources


  def test_compute_turtle_signals_ignores_market_buyback_when_report_buyback_exists():
      signals = compute_turtle_signals(base_facts(market_fields={
          "buyback_amount": money("buyback_amount", 999, "market.buyback"),
      }))

      assert signals.results["R"].value == pytest.approx(4.9)
      assert "market.buyback" not in signals.results["R"].sources


  def test_compute_turtle_signals_report_buyback_missing_degrades_r_gg_only():
      facts = base_facts()
      report = TurtleReportFacts(
          fields={k: v for k, v in facts.report.fields.items() if k != "buyback_amount"},
          metadata=facts.report.metadata,
          historical={
              pe: TurtleReportFacts(
                  fields={k: v for k, v in hist.fields.items() if k != "buyback_amount"},
                  metadata=hist.metadata,
                  caveats=hist.caveats,
                  status=hist.status,
              )
              for pe, hist in facts.report.historical.items()
          },
      )
      facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

      signals = compute_turtle_signals(facts)

      assert signals.status == "degraded"
      assert signals.results["R"].status == "degraded"
      assert signals.results["GG"].status == "degraded"
      assert signals.results["HH"].status == "complete"
      assert "buyback_amount_3y_avg" in signals.results["R"].missing_inputs
      assert "buyback_amount_3y_avg" in signals.results["GG"].missing_inputs
      assert "buyback_amount_3y_avg" not in signals.results["HH"].missing_inputs
      assert "buyback_amount_3y_avg missing; treated as 0 for degraded calculation" in signals.caveats


  def test_compute_turtle_signals_market_buyback_only_is_excluded_from_formula():
      facts = base_facts(market_fields={
          "buyback_amount": money("buyback_amount", 999, "market.buyback"),
      })
      report = TurtleReportFacts(
          fields={k: v for k, v in facts.report.fields.items() if k != "buyback_amount"},
          metadata=facts.report.metadata,
          historical={
              pe: TurtleReportFacts(fields={
                  k: v for k, v in hist.fields.items() if k != "buyback_amount"
              })
              for pe, hist in facts.report.historical.items()
          },
      )
      facts = TurtleFacts(context=facts.context, report=report, market=facts.market, status="complete")

      signals = compute_turtle_signals(facts)

      assert signals.results["R"].value == pytest.approx(4.0)
      assert "market.buyback" not in signals.results["R"].sources
      assert "market buyback_amount present but excluded from R/GG" in " ".join(signals.caveats)
  ```

- [ ] **Step 4.2: Run buyback tests to verify failure**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_calculations.py \
    -k "buyback or hh" -v
  ```

  Expected: tests fail because `_money_hm` still reads market fallback and missing key/caveat still uses `buyback_amount`.

- [ ] **Step 4.3: Replace buyback helper with 3-year canonical naming**

  Replace `_buyback_input(...)` in `calculations.py` with:

  ```python
  def _buyback_3y_input(
      buyback_amount_3y_avg: float | None,
      missing_buyback: list[str],
      caveats: list[str],
  ) -> tuple[float, list[str], bool]:
      if missing_buyback or buyback_amount_3y_avg is None:
          _append_caveat(caveats, "buyback_amount_3y_avg missing; treated as 0 for degraded calculation")
          return 0.0, ["buyback_amount_3y_avg"], True
      return buyback_amount_3y_avg, [], False
  ```

  Add this resolver after `_resolve_payout_inputs(...)`:

  ```python
  def _resolve_buyback_inputs(facts: TurtleFacts, caveats: list[str]) -> BuybackInputs:
      if "buyback_amount" in facts.market.fields and "buyback_amount" not in facts.report.fields:
          _append_caveat(caveats, MARKET_BUYBACK_EXCLUDED_CAVEAT)
      if "buyback_amount" in facts.report.fields or any(
          "buyback_amount" in period.fields for period in facts.report.historical.values()
      ):
          _append_caveat(caveats, BUYBACK_CANCELLATION_CONTEXT_CAVEAT)
      return BuybackInputs(sources=[], missing_inputs=[], caveats=[], status="complete")
  ```

- [ ] **Step 4.4: Use one return currency for R/GG and report-only buyback average**

  In `compute_turtle_signals(...)`, replace target currency setup:

  ```python
      r_target_currency = _money_target_currency(facts, ("net_profit", "market_cap"))
      owner_target_currency = _money_target_currency(facts, ("operating_cash_flow", "capex", "market_cap"))
  ```

  with:

  ```python
      return_target_currency = _money_target_currency(
          facts,
          ("net_profit", "operating_cash_flow", "capex", "market_cap"),
      )
      r_target_currency = return_target_currency
      owner_target_currency = return_target_currency
  ```

  Replace both `_money_hm(facts, "buyback_amount", ...)` calls with report-side 3-year helper:

  ```python
      buyback_inputs = _resolve_buyback_inputs(facts, caveats)
      r_buyback, r_buyback_sources, missing_r_buyback = _money_hm_report_3y_avg(
          facts,
          "buyback_amount",
          caveats,
          r_target_currency,
      )
      r_buyback_for_formula, r_degraded_buyback_missing, r_buyback_degraded = _buyback_3y_input(
          r_buyback,
          missing_r_buyback,
          caveats,
      )
  ```

  Use the same `r_buyback`, `r_buyback_sources`, and `r_buyback_for_formula` for GG:

  ```python
      gg_buyback_for_formula = r_buyback_for_formula
      gg_degraded_buyback_missing = list(r_degraded_buyback_missing)
      gg_buyback_degraded = r_buyback_degraded
      gg_buyback_sources = list(r_buyback_sources)
  ```

  Keep `gg_market_cap` converted with `owner_target_currency`, which now equals `r_target_currency`.

- [ ] **Step 4.5: Update R/GG formula strings and source merges**

  Replace formula strings for R/GG:

  ```python
      r_substitution = "(net_profit * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100"
  ```

  and:

  ```python
      formula="R = (net_profit * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100",
  ```

  For GG use:

  ```python
      gg_substitution = "(owner_earnings * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100"
  ```

  and:

  ```python
      formula="GG = (owner_earnings * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100",
  ```

  Leave HH `missing_inputs` built only from `r_critical_missing` and `gg_critical_missing`; do not merge `r_degraded_buyback_missing` or `gg_degraded_buyback_missing` into HH.

- [ ] **Step 4.6: Run buyback and HH tests**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_calculations.py \
    -k "buyback or hh or calculates_payout" -v
  ```

  Expected: buyback/HH tests pass. Some status aggregation tests may still fail until Task 5.

- [ ] **Step 4.7: Commit**

  ```bash
  git add tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_calculations.py
  git commit -m "feat(turtle): use report-side buyback amount 3y average"
  ```

---

## Task 5: Core Status Aggregation And Context Caveats

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/calculations.py`
- Test: `tests/unit/test_turtle_calculations.py`

- [ ] **Step 5.1: Write failing status aggregation tests**

  Replace `test_compute_turtle_signals_degrades_when_caveats_exist_with_complete_critical_results` with:

  ```python
  def test_compute_turtle_signals_degrades_when_material_caveat_exists_with_complete_results():
      facts = base_facts(caveats=["rf_rate missing"])

      signals = compute_turtle_signals(facts)

      assert signals.status == "degraded"
      assert signals.results["R"].status == "complete"
      assert "rf_rate missing" in signals.caveats


  def test_compute_turtle_signals_does_not_degrade_for_context_only_action_caveat():
      facts = base_facts(caveats=["dividend data missing", "buyback data missing"])

      signals = compute_turtle_signals(facts)

      assert signals.status == "complete"
      assert signals.results["R"].status == "complete"
      assert "dividend data missing" in signals.caveats
      assert "buyback data missing" in signals.caveats


  def test_compute_turtle_signals_ignores_degraded_facts_status_when_core_results_complete():
      facts = base_facts(status="degraded", caveats=["single-year report payout proxy; not a 3-year average"])

      signals = compute_turtle_signals(facts)

      assert signals.status == "complete"
      assert signals.results["R"].status == "complete"
  ```

- [ ] **Step 5.2: Run status tests to verify failure**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_calculations.py \
    -k "material_caveat or context_only_action or degraded_facts_status" -v
  ```

  Expected: context-only and degraded-facts tests fail because current aggregation uses `facts.status` and any caveat.

- [ ] **Step 5.3: Replace final status aggregation**

  In `compute_turtle_signals(...)`, replace the final status block:

  ```python
      critical_non_decisionable = results["R"].status == "non_decisionable" or results["GG"].status == "non_decisionable"
      if critical_non_decisionable or facts.status == "non_decisionable":
          status: TurtleStatus = "non_decisionable"
      elif (
          facts.status == "degraded"
          or has_input_caveats
          or any(result.status in {"degraded", "non_decisionable"} for result in results.values())
      ):
          status = "degraded"
      else:
          status = "complete"
  ```

  with:

  ```python
      core_results = [result for key, result in results.items() if key in CORE_RESULT_KEYS]
      core_non_decisionable = any(result.status == "non_decisionable" for result in core_results)
      core_degraded = any(result.status == "degraded" for result in core_results)
      material_input_caveats = _material_caveats(caveats)

      if core_non_decisionable:
          status: TurtleStatus = "non_decisionable"
      elif core_degraded or material_input_caveats:
          status = "degraded"
      else:
          status = "complete"
  ```

  Remove `has_input_caveats = bool(caveats)` near the start of `compute_turtle_signals(...)` because it is no longer used.

- [ ] **Step 5.4: Run all calculation tests**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_calculations.py -v
  ```

  Expected: all calculation tests pass after updating old expectations from `payout_anchor` to `payout_M`, from market payout to report payout, and from `buyback_amount` missing key to `buyback_amount_3y_avg`.

- [ ] **Step 5.5: Commit**

  ```bash
  git add tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_calculations.py
  git commit -m "fix(turtle): aggregate signal status from core material results"
  ```

---

## Task 6: FX Collection For Report-Side Buyback Only

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/calculations.py`
- Test: `tests/unit/test_turtle_payload_fx.py`

- [ ] **Step 6.1: Write failing FX tests**

  Append these tests to `tests/unit/test_turtle_payload_fx.py`:

  ```python
  def test_collect_fx_currencies_includes_report_side_buyback():
      report = TurtleReportFacts(fields={
          "net_profit": _money_fact("net_profit", 5e8, "HKD", "rnp"),
          "market_cap": _money_fact("market_cap", 1e10, "HKD", "rmc"),
          "buyback_amount": _money_fact("buyback_amount", 1e8, "CNY", "rbb"),
      })
      market = TurtleMarketFacts(fields={})

      assert collect_fx_currencies(report, market) == {"HKD", "CNY"}


  def test_collect_fx_currencies_ignores_market_only_buyback():
      report = TurtleReportFacts(fields={
          "net_profit": _money_fact("net_profit", 5e8, "HKD", "rnp"),
          "market_cap": _money_fact("market_cap", 1e10, "HKD", "rmc"),
      })
      market = TurtleMarketFacts(fields={
          "buyback_amount": _money_fact("buyback_amount", 1e8, "CNY", "mbb"),
      })

      assert collect_fx_currencies(report, market) == {"HKD"}


  def test_payload_market_only_buyback_currency_does_not_trigger_fx():
      report = TurtleReportFacts(fields={
          "net_profit": _money_fact("net_profit", 5e8, "HKD", "rnp"),
          "market_cap": _money_fact("market_cap", 1e10, "HKD", "rmc"),
      }, status="complete")
      market = TurtleMarketFacts(
          fields={"buyback_amount": _money_fact("buyback_amount", 1e8, "CNY", "mbb")},
          status="complete",
          metadata={"market_as_of": "2026-05-23"},
      )

      with patch.object(tat, "resolve_fx_rates") as rfx:
          out = _run(report, market)

      rfx.assert_not_called()
      assert out["facts"]["report"]["status"] == "complete"
  ```

- [ ] **Step 6.2: Run FX tests to verify failure**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_payload_fx.py \
    -k "buyback" -v
  ```

  Expected: market-only buyback test fails because `collect_fx_currencies(...)` currently falls back to market for every money field.

- [ ] **Step 6.3: Prevent market fallback for buyback currency collection**

  In `collect_fx_currencies(...)`, replace:

  ```python
      for name in FX_RELEVANT_MONEY_FIELDS:
          cur = _usable_money_currency(report.fields.get(name))
          if cur is None:
              cur = _usable_money_currency(market.fields.get(name))
  ```

  with:

  ```python
      for name in FX_RELEVANT_MONEY_FIELDS:
          cur = _usable_money_currency(report.fields.get(name))
          if cur is None and name != "buyback_amount":
              cur = _usable_money_currency(market.fields.get(name))
  ```

- [ ] **Step 6.4: Run FX tests**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_payload_fx.py -v
  ```

  Expected: all FX tests pass. Existing market fallback test for `market_cap` must still pass because the no-fallback rule applies only to `buyback_amount`.

- [ ] **Step 6.5: Commit**

  ```bash
  git add tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_payload_fx.py
  git commit -m "fix(turtle): ignore market-only buyback for formula FX"
  ```

---

## Task 7: Prompt And Rehydration Compatibility

**Files:**
- Modify: `tradingagents/dataflows/value_investment/turtle/decision.py`
- Test: `tests/unit/test_turtle_decision.py`
- Test: `tests/unit/test_turtle_value_analyst_integration.py`

- [ ] **Step 7.1: Write failing prompt tests**

  Add this test to `tests/unit/test_turtle_decision.py`:

  ```python
  def test_decision_prompt_documents_spec2_model_caveats():
      facts = empty_facts()
      signals = TurtleComputedSignals(
          status="complete",
          results={
              "payout_M": FormulaResult(
                  "payout_M",
                  "payout_M = max(payout_3y_avg, latest_signal)",
                  "payout_M = max(payout_3y_avg=0.4, latest_signal=0.5) = 0.5; "
                  "commitment_ratio=null, commitment_constraint_applied=false",
                  0.5,
                  "ratio",
                  ["report.payout"],
              ),
              "R": FormulaResult(
                  "R",
                  "R = (net_profit * M * (1 - Q) + buyback_amount_3y_avg) / market_cap * 100",
                  "(100 * 0.5 * (1 - 0.2) + 10) / 1000 * 100",
                  5.0,
                  "percent",
                  ["report.net_profit", "report.buyback"],
              ),
          },
          caveats=[
              "commitment payout ratio not extracted; payout_M uses max(payout_3y_avg, latest_signal) without commitment cap",
              "repurchase_of_stock used as buyback_amount input; cancellation progress is not verified",
          ],
      )
      prompt = build_turtle_decision_prompt(facts, signals)

      assert "分红按 holding_channel 扣税" in prompt
      assert "注销型回购" in prompt
      assert "buyback_amount_3y_avg" in prompt
      assert "repurchase_of_stock" in prompt
      assert "注销进度" in prompt
      assert "commitment_ratio" in prompt
      assert "payout_M" in prompt
      assert "可能偏高" in prompt
  ```

- [ ] **Step 7.2: Write old/new rehydration tests**

  Add tests to `tests/unit/test_turtle_value_analyst_integration.py` near the existing `_plain_turtle_report_prompt` tests:

  ```python
  def test_plain_turtle_report_prompt_rehydrates_new_payout_m_payload():
      from tradingagents.agents.analysts import value_analyst as va

      payload = _va_payload()
      data = json.loads(payload)
      data["signals"]["results"] = {
          "payout_M": {
              "name": "payout_M",
              "formula": "payout_M = max(payout_3y_avg, latest_signal)",
              "substitution": "payout_M = max(payout_3y_avg=0.4, latest_signal=0.5) = 0.5",
              "value": 0.5,
              "unit": "ratio",
              "sources": ["report.payout"],
              "missing_inputs": [],
              "status": "complete",
          }
      }

      with patch.object(va, "build_turtle_decision_prompt", return_value="prompt") as builder:
          assert va._plain_turtle_report_prompt("X", "600519", json.dumps(data, ensure_ascii=False)) == "prompt"

      signals = builder.call_args.args[1]
      assert "payout_M" in signals.results
      assert signals.results["payout_M"].substitution.startswith("payout_M = max")


  def test_plain_turtle_report_prompt_rehydrates_legacy_payout_anchor_payload():
      from tradingagents.agents.analysts import value_analyst as va

      payload = _va_payload()
      data = json.loads(payload)
      data["signals"]["results"] = {
          "payout_anchor": {
              "name": "payout_anchor",
              "formula": "payout_anchor = avg_payout_ratio_3y",
              "substitution": "payout_anchor = 0.5",
              "value": 0.5,
              "unit": "ratio",
              "sources": ["legacy.market.payout"],
              "missing_inputs": [],
              "status": "complete",
          }
      }

      with patch.object(va, "build_turtle_decision_prompt", return_value="prompt") as builder:
          assert va._plain_turtle_report_prompt("X", "600519", json.dumps(data, ensure_ascii=False)) == "prompt"

      signals = builder.call_args.args[1]
      assert "payout_anchor" in signals.results
      assert signals.results["payout_anchor"].value == 0.5
  ```

- [ ] **Step 7.3: Run prompt/rehydration tests to verify failure**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_decision.py tests/unit/test_turtle_value_analyst_integration.py \
    -k "spec2_model_caveats or payout_m_payload or payout_anchor_payload" -v
  ```

  Expected: prompt text test fails until `decision.py` is updated. Rehydration tests should pass because the code already iterates raw result keys generically; keep them as regression coverage.

- [ ] **Step 7.4: Add Spec 2 caveat instructions to decision prompt**

  In `build_turtle_decision_prompt(...)`, add this paragraph before `"## 输出结构\n"`:

  ```python
              (
                  "## Turtle v0.15 Spec 2 口径说明\n"
                  "- 分红按 holding_channel 对应 tax_rate 扣税；注销型回购对继续持有股东无即时税务事件，"
                  "R/GG 中 buyback_amount_3y_avg 不扣税。\n"
                  "- repurchase_of_stock 被用作报告侧 buyback_amount 输入，但当前 payload 未验证股份注销进度；"
                  "若回购未注销，O 可能高估股东回报。\n"
                  "- commitment_ratio 本版本未抽取；payout_M 使用 max(payout_3y_avg, latest_signal)，"
                  "未应用承诺上限。\n"
                  "- latest_signal 使用回看的最新年 dividends_paid/net_profit 代理前瞻 DPS 调整值，"
                  "且它同时是 payout_3y_avg 的成员；在支付率上行、亏损年被排除或承诺上限缺失时，"
                  "payout_M 与 R/GG 可能偏高。"
              ),
  ```

- [ ] **Step 7.5: Run prompt/rehydration tests**

  Run:

  ```bash
  python -m pytest tests/unit/test_turtle_decision.py tests/unit/test_turtle_value_analyst_integration.py -v
  ```

  Expected: all prompt and value analyst integration tests pass.

- [ ] **Step 7.6: Commit**

  ```bash
  git add tradingagents/dataflows/value_investment/turtle/decision.py \
    tests/unit/test_turtle_decision.py \
    tests/unit/test_turtle_value_analyst_integration.py
  git commit -m "docs(turtle): document spec2 model caveats in decision prompt"
  ```

---

## Task 8: Full Verification And Frontend Build

**Files:**
- Modify: tests only if focused suites reveal stale names or old expectations.
- Verify: `frontend/`

- [ ] **Step 8.1: Run focused Python suites**

  Run:

  ```bash
  python -m pytest \
    tests/unit/test_turtle_report_adapter.py \
    tests/unit/test_turtle_market_adapter.py \
    tests/unit/test_turtle_calculations.py \
    tests/unit/test_turtle_payload_fx.py \
    tests/unit/test_turtle_decision.py \
    tests/unit/test_turtle_value_analyst_integration.py \
    -v
  ```

  Expected: all selected tests pass.

- [ ] **Step 8.2: Run frontend type-check**

  Run:

  ```bash
  cd frontend && npm run type-check
  ```

  Expected: command exits 0. This validates generic `results` rendering still accepts `payout_M` and string `substitution`.

- [ ] **Step 8.3: Run frontend production build**

  Run:

  ```bash
  cd frontend && npm run build
  ```

  Expected: command exits 0.

- [ ] **Step 8.4: Run final grep checks**

  Run:

  ```bash
  rg -n "payout_anchor" tradingagents tests frontend/src
  ```

  Expected: only the legacy rehydration regression test may mention `payout_anchor`. No production code should produce `payout_anchor`.

  Run:

  ```bash
  rg -n "buyback_amount missing; treated as 0" tradingagents tests
  ```

  Expected: no matches. The canonical missing key/caveat is `buyback_amount_3y_avg missing; treated as 0 for degraded calculation`.

- [ ] **Step 8.5: Commit verification cleanups**

  If Step 8 required test-name or stale-expectation fixes, commit them:

  ```bash
  git add tradingagents tests frontend
  git commit -m "test(turtle): align recalibration verification coverage"
  ```

  If Step 8 required no file changes, skip this commit.

---

## Task 9: Roadmap Status And PR Notes

**Files:**
- Modify: `docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md`

- [ ] **Step 9.1: Update roadmap implementation status**

  When implementation starts, update Spec 2 from `🔵` to `🟠` and keep the plan path:

  ```markdown
  | Spec 2：model-recalibration | 🟠 | `docs/superpowers/specs/2026-05-26-turtle-model-recalibration-design.md` | `docs/superpowers/plans/2026-05-26-turtle-model-recalibration.md` | — | implementation in progress；承诺未应用为 context-only；市场 action 仅上下文 |
  ```

- [ ] **Step 9.2: Commit roadmap implementation-start marker**

  ```bash
  git add docs/tech_reviews/2026-05-21-pr7-turtle-v015-followup-roadmap.md
  git commit -m "docs(roadmap): mark turtle spec2 implementation in progress"
  ```

- [ ] **Step 9.3: Prepare PR summary**

  Use this PR summary:

  ```markdown
  ## Summary
  - recalibrates Turtle R/GG to use report-side `payout_M` and report-side `buyback_amount_3y_avg`
  - derives reliable per-period `dividend_payout_ratio_current_year` while keeping the old proxy display-only
  - treats market action caveats and commitment-not-applied as context-only, with core signal status based on material formula inputs
  - updates FX collection, prompt caveats, and legacy `payout_anchor` compatibility tests

  ## Tests
  - `python -m pytest tests/unit/test_turtle_report_adapter.py tests/unit/test_turtle_market_adapter.py tests/unit/test_turtle_calculations.py tests/unit/test_turtle_payload_fx.py tests/unit/test_turtle_decision.py tests/unit/test_turtle_value_analyst_integration.py -v`
  - `cd frontend && npm run type-check`
  - `cd frontend && npm run build`
  ```

---

## Self-Review Checklist For Implementer

- [ ] Spec §3.1: commitment-not-applied caveat exists and is context-only; data-complete `payout_M` can be `complete`.
- [ ] Spec §3.2/§4.2: latest signal and 3-year average both use `dividend_payout_ratio_current_year`; average is mean of per-period ratios with ≥2 period threshold.
- [ ] Spec §4.3: alias direction is `"repurchase_of_stock": "buyback_amount"`.
- [ ] Spec §5.1: `payout_M.substitution` is a string and contains `commitment_ratio=null`.
- [ ] Spec §5.2/§5.3: R/GG use `buyback_amount_3y_avg` from report-side helper only.
- [ ] Spec §5.4: HH does not inherit buyback missing status and uses the same return target currency for R/GG.
- [ ] Spec §6.1/§6.2: `signals.status` ignores `facts.status` and only material caveats degrade it.
- [ ] Spec §9: market-only buyback does not trigger formula FX and gets a context-only exclusion caveat.
- [ ] Spec §10.4: old `payout_anchor` payload rehydrates, but new production results do not produce it.
