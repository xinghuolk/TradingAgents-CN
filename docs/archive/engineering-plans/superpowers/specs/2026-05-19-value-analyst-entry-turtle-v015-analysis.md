# Value Analyst Entry and Turtle v0.15 Gap Analysis

## Background

The project already contains a partial `value` analyst path and a value-investment tool. It can calculate several value-style metrics, including penetrating yield, cash health, dividend and buyback related fields, and a local 5D health score.

The external Turtle framework at `/home/like/git/Stock_Analyze_Prompts/turtle_framework/龟龟投资策略_v0.15` defines a stricter multi-phase investment analysis process. The current project implementation does not yet match that framework end to end.

This document records the current gap and the recommended implementation order before starting code changes.

## Current Project State

The current implementation has these pieces:

- `tradingagents/tools/value_investment_tool.py` provides `get_value_investment_analysis`.
- `tradingagents/dataflows/value_investment/penetrating_yield.py` computes a simplified penetrating yield.
- `tradingagents/dataflows/value_investment/cash_health.py` computes a simplified cash health model.
- `tradingagents/dataflows/value_investment/health_score.py` computes a local 5D health score.
- `financial-report-llm-extractor` data can supplement some financial report fields and source notes.
- `GraphSetup` has partial `value` analyst wiring, but the graph path is not complete.

Known entry gaps:

- Default analyst selections do not include `value`.
- Frontend analyst constants do not expose `value`.
- CLI analyst enum does not expose `value`.
- API parameter defaults do not include `value`.
- `GraphSetup` references conditional routing by analyst type, but there is no complete `should_continue_value` path.
- The current value path is not suitable to present as a faithful Turtle v0.15 analysis.

## Turtle v0.15 Reference Flow

Turtle v0.15 is a staged workflow:

1. Coordinator parses stock, market, holding channel, and optional annual report PDF.
2. Phase 1 generates `data_pack_market.md`.
3. Phase 2 parses annual report PDF into `data_pack_report.md` when available.
4. Phase 3 reads only the data packs and produces the final analysis.

Important Turtle v0.15 requirements:

- Phase 3 should not call external data tools.
- The latest annual report target year follows the Jan-Mar / Apr-Dec rule.
- Reported units and currencies must be preserved and normalized explicitly.
- Analysis uses a four-factor structure:
  - Factor 1A: five-minute veto screen.
  - Factor 1B: deeper qualitative and financial analysis.
  - Factor 2: rough top-down penetrating yield.
  - Factor 3: precise bottom-up penetrating yield and cash quality audit.
  - Factor 4: valuation, margin of safety, and position judgement.
- It distinguishes rough `R`, precise `GG`, and deviation `HH`.
- It includes narrow cash, broad cash, super-broad cash, contract liabilities, cash protection, and EV switching rules.
- It considers holding channel tax and distribution willingness.
- It requires formula substitution, source citation, and checkpoint-style reasoning.

## Gap Summary

| Area | Turtle v0.15 | Current project |
| --- | --- | --- |
| Workflow | Coordinator plus Phase 1/2/3 data packs | Single tool pulls data and writes a report |
| Data discipline | Analysis reads prepared data packs only | Analysis can call data sources directly |
| Veto gates | Explicit audit, fraud, business, payout, and risk-free-rate gates | Mostly absent |
| Penetrating yield | Rough `R`, precise `GG`, deviation `HH` | Single simplified penetrating yield |
| Cash model | Narrow/broad/super-broad cash, EV switch, cash protection | Cash minus interest-bearing debt |
| Tax/channel | Holding-channel tax is part of the analysis | Not fully modeled |
| Market coverage | Data-pack based, designed for broader market coverage | Core AKShare path is mostly A-share |
| Output standard | Formula substitutions, citations, checkpoints | Tool-style metric report |
| Scoring | Four-factor threshold/veto framework | Local 5D health score |

## Recommendation

Implement this in phases.

### Phase 1: Entry Layer Minimal Closure

Goal: make `value` selectable and runnable across graph, API, frontend, CLI, and smoke tests, without claiming full Turtle v0.15 fidelity.

Scope:

- Add `value` to supported analyst definitions.
- Add graph conditional routing for `value`.
- Add frontend analyst entry.
- Add CLI analyst enum entry.
- Add API parameter support.
- Add smoke or focused regression tests for selecting `value`.
- Keep `value` disabled by default unless explicitly selected.
- Label the current output as an experimental value-investment metrics analysis, not a complete Turtle v0.15 report.

Reason:

The project needs a stable execution entry before deeper Turtle workflow work can be integrated and tested. This also avoids overstating the current implementation.

### Phase 2: Turtle v0.15 Flow Layer

Goal: implement the faithful Turtle v0.15 process behind the entry.

Scope:

- Introduce market data pack generation.
- Introduce annual report data pack generation.
- Make final analysis consume data packs only.
- Add Factor 1A and 1B veto logic.
- Add Factor 2 rough `R`.
- Add Factor 3 precise `GG` and `HH` deviation checks.
- Add Factor 4 valuation and margin-of-safety judgement.
- Add EV cash protection rules.
- Add holding channel tax handling.
- Add formula substitution and source citation requirements.

### Phase 3: Productization

Goal: only after output quality is acceptable, present the feature as Turtle-style analysis in the UI and documentation.

Scope:

- Decide final display name.
- Add report sections matching the Turtle v0.15 framework.
- Add structured UI display for factor results and veto status.
- Document required input fields and optional PDF behavior.
- Add broader market and regression coverage.

## Proposed Issue Draft

Title:

`feat: add value analyst entry and prepare Turtle v0.15 analysis flow`

Body:

```markdown
## Goal

Add a stable independent value analyst entry first, then use it as the integration point for the fuller Turtle v0.15 analysis workflow.

## Context

The project currently has a partial `value` analyst path and `get_value_investment_analysis`, but the entry is incomplete across graph/API/frontend/CLI. The current tool calculates simplified value metrics and should not yet be presented as a faithful Turtle v0.15 implementation.

## Phase 1 Scope

- Support selecting `value` in backend analyst lists.
- Add missing graph conditional routing for `value`.
- Expose `value` in API parameters.
- Expose `value` in frontend analyst options.
- Expose `value` in CLI analyst enum.
- Add focused smoke/regression coverage.
- Keep it opt-in, not default.
- Label output as experimental value-investment metrics analysis.

## Phase 2 Scope

- Add Turtle v0.15 market data pack generation.
- Add annual report data pack generation.
- Make final analysis read data packs only.
- Implement Factor 1A/1B/2/3/4.
- Implement R/GG/HH.
- Implement EV cash protection and holding-channel tax logic.
- Require formula substitutions and source citations.

## Non-goals for Phase 1

- Do not claim full Turtle v0.15 fidelity.
- Do not make `value` a default analyst.
- Do not rewrite the full value-investment engine before the entry path is stable.
```

## Open Design Questions

1. Should the analyst key remain `value`, or should a separate `turtle` key be introduced later?
2. Should the UI label be `价值投资` during Phase 1 and only become `龟龟投资` after Phase 2?
3. Should the Turtle v0.15 data packs be persisted as files, database records, or structured in-memory artifacts attached to the analysis run?
4. Should Phase 1 allow PDF input, or should PDF handling wait until Phase 2?

