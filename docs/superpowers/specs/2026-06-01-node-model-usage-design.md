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
2. Each LLM call made during an analysis must be attributed to the graph node or post-graph step that caused it.
3. The stored analysis result must include a node-level aggregate `model_usage` field.
4. The report detail page must show model usage metadata beside each report module when available.
5. Existing API clients and UI paths must continue working when `model_usage` is absent.
6. The old `model_info` field must remain for backward compatibility.

## Non-Goals

- Do not add single-call detail rows to the report page.
- Do not redesign the Token Statistics page.
- Do not change whether quick analysis runs the downstream research, trading, and risk pipeline.
- Do not remove or rewrite the existing `token_usage` collection.

## Model Assignment

The graph should use these model roles:

| Node | Model role |
| --- | --- |
| Market Analyst | quick |
| Fundamentals Analyst | quick |
| News Analyst | quick |
| Social Analyst | quick |
| Value Analyst | deep |
| Bull Researcher | quick |
| Bear Researcher | quick |
| Research Manager | deep |
| Trader | quick |
| Risky Analyst | quick |
| Safe Analyst | quick |
| Neutral Analyst | quick |
| Risk Judge | deep |
| SignalProcessor | quick |

The Value Analyst change is intentional because Turtle value analysis is a decision-quality report and should use the higher-quality deep model.

## Data Model

Add `model_usage` to analysis results and report documents:

```json
{
  "model_usage": {
    "summary": {
      "total_calls": 12,
      "total_input_tokens": 12345,
      "total_output_tokens": 2345,
      "total_cost": 0.1234,
      "total_duration_seconds": 45.67
    },
    "nodes": {
      "value_analyst": {
        "display_name": "价值投资分析",
        "provider": "codex",
        "model": "gpt-5.5",
        "calls": 2,
        "input_tokens": 3000,
        "output_tokens": 900,
        "cost": 0.08,
        "duration_seconds": 12.3,
        "partial": false
      }
    }
  }
}
```

`partial` is `true` when a call had provider/model/duration but did not expose token or cost data. Token and cost totals should use `0` for missing numeric values so the UI can render stable aggregates.

## Backend Recording Design

Create a small runtime module, `tradingagents/graph/model_usage.py`, responsible for:

- Tracking the current `task_id` and `node_name` with `contextvars.ContextVar`.
- Providing `model_usage_context(task_id, node_name)` to wrap node execution.
- Providing `record_llm_call(...)` for LLM wrappers to report actual usage.
- Providing `get_model_usage_snapshot(task_id)` to return aggregate data.
- Clearing temporary records after `TradingAgentsGraph.propagate()` copies the snapshot into final state.

Wrap graph node callables when adding them in `GraphSetup.setup_graph()`. This is more reliable than trying to infer the current node from `graph.stream()` output, because stream chunks are yielded after node execution.

Wrap LLM instances returned by `create_llm_by_provider()` with a lightweight proxy that records:

- provider
- model
- duration
- input tokens
- output tokens
- cost when exposed by adapter metadata

The proxy should delegate all unknown attributes to the wrapped LLM so existing LangChain behavior continues to work.

`SignalProcessor` runs after graph execution, so `TradingAgentsGraph.propagate()` should set `node_name="signal_processor"` around `self.process_signal(...)`.

## Persistence and API

`TradingAgentsGraph.propagate()` should attach `final_state["model_usage"]`. The service layer should copy `model_usage` into the in-memory result, `analysis_tasks.result`, and `analysis_reports`.

Result APIs should return `model_usage` from all existing sources:

- in-memory task result
- `analysis_reports`
- `analysis_tasks.result` fallback

Report APIs should return `model_usage` for list/detail responses where `model_info` is already returned.

## Frontend Design

`ReportDetail.vue` should render model usage metadata for each report module when available:

- model: `provider / model`
- token: `<input> in / <output> out`
- cost: `¥<cost>`
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
| risky_analyst | risky_analyst |
| safe_analyst | safe_analyst |
| neutral_analyst | neutral_analyst |
| risk_management_decision | risk_judge |
| final_trade_decision | risk_judge |

When `model_usage` or the node entry is absent, the UI should not render the metadata row.

## Testing

Backend tests should cover:

- Value Analyst receives the deep LLM while other quick-role nodes still receive the quick LLM.
- A wrapped node records calls under its node key.
- A SignalProcessor call records under `signal_processor`.
- Aggregation sums calls, tokens, cost, and duration.
- `model_usage` is copied into analysis result documents and returned by result/report API helpers.

Frontend tests or verification should cover:

- Report detail rendering does not change when `model_usage` is missing.
- A report module shows the matching node model metadata when `model_usage.nodes` contains data.

## Rollout

The change is backward-compatible. Existing reports without `model_usage` continue to render. New reports include both `model_info` and `model_usage`; the UI prefers `model_usage` where available.
