# Node Model Usage Recording Design

## Goal

Record the actual provider/model/token/cost/duration used by each analysis node, show node-level aggregate model usage in the report detail page, and move Value Analyst from the quick model to the deep model.

## Current Behavior

The analysis graph creates two LLM instances:

- `quick_thinking_llm`, configured from `quick_provider` and `quick_think_llm`.
- `deep_thinking_llm`, configured from `deep_provider` and `deep_think_llm`.

`Value Analyst` currently uses `quick_thinking_llm` in `tradingagents/graph/setup.py`, while `Research Manager` and `Risk Judge` use `deep_thinking_llm`. The result document stores a single `model_info` value derived from the deep model, which can make a mixed-model analysis look like it used only one model.

The existing `token_usage` collection records provider/model/token/cost data at the adapter layer, but the report detail page cannot reliably attribute those records to analysis nodes.

## Requirements

1. `Value Analyst` must use the deep model.
2. Each graph-managed LLM call made during an analysis must be attributed to the graph node or post-graph step that caused it.
3. The stored analysis result must include a node-level aggregate `model_usage` field.
4. The report detail page must show model usage metadata beside each report module when available.
5. Existing API clients and UI paths must continue working when `model_usage` is absent.
6. The old `model_info` field must remain for backward compatibility.
7. Direct internal OpenAI LLM calls made by dataflow tools while a node context is active should be recorded under that active node when feasible. If a direct call cannot expose tokens or cost, the node must be marked partial rather than omitted.

## Non-Goals

- Do not add single-call detail rows to the report page.
- Do not redesign the Token Statistics page.
- Do not change whether quick analysis runs the downstream research, trading, and risk pipeline.
- Do not remove or rewrite the existing `token_usage` collection.
- Do not attribute non-LLM provider calls, market data calls, or PDF parsing work to `model_usage`.

## Model Assignment

The graph should use these model roles:

| Graph/post-graph node | Canonical node key | Model role |
| --- | --- | --- |
| Market Analyst | market_analyst | quick |
| tools_market | market_analyst | quick |
| Fundamentals Analyst | fundamentals_analyst | quick |
| tools_fundamentals | fundamentals_analyst | quick |
| News Analyst | news_analyst | quick |
| tools_news | news_analyst | quick |
| Social Analyst | social_analyst | quick |
| tools_social | social_analyst | quick |
| Value Analyst | value_analyst | deep |
| tools_value | value_analyst | deep |
| Bull Researcher | bull_researcher | quick |
| Bear Researcher | bear_researcher | quick |
| Research Manager | research_manager | deep |
| Trader | trader | quick |
| Risky Analyst | risky_analyst | quick |
| Safe Analyst | safe_analyst | quick |
| Neutral Analyst | neutral_analyst | quick |
| Risk Judge | risk_judge | deep |
| SignalProcessor | signal_processor | quick |

The Value Analyst change is intentional because Turtle value analysis is a decision-quality report and should use the higher-quality deep model.

The backend must normalize graph display names to the canonical node keys above before storing `model_usage`. Frontend mappings should only depend on canonical keys, never raw LangGraph node names such as `"Market Analyst"` or `"tools_market"`.

## Data Model

Add `model_usage` to analysis results and report documents:

```json
{
  "model_usage": {
    "summary": {
      "total_calls": 12,
      "total_input_tokens": 12345,
      "total_output_tokens": 2345,
      "total_duration_seconds": 45.67,
      "costs_by_currency": {
        "CNY": 0.1234
      }
    },
    "nodes": {
      "value_analyst": {
        "display_name": "价值投资分析",
        "provider": "codex",
        "model": "gpt-5.5",
        "providers": ["codex"],
        "models": ["gpt-5.5"],
        "calls": 2,
        "input_tokens": 3000,
        "output_tokens": 900,
        "cost": 0.08,
        "currency": "CNY",
        "costs_by_currency": {
          "CNY": 0.08
        },
        "duration_seconds": 12.3,
        "partial": false,
        "partial_reason": null
      }
    }
  }
}
```

`provider`, `model`, `cost`, and `currency` are convenient single-provider fields. If a node uses more than one provider/model or more than one currency, set `provider`/`model` to `"mixed"`, set `cost` and `currency` to `null`, and use the `providers`, `models`, and `costs_by_currency` fields for display.

`partial` is `true` when at least one call had provider/model/duration but did not expose token or cost data. Aggregates should sum known token and cost values only. Missing token or cost values must not be presented as exact zeroes in the UI; the UI should show them as unavailable when `partial` is true and no known value exists.

## Backend Recording Design

Create a small runtime module, `tradingagents/graph/model_usage.py`, responsible for:

- Tracking the current `task_id` and `node_name` with `contextvars.ContextVar`.
- Providing `model_usage_context(task_id=None, node_name=None)` to wrap whole-run and per-node execution.
- Providing `record_llm_call(...)` for LLM wrappers to report actual usage.
- Providing `get_model_usage_snapshot(task_id)` to return aggregate data.
- Clearing temporary records in a `finally` block after `TradingAgentsGraph.propagate()` copies the snapshot into final state.

The in-memory aggregate store must be keyed by an explicit analysis run id and protected against concurrent analysis tasks. Service entry points should pass the real task id into `TradingAgentsGraph.propagate(..., task_id=task_id)`; `None` must not be used as a shared aggregate key. If a non-service caller omits `task_id`, `propagate()` should create a unique local run id and still clean it up. `propagate()` should establish the whole-run context once, then graph node wrappers should set only `node_name` and inherit the current run id from the context variable.

Wrap graph node callables when adding them in `GraphSetup.setup_graph()`, and pass the canonical node key into `model_usage_context(...)`. This is more reliable than trying to infer the current node from `graph.stream()` output, because stream chunks are yielded after node execution. Tool nodes such as `tools_value` should use the same canonical key as their owning analyst so tool-triggered internal LLM work appears beside the report module it supports.

Wrap LLM instances returned by `create_llm_by_provider()` with a lightweight proxy or method-level wrapper that records:

- provider
- model
- duration
- input tokens
- output tokens
- cost when exposed by adapter metadata

The wrapper must preserve LangChain behavior and must not break class-sensitive code, `bind_tools()`, or runnable composition. A method-level wrapper that instruments the original runnable object is acceptable if it is safer than a standalone proxy. It should record direct `invoke`/`ainvoke`/`stream`/`astream` calls, wrap objects returned by `bind_tools(...)` so bound-tool invocations are still recorded, and keep delegating unknown attributes to the wrapped object when a proxy is used. Usage extraction should check common LangChain/OpenAI locations such as `AIMessage.usage_metadata`, `response_metadata.token_usage`, `llm_output.token_usage`, raw response usage, and adapter-specific metadata. Calls without token/cost metadata should still record provider/model/duration and mark the node partial.

The wrapper must preserve the legacy `model_info` value. `TradingAgentsGraph` should either unwrap the original LLM when deriving `model_info` or use a helper that reports the original class/model instead of the wrapper class.

Direct internal OpenAI calls in dataflow helpers, including the OpenAI-backed tool paths in `tradingagents/dataflows/interface.py`, should call the same recorder when a model usage context is active. These records should use the active canonical node key and should be partial if the direct API response does not expose token or cost details.

`SignalProcessor` runs after graph execution, so `TradingAgentsGraph.propagate()` should set `node_name="signal_processor"` around `self.process_signal(...)`.

## Persistence and API

`TradingAgentsGraph.propagate()` should attach `final_state["model_usage"]`. The service layer should copy `model_usage` into the in-memory result, `analysis_tasks.result`, and `analysis_reports`.

Concrete backend paths that must be updated:

- `app/services/analysis_service.py` graph calls should pass the active `task_id` into `propagate(...)`.
- `app/models/analysis.py` should add optional `model_usage` to `AnalysisResult`.
- `app/services/analysis/status_update_utils.py` should preserve `model_usage` when persisting `AnalysisResult.dict()`.
- `app/services/simple_analysis_service.py` should add `model_usage` to the runtime result, the `analysis_reports` document, the `analysis_tasks.result` mirror, and the complete-save wrapper.

Result APIs should return `model_usage` from all existing sources:

- in-memory task result
- `analysis_reports`
- `analysis_tasks.result` fallback

`app/routers/analysis.py` should add `model_usage` to the explicit result builders for in-memory, `analysis_reports`, and `analysis_tasks.result` fallback paths, and to the final response whitelist. While touching these whitelists, preserve the existing `model_info` field consistently.

Report APIs should return `model_usage` for list/detail responses where `model_info` is already returned. `app/routers/reports.py` should include `model_usage` in the report list, the report detail response backed by `analysis_reports`, and the report detail fallback backed by `analysis_tasks.result`.

## Frontend Design

`ReportDetail.vue` should render model usage metadata for each report module when available:

- model: `provider / model`
- token: `<input> in / <output> out`
- cost: currency-aware cost, such as `¥<cost>`, `$<cost>`, or `<currency> <cost>`
- duration: `<seconds>s`

Map report keys to node keys:

| Report key | Model usage node |
| --- | --- |
| market_report | market_analyst |
| fundamentals_report | fundamentals_analyst |
| news_report | news_analyst |
| sentiment_report | social_analyst |
| value_report | value_analyst |
| bull_researcher | bull_researcher |
| bear_researcher | bear_researcher |
| research_team_decision | research_manager |
| trader_investment_plan | trader |
| investment_plan | trader |
| risky_analyst | risky_analyst |
| safe_analyst | safe_analyst |
| neutral_analyst | neutral_analyst |
| risk_management_decision | risk_judge |
| final_trade_decision | risk_judge |

When `model_usage` or the node entry is absent, the UI should not render the metadata row. The metadata row should be rendered once at the top of each report module/tab, before content-specific rendering branches. This matters for `value_report`, which uses the Turtle payload component instead of the normal markdown branch.

When a node is `partial`, render known values normally and show unavailable token/cost values as `N/A` or equivalent UI copy, not as exact zero. Cost display should use `cost`/`currency` for single-currency nodes and `costs_by_currency` for mixed-currency nodes.

Frontend types in `frontend/src/types/analysis.ts` and any local report-detail interfaces should define optional `model_usage` so typed callers do not depend on ad hoc fields.

## Testing

Backend tests should cover:

- Value Analyst receives the deep LLM while other quick-role nodes still receive the quick LLM.
- Canonical graph node names and tool node names are normalized to the exact node keys used by the frontend.
- A wrapped node records calls under its node key.
- A `bind_tools()` returned runnable still records usage under the active node key.
- A SignalProcessor call records under `signal_processor`.
- Aggregation sums calls, tokens, cost, and duration.
- Mixed currencies are not collapsed into a single `total_cost`.
- Partial calls render stable aggregate data without pretending missing tokens or costs are zero.
- `model_usage` is copied into analysis result documents and returned by result/report API helpers.
- Service graph calls pass task id into `propagate(...)`, and concurrent tasks cannot share a `None` aggregate key.

Frontend tests or verification should cover:

- Report detail rendering does not change when `model_usage` is missing.
- A report module shows the matching node model metadata when `model_usage.nodes` contains data.
- The `investment_plan` compatibility report key maps to trader usage.
- The `value_report` Turtle payload path still shows model usage metadata.
- Partial and mixed-currency usage values are formatted without misleading zero-cost or zero-token displays.

## Rollout

The change is backward-compatible. Existing reports without `model_usage` continue to render. New reports include both `model_info` and `model_usage`; the UI prefers `model_usage` where available.
