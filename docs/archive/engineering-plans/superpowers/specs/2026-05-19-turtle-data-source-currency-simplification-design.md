# Turtle Data Source and Currency Simplification Design

## Purpose

Turtle v0.15 should use trustworthy facts with explicit provenance. The current HK review fixes routed HK data away from A-share helpers, but they also introduced too much local interpretation in TradingAgents-CN: yfinance HK payout and buyback fields are being adapted into Turtle-critical facts even though their semantics do not match Turtle's required annual-report facts.

This design narrows the Turtle flow:

- `financial-report-llm-extractor` is the authority for annual-report facts and their money units.
- TradingAgents-CN owns only Turtle fact adaptation, deterministic formula calculation, and decision-status discipline.
- Market providers provide market facts such as `market_cap`, `close_price`, and `industry`.
- Weak HK payout and buyback sources must not be promoted to reliable Turtle inputs.

## Current Problems

### Overloaded HK market provider

`tradingagents/dataflows/providers/hk/hk_stock.py` currently exposes HK dividend and buyback helpers based on yfinance fields:

- `payoutRatio` is returned as `avg_payout_ratio_3y`;
- cashflow repurchase rows are treated as cancelled buyback;
- multiple cashflow periods may be summed and added once into R/GG.

Those are not equivalent to Turtle's required payout anchor or confirmed annual buyback/cancellation amount. This can make HK runs appear complete when the critical fact is only a weak proxy.

### Duplicate money normalization

`financial-report-llm-extractor` already models report money facts with:

- value;
- currency;
- unit;
- unit multiplier;
- normalized value;
- evidence and confidence.

TradingAgents-CN should not re-infer report currencies from raw strings or build broad FX behavior around every money field. It should convert the public client's `FieldValue` contract into Turtle `MoneyAmount` and then calculate only when formula inputs are mutually compatible.

### Fragile currency target selection

The current calculation layer picks a target currency globally. It can become fragile when:

- any unrelated `fx_rates` entry exists;
- reliable money fields that are not used by Turtle formulas have another currency;
- all formula inputs are same-currency HKD but unrelated metadata forces CNY conversion.

Turtle ratios only require numerator and denominator money values to be in the same currency and comparable unit. They do not require CNY unless the run explicitly needs cross-currency comparison.

## Approaches Considered

### Approach A: Continue using HK yfinance action data as fallback

This keeps HK runs more often decisionable, but it overstates the reliability of payout and buyback inputs. It also duplicates source semantics already handled upstream by `financial-report-llm-extractor`.

Rejected.

### Approach B: Add FX and action-source logic in TradingAgents-CN

TradingAgents-CN could build its own HK payout history, buyback cancellation policy, and FX source layer. This would duplicate extractor/source-policy responsibilities and increase maintenance surface.

Rejected.

### Approach C: Conservative source boundary

Use extractor-derived report facts for report-derived Turtle inputs. Use market providers only for market facts. If a critical payout or buyback input is unavailable from reliable report facts, mark the calculation missing/degraded/non-decisionable according to Turtle status rules.

Chosen.

## Design

### Data Source Boundaries

`financial-report-llm-extractor` remains the source for annual-report facts:

- `net_profit`;
- `operating_cash_flow`;
- `capex`;
- `cash`;
- `interest_bearing_debt`;
- future report-derived fields such as `dividends_paid` and confirmed buyback/repurchase fields when exposed by the public client contract.

TradingAgents-CN must consume only the extractor public API via `FinancialReportClient`/`FieldValue`. It must not read extractor internal caches, SQLite files, JSON artifacts, or temporary run directories.

Market providers remain the source for market facts:

- `market_cap`;
- `close_price`;
- `industry` when available.

Tax and risk-free-rate facts remain configuration/default facts:

- holding channel;
- dividend withholding tax;
- `TURTLE_RF_RATE_*`.

### HK Payout Anchor

HK payout anchor should be reliable only when derived from report facts.

First implementation:

- If `dividends_paid` and `net_profit` are both reliable, same-currency, and positive where required, calculate `dividend_avg_payout_ratio_3y` as a conservative single-year report-derived payout proxy and attach an explicit caveat that it is a one-year proxy, not a three-year average.
- If `dividends_paid` is missing or unreliable, omit the payout anchor. R/GG become `non_decisionable` because payout is critical.
- Do not use yfinance `payoutRatio` as a Turtle-critical payout anchor.

Later implementation may extend extractor/client support to fetch three fiscal years and compute the true three-year average.

### HK Buyback

HK buyback should not be treated as reliable unless the source proves the correct annual amount and semantics.

First implementation:

- Remove yfinance HK cashflow repurchase adaptation from Turtle market facts.
- If extractor exposes a reliable annual buyback/repurchase amount in the future, adapt it as `buyback_amount`.
- If buyback is missing, `R` and `GG` may still compute as `degraded` with buyback treated as zero, using the existing degraded-buyback rule.
- Do not mark yfinance cashflow repurchase rows as `is_cancelled=True`.

This is conservative: missing buyback should reduce confidence, not block every run.

### Currency and Unit Handling

`MoneyAmount` should represent a numeric amount plus explicit currency and scale. For report facts, TradingAgents-CN should map the extractor public fields directly:

- `FieldValue.value` -> numeric amount;
- `FieldValue.currency` -> Turtle currency;
- `FieldValue.unit` -> Turtle unit if it maps cleanly;
- if an extractor field carries normalized raw units in a future public contract, prefer that over local unit parsing.

Formula calculation should choose currency based on fields that are actually used by Turtle formulas:

- `net_profit`;
- `operating_cash_flow`;
- `capex`;
- `cash`;
- `interest_bearing_debt`;
- `market_cap`;
- `buyback_amount` when present.

Rules:

1. If all required present formula money inputs share one reliable currency, calculate in that currency.
2. If currencies differ and the required direct FX rates are present, convert to the selected target currency and record FX in source references.
3. If currencies differ and required FX is missing, mark affected formulas `non_decisionable`.
4. Do not let unrelated money fields or unrelated FX rates change the target currency.

For ratio formulas, the output remains currency-independent:

- `R`: percent;
- `GG`: percent;
- `HH`: percentage points;
- `net_cash_ratio`: percent.

Only intermediate money formula outputs such as `owner_earnings` carry `hundred_million <currency>`.

### Report Adapter Unit Mapping

The report adapter should accept common extractor/public-client unit strings without treating HKD base units as unsupported:

- base unit: `yuan`, `rmb`, `cny`, `hkd`, `hk$`, `港元`, `港币`, `usd`, `us$`;
- thousand: `thousand`, `rmb'000`, `hkd'000`, `usd'000`, `千元`;
- million: `million`, `百万元`, `百万`, `hk$ million`, `rmb million`, `us$ million`;
- ten-thousand and hundred-million remain supported where relevant.

Unit parsing should be small and deterministic. It should not attempt full natural-language interpretation; extractor owns that.

### Status Semantics

The first implementation should preserve existing Turtle status behavior:

- missing `market_cap`: `non_decisionable`;
- missing payout anchor: `non_decisionable` for R/GG;
- missing buyback: `degraded`, with buyback treated as zero;
- mixed currencies without required FX: `non_decisionable`;
- all same-currency monetary inputs: complete/degraded according to data completeness, without FX requirement.

### Tests

Unit tests should cover:

- all-HKD formula inputs calculate without FX;
- all-CNY formula inputs calculate without FX;
- mixed HKD/CNY formula inputs require FX;
- unrelated reliable money fields do not break all-HKD formula calculations;
- unrelated FX rates do not force all-HKD formula calculations into CNY;
- HK market facts still use HK market provider for `market_cap`;
- HK Turtle flow does not call yfinance payout/buyback helpers as reliable action facts;
- report adapter accepts HKD base-unit aliases;
- report-derived one-year payout proxy is explicitly caveated.

Tests must use fake public-client result objects or small Turtle fact fixtures. They must not depend on files under `~/git/financial-report-llm-extractor`.

## Migration Impact

This change may make some HK Turtle runs less decisionable than the temporary yfinance action fallback. That is intentional. It is better to return a degraded or non-decisionable Turtle report than to compute a complete-looking report from weak action semantics.

The final report should expose missing or proxy inputs in caveats so users can see why a Turtle run did not produce a full decision.

## Non-Goals

- Do not implement a full FX-rate provider in TradingAgents-CN.
- Do not read extractor internals or cache files.
- Do not compute true three-year payout until the extractor/client flow can fetch the required multi-year facts through a public contract.
- Do not remove legacy value-investment metrics outside the Turtle flow.
- Do not change frontend labels or UX in this repair.

## Acceptance Criteria

1. HK Turtle market facts still include market cap when the HK market provider supplies it.
2. HK yfinance payout and buyback data are not promoted to reliable Turtle-critical facts.
3. Report-derived payout proxy is only produced from reliable report fields and is clearly caveated.
4. Same-currency Turtle formula inputs compute without FX.
5. Mixed-currency formula inputs require explicit relevant FX.
6. Unit tests prove the conservative HK behavior and currency target behavior.
