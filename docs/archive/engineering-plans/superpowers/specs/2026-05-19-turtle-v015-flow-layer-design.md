# Turtle v0.15 Flow Layer Design

> Date: 2026-05-19
> Status: Draft for review
> Scope: TradingAgents-CN value analyst flow after the `value` entry layer

## Goal

Add a Turtle v0.15-compatible value-investment flow behind the existing opt-in `value` analyst entry.

The new flow should not rebuild PDF search, PDF parsing, annual-report field extraction, provider trust policy, or LLM supplement merging. Those responsibilities already belong to existing upstream or adapter modules.

TradingAgents-CN should add the Turtle-specific workflow layer:

- adapt existing financial-report and market data into Turtle facts;
- run deterministic Turtle calculation skills;
- generate a final Turtle decision report from facts and computed signals.

The existing `value` analyst id and `value_report` state key remain the integration surface for graph, API, frontend, CLI, and downstream research/trader/risk prompts.

## Context

The previous entry-layer spec made `value` selectable and runnable across the product surface:

- `docs/superpowers/specs/2026-05-19-value-analyst-entry-flow-design.md`
- `docs/superpowers/specs/2026-05-19-value-analyst-entry-turtle-v015-analysis.md`

That phase intentionally kept the report as an experimental value-investment metrics report. It did not implement the Turtle v0.15 coordinator, data-pack boundary, factor gates, EV cash protection, R/GG/HH calculations, or final decision discipline.

The existing annual-report integration is already available in:

- `tradingagents/dataflows/financial_reports/adapter.py`
- `tradingagents/dataflows/financial_reports/policy.py`
- `tradingagents/dataflows/financial_reports/mapper.py`
- `tradingagents/dataflows/financial_reports/integration.py`

That integration consumes `financial-report-llm-extractor` through its public `FinancialReportClient` API.

## Responsibility Boundaries

### report-collector

`report-collector` is only a PDF discovery and download provider.

It may:

- search for the latest annual report PDF;
- download or locate the local PDF;
- return a local file path to `FinancialReportAdapter.resolve_pdf()`.

It must not:

- calculate Turtle fields;
- provide financial-field fallback for Turtle decisions;
- generate value-investment analysis;
- replace `FinancialReportClient` as the authoritative annual-report field source.

Recommended config:

```text
REPORT_COLLECTOR_ENABLED=true
REPORT_COLLECTOR_ANALYSIS_ENABLED=false
```

### financial-report-llm-extractor

`financial-report-llm-extractor` remains the annual-report authority.

TradingAgents-CN should consume it only through `FinancialReportClient`, via the existing financial report adapter package. TradingAgents-CN must not read extractor SQLite, JSON artifacts, CLI output, temporary run directories, or internal cache structures.

It is responsible for:

- parsing annual-report PDFs;
- extracting catalog fields;
- handling LLM supplement;
- marking field reliability and staleness;
- exposing source metadata through public client result objects.

Recommended config for on-demand PDF-backed extraction:

```text
FINANCIAL_REPORT_CLIENT_ENABLED=true
FINANCIAL_REPORT_CACHE_ONLY=false
FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT=true
FINANCIAL_REPORT_LLM_CONFIG_PATH=<extractor-llm-config-path>
```

`FINANCIAL_REPORT_FORCE_REFRESH=true` may be used for explicit refresh runs.

### Turtle Flow In TradingAgents-CN

TradingAgents-CN owns the Turtle-specific investment logic.

It should:

- normalize annual-report fields, market data, dividend data, buyback data, Rf, holding channel, currency, and units into Turtle facts;
- run deterministic Turtle calculation modules;
- pass only prepared facts and computed signals to the final decision prompt;
- write the final result to `value_report`.

It should not:

- parse PDFs directly;
- invent missing annual-report values in the decision prompt;
- let the final LLM call external data tools;
- hide missing-data caveats.

## Architecture

Add a new focused package under the existing value-investment dataflow boundary:

```text
tradingagents/dataflows/value_investment/turtle/
  __init__.py
  facts.py
  report_adapter.py
  market_adapter.py
  calculations.py
  decision.py
  formatting.py
```

The package is internal to TradingAgents-CN. The external surface remains `get_value_investment_analysis()` or a successor tool called by `value_analyst`.

### `facts.py`

Defines structured Turtle inputs and outputs.

Suggested dataclasses:

- `TurtleRunContext`: ticker, market, trade date, period end, holding channel, company name.
- `MoneyAmount`: numeric value, currency, unit, source label, source reference, reliability status.
- `TurtleReportFacts`: extractor metadata, reliable annual-report fields, display-only fields, caveats.
- `TurtleMarketFacts`: price, market cap, shares, industry, dividend data, buyback data, Rf, holding-channel tax rate.
- `TurtleFacts`: combined report and market facts.
- `TurtleComputedSignals`: deterministic factor results, formulas, source references, missing inputs.

These objects should carry values, currency, unit, reliability, and source labels together. A numeric value without provenance is not acceptable for Turtle decisions.

### Money And Unit Rules

Turtle calculations must use explicit money units. The final report should display major money values in RMB 100 million unless the result is explicitly marked as untranslated due to missing FX data.

`MoneyAmount` should support at least these units:

- `yuan`;
- `thousand`;
- `ten_thousand`;
- `million`;
- `hundred_million`.

Conversion rules:

- million -> hundred million: divide by 100;
- thousand -> hundred million: divide by 100,000;
- ten thousand -> hundred million: divide by 10,000;
- non-RMB currency -> RMB: multiply by the analysis-date FX rate and record the FX source.

The implementation must not combine two `MoneyAmount` values unless currency and unit have been normalized or a recorded FX conversion has been applied. Missing FX data makes affected calculations degraded or non-decisionable, depending on whether the result feeds a gate.

Unit conversion requires dedicated tests because v0.15 has high-risk 10x and 1000x failure modes when report values in million, thousand, and ten-thousand units are mixed.

### `report_adapter.py`

Consumes `FinancialReportAdapter` / `FinancialReportClient` results and converts them into `TurtleReportFacts`.

Rules:

- reliable fields may feed computation;
- trusted LLM supplement may feed computation only through existing `FinancialReportPolicy`;
- stale or missing extractions become caveats and display-only metadata;
- extractor internal paths and raw cache details are never exposed to Turtle modules.

This layer can reuse `merge_financial_report_data()` initially, but should avoid coupling the Turtle model to the legacy `financial_data` dict where a structured field is clearer.

### Required Turtle Fields

The first implementation must make field availability explicit before calculations run. Do not silently skip a Turtle rule because a field is not exposed by the current `FinancialReportClient` catalog.

| Turtle need | Primary source | First-slice status | Behavior if missing |
| --- | --- | --- | --- |
| net profit | `FinancialReportClient` mapped field / market fallback | available now | R/GG non-decisionable if no reliable profit proxy |
| operating cash flow | `FinancialReportClient` mapped field / market fallback | available now | GG degraded or non-decisionable depending on formula path |
| capex | `FinancialReportClient` mapped field / market fallback | available now | FCF and owner-earnings calculations degraded |
| cash and equivalents | `FinancialReportClient` mapped field | available now | cash protection degraded |
| short/long debt and bonds | `FinancialReportClient` mapped fields | available now | net cash and EV switch degraded |
| total assets/liabilities/current assets/current liabilities | `FinancialReportClient` mapped fields | available now | balance-sheet quality metrics degraded |
| market cap, close price, shares | market adapter | available now | R/GG/EV non-decisionable if market cap missing |
| dividend history / latest dividend signal | market adapter | available now | payout anchor falls back only if documented; otherwise degraded |
| buyback history / cancellation amount | market adapter | available now | buyback component becomes missing, not zero, unless verified no buyback |
| Rf | market adapter | available now | hurdle checks non-decisionable if missing |
| holding-channel tax `Q` | holding-channel defaults / market adapter | available now | use default with caveat until UI/API exposes channel |
| audit opinion | extractor catalog if exposed; otherwise not available | required but may be missing | Factor 1A item is `unknown`; if too many veto checks unknown, run is non-decisionable |
| related-party transactions | extractor catalog if exposed; otherwise not available | required but may be missing | governance/1A caveat; no negative conclusion without data |
| restricted cash | extractor catalog if exposed; otherwise not available | required but may be missing | cash protection degraded |
| parent-company standalone balance sheet | extractor catalog if exposed; otherwise not available | required for holding-company SOTP | holding-company module deferred/non-decisionable |
| contract liabilities / deferred revenue | extractor catalog or mapped balance-sheet field | required but may be missing | super-broad cash and EV cash addback disabled |
| dividend policy and paid dividend total | extractor catalog if exposed; market dividend fallback | required but may be missing | payout anchor degraded |
| MD&A text, management, industry, competition | extractor public fields or market adapter summaries | partial / later | Factor 1B limited to available facts; richer modules deferred |

If an extractor field is needed but not exposed through public API, the fix is to extend the extractor contract in that project or mark the Turtle module as deferred. TradingAgents-CN must not read extractor internals to close the gap.

### `market_adapter.py`

Collects non-PDF data that Turtle still needs:

- current or trade-date market cap;
- close price;
- shares outstanding when available;
- dividend history and latest dividend signal;
- buyback history and cancellation amount;
- Rf by market;
- holding-channel tax rate;
- industry and market classification.

Existing A/HK fetchers may be reused. The adapter should return missing inputs explicitly instead of silently defaulting to zero when the value affects a gate or return calculation.

### `calculations.py`

Implements Turtle deterministic skills.

First implementation scope:

- Factor 1A base veto checks where data exists:
  - audit opinion abnormality;
  - major fraud/regulatory caveat;
  - major related-party or governance caveat;
  - severe missing-data caveat that makes the run non-decisionable.
- payout anchor `M`;
- rough return `R`;
- precise return `GG`;
- hurdle spread `HH = R - GG`;
- holding-channel tax adjustment `Q`;
- EV switch when broad net cash / market cap exceeds the Turtle threshold;
- cash protection level;
- cash distribution willingness test;
- safety-margin discount adjustment.

Each calculation result must include:

- formula name;
- numeric substitution;
- result;
- input sources;
- missing inputs;
- pass/fail status where applicable.

The existing simplified `PenetratingYieldCalculator`, `CashHealthCalculator`, and `HealthScoreCalculator` may be reused only when their formula matches the Turtle v0.15 rule. Otherwise, implement a separate Turtle calculation and leave the legacy metric as compatibility context.

### `decision.py`

Builds the final decision prompt and parses the report output.

The final decision prompt should receive:

- `TurtleRunContext`;
- `TurtleFacts`;
- `TurtleComputedSignals`;
- extractor and market-data caveats;
- the Turtle v0.15 report structure.

The prompt must state:

- do not call external tools;
- do not invent missing data;
- cite values from supplied facts/signals;
- separate veto, non-decisionable, watchlist, and investable outcomes;
- include formula substitutions for R/GG/HH/EV-related conclusions.

The final decision LLM call must be a plain report-generation chain with no bound tools. Data fetching and deterministic calculations happen before this step. If the current `value_analyst` remains tool-backed, the tool may return only `TurtleFacts` and `TurtleComputedSignals`; the final Turtle report generation must happen after tool execution without binding `get_value_investment_analysis` or any external-data tool.

### `formatting.py`

Formats structured facts and computed signals into compact markdown for:

- the final decision prompt;
- `value_report`;
- logs or debug output when needed.

Formatting should preserve source references and caveats without exposing implementation internals.

## Data Flow

```text
value analyst selected
  -> Turtle run context
  -> report-collector resolves/downloads latest annual report PDF when needed
  -> FinancialReportClient extracts annual-report fields
  -> Turtle report adapter builds TurtleReportFacts
  -> Turtle market adapter builds TurtleMarketFacts
  -> Turtle calculations build TurtleComputedSignals
  -> final decision LLM generates value_report
  -> downstream research/trader/risk consume value_report as they do today
```

The final decision step is intentionally after all data fetching and deterministic calculations. It should be impossible for the decision prompt to fetch new external data.

## Annual Report Period Rule

Turtle v0.15 uses a March cutoff:

- if current month is January through March, target annual report year is `current_year - 2`;
- otherwise target annual report year is `current_year - 1`.

The current `FinancialReportAdapter.infer_annual_period_end()` uses a May cutoff. To preserve existing behavior for other consumers, the Turtle flow should compute its own target `period_end` and pass it explicitly into the financial report adapter.

## Holding Channel

The first version should support a simple holding-channel model:

- A-share: default `long_term_domestic`;
- HK: default `stock_connect`;
- US: unsupported by the current value tool unless a later spec adds US rules.

The selected holding channel determines tax parameter `Q`. If the UI/API does not yet expose holding channel, the defaults above are used and shown in the report caveats.

## Error Handling And Degraded Runs

The flow should distinguish these outcomes:

- `complete`: enough reliable data for calculation and decision;
- `degraded`: some fields missing, but core R/GG/HH and cash protection can still be calculated;
- `non_decisionable`: key inputs are missing or stale, so the report must not produce an investable conclusion;
- `unsupported`: market or ticker format not supported.

Missing annual-report extraction is not fatal by itself, but it should usually make the Turtle run `non_decisionable` unless market-only data is enough for a clearly labeled rough screen.

Stale extraction is display-only by default. It should not feed computation unless a later explicit policy allows stale data.

`non_decisionable` is a hard boundary. A non-decisionable Turtle run must output a Turtle non-decisionable report with missing inputs and caveats, and must not fall back to a legacy report that contains investable, hold, or avoid recommendations.

## Backward Compatibility

Keep the existing `value` analyst entry and `value_report`.

The current `get_value_investment_analysis()` path may remain as a fallback while the Turtle flow is introduced. During the transition, the report should clearly identify whether it is:

- Turtle v0.15 flow report;
- degraded Turtle screen;
- legacy value metrics report.

Legacy value metrics fallback is allowed only when Turtle flow is disabled by configuration, the market is unsupported before Turtle starts, or extractor/report infrastructure is unavailable before a Turtle status is produced. Once the Turtle flow returns `degraded` or `non_decisionable`, that Turtle status owns the final `value_report`.

Existing default analyst selections remain unchanged. `value` stays opt-in.

## Non-Goals

- Do not introduce a separate `turtle` analyst id in this phase.
- Do not build a new PDF parser inside TradingAgents-CN.
- Do not read `financial-report-llm-extractor` internals.
- Do not make `value` a default analyst.
- Do not add PDF upload UI in this phase.
- Do not implement full Phase2原文段落 extraction in TradingAgents-CN.
- Do not remove the legacy value metrics code until the Turtle path is verified.

## Testing Strategy

Add focused unit tests before implementation:

1. Turtle annual-report period inference uses the March cutoff.
2. `report_adapter` consumes a fake `FinancialReportClient` result and preserves reliable/display-only/caveat boundaries.
3. `market_adapter` returns explicit missing inputs instead of silent zeroes for decision-critical fields.
4. `calculations` verifies R, GG, HH, EV switch, cash protection level, and payout anchor with deterministic fixtures.
5. Unsupported US market returns an `unsupported` result without calling annual-report extraction.
6. Missing or stale annual-report extraction produces `non_decisionable` or `degraded` status with caveats.
7. Final decision prompt contains no tool binding requirement and includes supplied formula substitutions.
8. `non_decisionable` does not execute legacy fallback or emit investable/hold/avoid recommendations.
9. Money unit conversion covers million, thousand, ten-thousand, hundred-million, and FX-normalized values.
10. Existing `value` entry tests still pass.

### Test Data Strategy

Use a layered test-data strategy so ordinary tests stay deterministic while local smoke tests can exercise real PDFs.

Unit tests must not depend on absolute paths under another checkout such as `~/git/financial-report-llm-extractor`. They should use small fake `ExtractionResult` objects or compact fixture JSON committed under TradingAgents-CN, for example:

```text
tests/fixtures/turtle/
  cn_600519_report_facts.json
  hk_00001_report_facts.json
  turtle_market_facts.json
```

These fixtures should represent public `FinancialReportClient` result shape or the TradingAgents-CN `TurtleFacts` shape. They must not copy extractor SQLite, JSON cache internals, `tmp/runs` internals, or CLI output contracts.

The local extractor checkout can be used as a developer data source for smoke tests and manual validation:

```text
FINANCIAL_REPORT_PDF_ROOT=/home/like/git/financial-report-llm-extractor/downloads
FINANCIAL_REPORT_CLIENT_ENABLED=true
FINANCIAL_REPORT_CACHE_ONLY=false
```

Known local PDF candidates:

- A-share: `/home/like/git/financial-report-llm-extractor/downloads/cn_stocks/600519/annual/2024_年度报告.pdf`
- A-share: `/home/like/git/financial-report-llm-extractor/downloads/cn_stocks/300750/annual/2024_年度报告.pdf`
- HK: `/home/like/git/financial-report-llm-extractor/downloads/hk_stocks/00001/annual/2024_annual_en.pdf`
- HK: `/home/like/git/financial-report-llm-extractor/downloads/hk_stocks/01810/annual/2024_annual_en.pdf`

Historical files under `financial-report-llm-extractor/tmp/runs/` may be used as development references, but TradingAgents-CN tests must not rely on them. They are extractor implementation artifacts and may change independently of the public client contract.

For smoke testing, use one A-share and one HK ticker with cached or downloadable annual reports. The smoke should verify:

- report-collector can provide a PDF path when configured;
- `FinancialReportClient` can return extraction metadata;
- Turtle calculations run without final LLM data fetching;
- `value_report` is populated and downstream state shape is unchanged.

## Recommended First Implementation Slice

Implement the minimal Turtle v0.15 flow layer:

1. Add Turtle dataclasses and annual-period inference.
2. Add report and market adapters that reuse existing `financial_reports` and value-investment fetchers.
3. Add deterministic calculation functions for payout anchor, R/GG/HH, EV switch, cash protection, and basic veto status.
4. Add final decision prompt builder and wire it behind the existing `value` analyst path.
5. Keep the legacy report only as a pre-Turtle fallback when Turtle flow is disabled or unavailable before a Turtle status exists.

This gives a real Turtle decision path without duplicating the already completed financial-report extraction component.

## Open Follow-Ups

- Add UI/API holding-channel selection.
- Add richer Phase2 annual-report narrative facts once extractor exposes the required text sections through public API.
- Expand Factor 1B qualitative modules after structured facts and calculation gates are stable.
- Decide when the frontend label should change from `价值投资分析师` to `龟龟投资分析师`.
