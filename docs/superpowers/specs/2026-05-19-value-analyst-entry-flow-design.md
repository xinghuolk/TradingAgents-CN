# Value Analyst Entry Flow Design

## Goal

Make the existing `value` analyst a stable opt-in analyst entry across backend graph setup, API models, frontend analyst selection, CLI selection/output, and focused tests.

This phase intentionally does not implement the full Turtle v0.15 workflow. The exposed entry should be described as an experimental value-investment metrics analysis until the dedicated Turtle v0.15 data-pack and four-factor flow is implemented later.

## Current Context

The project already has partial value analyst support:

- `tradingagents/agents/analysts/value_analyst.py` defines `create_value_analyst`.
- `tradingagents/agents/utils/agent_states.py` includes `value_report` and `value_tool_call_count`.
- `tradingagents/graph/setup.py` creates the value analyst node when `"value"` is selected.
- `tradingagents/graph/trading_graph.py` creates `tools_value` with `get_value_investment_analysis`.

The entry is incomplete:

- `ConditionalLogic` has no `should_continue_value`, so graph setup with `"value"` can fail.
- `Propagator.create_initial_state()` does not initialize `value_report`.
- Frontend analyst constants do not include a value analyst.
- CLI analyst enum and interactive analyst order do not include a value analyst.
- Report extraction and UI report mappings omit `value_report`.
- Existing research, trader, and risk prompts only consume market, sentiment, news, and fundamentals reports.

## Approaches Considered

### Recommended: add a stable `value` analyst entry using the existing report surface

Use analyst id `value`, store output in `value_report`, include it in the same report extraction/display paths as other analyst reports, and pass it into downstream researcher/trader/risk context.

Benefits:

- Small, testable change.
- Reuses the already-created value analyst and tool.
- Avoids creating a second analyst identity before the Turtle v0.15 workflow is ready.
- Keeps the feature opt-in and avoids changing default analysis behavior.

Trade-off:

- The analysis remains a simplified value-investment metrics report, not the final Turtle v0.15 workflow.

### Alternative: introduce a separate `turtle` analyst id now

Create a new `turtle` analyst id and leave `value` as-is.

Benefits:

- Clear semantic separation between current value metrics and future Turtle v0.15 analysis.

Trade-off:

- Duplicates entry plumbing before the actual Turtle workflow exists.
- Requires deciding future API/UI naming too early.

### Alternative: implement Turtle v0.15 first and skip the current value entry

Build the data-pack and four-factor Turtle workflow before exposing anything.

Benefits:

- Avoids exposing simplified output.

Trade-off:

- Larger change with more risk.
- Delays fixing the already partially-wired graph entry.
- Makes it harder to test graph/API/frontend selection separately from analysis quality.

## Design

### Analyst Identity

Use `value` as the internal analyst id for this phase.

Recommended labels:

- Backend/CLI English label: `Value Analyst`
- Frontend Chinese label: `价值投资分析师`
- Description should say it uses penetrating-yield and cash-health metrics and is experimental.

Do not use `龟龟投资` as the primary UI label in this phase. That name should be reserved for the later Turtle v0.15 workflow.

### Graph Flow

`value` should behave like the other tool-backed analyst nodes:

1. The selected analyst list may include `"value"`.
2. `GraphSetup` creates `Value Analyst`, `tools_value`, and `Msg Clear Value`.
3. `ConditionalLogic.should_continue_value()` routes to `tools_value` when the value analyst emits tool calls.
4. It stops when `value_report` is populated or `value_tool_call_count` reaches its cap.
5. `Propagator.create_initial_state()` initializes `value_report` and `value_tool_call_count`.

The default selected analyst lists remain unchanged. `value` is opt-in only.

### Downstream Report Context

When `value_report` exists, it should be included in the context seen by:

- Bull researcher
- Bear researcher
- Research manager
- Trader
- Risk debators
- Risk manager

The downstream prompts should include a clearly labeled value report section. Empty value reports should not add noise.

### API and Persistence

`AnalysisParameters.selected_analysts` should continue accepting a list of strings. No enum migration is required for this phase.

Report extraction paths should include `value_report`:

- API result extraction in `app/routers/analysis.py`
- report export/persistence helpers in `app/services/simple_analysis_service.py`

The existing defaults should not include `value`.

### Frontend

Add `value` to `frontend/src/constants/analysts.ts`:

- id: `value`
- name: `价值投资分析师`
- description: `基于穿透收益率、分红回购和现金健康的实验性价值指标分析`
- icon: an existing Element Plus icon name such as `Money`

Update name/id mappings so selected analyst names convert to `value`.

Add `value_report` to the single-analysis report mapping so completed value reports display in the report list.

Batch analysis should pick up the analyst automatically from shared constants.

`DEFAULT_ANALYSTS` remains `['市场分析师', '基本面分析师']`.

### CLI

Add `VALUE = "value"` to `AnalystType`.

Add an interactive choice:

`价值投资分析师 | Value Analyst`

CLI output buffers and streaming status should include:

- `value_report`
- `Value Analysis`
- `Value Analyst`

The CLI should display the value report when present.

### Testing

Add focused tests that do not require real LLM/API calls:

1. `ConditionalLogic.should_continue_value()` routes correctly:
   - returns `tools_value` when the last message has tool calls and no completed report.
   - returns `Msg Clear Value` when `value_report` is populated.
   - returns `Msg Clear Value` after the max tool-call cap.

2. Graph setup supports `selected_analysts=["value"]`:
   - compiling the graph does not raise `AttributeError`.
   - graph nodes include `Value Analyst`, `tools_value`, and `Msg Clear Value`.

3. Initial state includes `value_report` and `value_tool_call_count`.

4. Frontend constant conversion maps `价值投资分析师` to `value`.

5. CLI enum and analyst order include value.

## Non-Goals

- Do not implement Turtle v0.15 Phase 1/2/3 data packs.
- Do not implement R/GG/HH.
- Do not implement EV cash protection rules.
- Do not make `value` a default analyst.
- Do not rename existing `value` code to `turtle`.
- Do not require PDF input in this phase.

## Rollout

This is an opt-in feature. Existing analysis flows should behave the same unless a user explicitly selects the value analyst.

After this phase, the next spec should define the Turtle v0.15 flow layer behind the stable `value` entry.

