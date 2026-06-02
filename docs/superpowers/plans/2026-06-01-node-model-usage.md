# Node Model Usage Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record actual per-node model usage, persist it through result/report APIs, render it in report detail modules, and move Value Analyst to the deep model.

**Architecture:** Add a small graph runtime recorder backed by contextvars and a task-scoped aggregate store. Instrument graph nodes and LLM invocation paths at the graph boundary, then explicitly thread `model_usage` through existing whitelist-based persistence and API responses. The frontend consumes the stored aggregate only when present.

**Tech Stack:** Python 3.11, LangChain/LangGraph, FastAPI, MongoDB documents, pytest, Vue 3, TypeScript, Element Plus.

**Branch Note:** Work in the existing `codex/node-model-usage` branch. Do not create a git worktree.

**Spec:** `docs/superpowers/specs/2026-06-01-node-model-usage-design.md`

---

## File Structure

- Create `tradingagents/graph/model_usage.py`: canonical node keys, context managers, aggregation, usage extraction, LLM instrumentation helpers.
- Modify `tradingagents/graph/trading_graph.py`: wrap factory-created LLMs, establish run context, attach `final_state["model_usage"]`, preserve `model_info`, wrap `SignalProcessor`.
- Modify `tradingagents/graph/setup.py`: switch Value Analyst to the deep LLM and wrap graph node callables with canonical node contexts.
- Modify `tradingagents/dataflows/interface.py`: record direct OpenAI `responses.create` calls made inside tool/dataflow helpers when a graph node context is active.
- Modify `app/models/analysis.py`: add optional `model_usage` to `AnalysisResult`.
- Modify `app/services/analysis_service.py`: pass `task_id` into all `propagate` calls and copy `model_usage` into `AnalysisResult`.
- Modify `app/services/simple_analysis_service.py`: include `model_usage` in runtime result, `analysis_reports`, `analysis_tasks.result`, and complete-save paths.
- Modify `app/services/analysis/status_update_utils.py`: preserve `model_usage` when persisting `AnalysisResult`.
- Modify `app/routers/analysis.py`: add `model_usage` and preserve `model_info` in all result builders and final whitelist.
- Modify `app/routers/reports.py`: return `model_usage` in list and detail responses, including task-result fallback.
- Modify `frontend/src/types/analysis.ts`: add shared `ModelUsage` types and optional fields.
- Modify `frontend/src/views/Reports/ReportDetail.vue`: render module-level model usage metadata, including the `value_report` special rendering path.
- Add tests under `tests/unit/`: `test_model_usage.py`, `test_model_usage_llm_wrapper.py`, and focused service/router tests near existing analysis/report tests.

---

### Task 1: Runtime Recorder Contract

**Files:**
- Create: `tests/unit/test_model_usage.py`
- Create: `tradingagents/graph/model_usage.py`

- [ ] **Step 1: Write failing tests for canonical keys, aggregation, partial values, and cleanup**

Create `tests/unit/test_model_usage.py` with tests using this API:

```python
from tradingagents.graph.model_usage import (
    canonical_node_key,
    clear_model_usage,
    get_model_usage_snapshot,
    model_usage_context,
    record_llm_call,
)


def test_canonical_node_keys_cover_graph_and_tool_nodes():
    assert canonical_node_key("Market Analyst") == "market_analyst"
    assert canonical_node_key("tools_market") == "market_analyst"
    assert canonical_node_key("Value Analyst") == "value_analyst"
    assert canonical_node_key("tools_value") == "value_analyst"
    assert canonical_node_key("Risk Judge") == "risk_judge"
    assert canonical_node_key("SignalProcessor") == "signal_processor"


def test_record_llm_call_aggregates_under_current_node():
    with model_usage_context(task_id="task-1"):
        with model_usage_context(node_name="Value Analyst"):
            record_llm_call(
                provider="codex",
                model="gpt-5.5",
                duration_seconds=1.25,
                input_tokens=100,
                output_tokens=25,
                cost=0.12,
                currency="CNY",
            )

    snapshot = get_model_usage_snapshot("task-1")
    node = snapshot["nodes"]["value_analyst"]
    assert node["display_name"] == "价值投资分析"
    assert node["provider"] == "codex"
    assert node["model"] == "gpt-5.5"
    assert node["calls"] == 1
    assert node["input_tokens"] == 100
    assert node["output_tokens"] == 25
    assert node["cost"] == 0.12
    assert node["currency"] == "CNY"
    assert node["partial"] is False
    assert snapshot["summary"]["total_calls"] == 1
    assert snapshot["summary"]["costs_by_currency"] == {"CNY": 0.12}


def test_partial_and_mixed_currency_snapshot():
    with model_usage_context(task_id="task-2"):
        with model_usage_context(node_name="News Analyst"):
            record_llm_call(provider="openai", model="gpt-4.1", duration_seconds=0.5, cost=0.01, currency="USD")
            record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.7)

    node = get_model_usage_snapshot("task-2")["nodes"]["news_analyst"]
    assert node["provider"] == "mixed"
    assert node["model"] == "mixed"
    assert node["providers"] == ["codex", "openai"]
    assert node["models"] == ["gpt-4.1", "gpt-5.5"]
    assert node["cost"] is None
    assert node["currency"] is None
    assert node["costs_by_currency"] == {"USD": 0.01}
    assert node["partial"] is True


def test_clear_model_usage_removes_task_snapshot():
    with model_usage_context(task_id="task-3", node_name="Trader"):
        record_llm_call(provider="deepseek", model="flash", duration_seconds=0.1)
    assert get_model_usage_snapshot("task-3")["summary"]["total_calls"] == 1
    clear_model_usage("task-3")
    assert get_model_usage_snapshot("task-3")["summary"]["total_calls"] == 0
```

- [ ] **Step 2: Run tests and verify they fail because the module does not exist**

Run:

```bash
uv run pytest tests/unit/test_model_usage.py -q
```

Expected: import failure for `tradingagents.graph.model_usage`.

- [ ] **Step 3: Implement `tradingagents/graph/model_usage.py`**

Implement these public functions and constants:

```python
CANONICAL_NODE_KEYS = {
    "Market Analyst": "market_analyst",
    "tools_market": "market_analyst",
    "Fundamentals Analyst": "fundamentals_analyst",
    "tools_fundamentals": "fundamentals_analyst",
    "News Analyst": "news_analyst",
    "tools_news": "news_analyst",
    "Social Analyst": "social_analyst",
    "tools_social": "social_analyst",
    "Value Analyst": "value_analyst",
    "tools_value": "value_analyst",
    "Bull Researcher": "bull_researcher",
    "Bear Researcher": "bear_researcher",
    "Research Manager": "research_manager",
    "Trader": "trader",
    "Risky Analyst": "risky_analyst",
    "Safe Analyst": "safe_analyst",
    "Neutral Analyst": "neutral_analyst",
    "Risk Judge": "risk_judge",
    "SignalProcessor": "signal_processor",
    "signal_processor": "signal_processor",
}

DISPLAY_NAMES = {
    "market_analyst": "市场技术分析",
    "fundamentals_analyst": "基本面分析",
    "news_analyst": "新闻事件分析",
    "social_analyst": "市场情绪分析",
    "value_analyst": "价值投资分析",
    "bull_researcher": "多头研究员",
    "bear_researcher": "空头研究员",
    "research_manager": "研究经理决策",
    "trader": "交易员计划",
    "risky_analyst": "激进分析师",
    "safe_analyst": "保守分析师",
    "neutral_analyst": "中性分析师",
    "risk_judge": "投资组合经理",
    "signal_processor": "信号处理",
}
```

Required behavior:

- `model_usage_context(task_id=None, node_name=None)` sets only the provided contextvars and restores previous values on exit.
- `record_llm_call` is a no-op when no task id or no node name is active.
- Aggregates are protected by a `threading.RLock`.
- `get_model_usage_snapshot(task_id)` returns a stable empty structure for missing tasks.
- `providers` and `models` are sorted unique lists.
- Single provider/model/currency populate the convenience `provider`, `model`, `cost`, and `currency` fields.
- Mixed provider/model/currency uses `"mixed"` for provider/model and `None` for cost/currency.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
uv run pytest tests/unit/test_model_usage.py -q
```

Expected: all tests in `test_model_usage.py` pass.

Commit:

```bash
git add tradingagents/graph/model_usage.py tests/unit/test_model_usage.py
git commit -m "feat: add node model usage recorder"
```

---

### Task 2: LLM Invocation Instrumentation

**Files:**
- Modify: `tradingagents/graph/model_usage.py`
- Modify: `tradingagents/graph/trading_graph.py`
- Create: `tests/unit/test_model_usage_llm_wrapper.py`
- Update if needed: `tests/unit/test_create_llm_by_provider_subscription.py`

- [ ] **Step 1: Write failing wrapper tests**

Create `tests/unit/test_model_usage_llm_wrapper.py`:

```python
from langchain_core.messages import AIMessage

from tradingagents.graph.model_usage import (
    describe_llm,
    get_model_usage_snapshot,
    instrument_llm_for_model_usage,
    model_usage_context,
)


class FakeBoundRunnable:
    model_name = "fake-model"

    def invoke(self, value):
        return AIMessage(
            content="bound-ok",
            usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        )


class FakeLLM:
    model_name = "fake-model"

    def invoke(self, value):
        return AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
        )

    def bind_tools(self, tools):
        return FakeBoundRunnable()


def test_instrumented_invoke_records_usage_and_preserves_description():
    llm = instrument_llm_for_model_usage(FakeLLM(), provider="codex", model="gpt-5.5")
    assert describe_llm(llm) == "FakeLLM:fake-model"

    with model_usage_context(task_id="task-wrapper", node_name="Trader"):
        llm.invoke("hello")

    node = get_model_usage_snapshot("task-wrapper")["nodes"]["trader"]
    assert node["calls"] == 1
    assert node["provider"] == "codex"
    assert node["model"] == "gpt-5.5"
    assert node["input_tokens"] == 11
    assert node["output_tokens"] == 5


def test_bind_tools_result_is_instrumented():
    llm = instrument_llm_for_model_usage(FakeLLM(), provider="codex", model="gpt-5.5")
    bound = llm.bind_tools([])

    with model_usage_context(task_id="task-bound", node_name="Value Analyst"):
        bound.invoke({"messages": []})

    node = get_model_usage_snapshot("task-bound")["nodes"]["value_analyst"]
    assert node["calls"] == 1
    assert node["input_tokens"] == 7
    assert node["output_tokens"] == 3
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/unit/test_model_usage_llm_wrapper.py -q
```

Expected: import failure for `instrument_llm_for_model_usage` and `describe_llm`.

- [ ] **Step 3: Implement instrumentation helpers**

In `tradingagents/graph/model_usage.py`, add:

- `instrument_llm_for_model_usage(llm, provider, model, currency=None)`
- `describe_llm(llm)`
- usage extraction helpers that read `usage_metadata`, `response_metadata.token_usage`, `llm_output.token_usage`, and `usage`.

Implementation constraints:

- Prefer method-level instrumentation so class-sensitive code like `llm.__class__.__name__` continues to reflect the underlying LLM.
- Use `object.__setattr__` when LangChain/Pydantic objects block normal attribute assignment.
- Store original methods under private attributes such as `_model_usage_original_invoke` to avoid double wrapping.
- Instrument return values from `bind_tools` with the same provider/model/currency metadata.
- Record duration with `time.perf_counter()`.

- [ ] **Step 4: Refactor `create_llm_by_provider` to instrument all returned LLMs**

In `tradingagents/graph/trading_graph.py`, avoid early returns that bypass instrumentation. Use a local `llm` variable and return the instrumented object once. Keep each provider branch's current constructor arguments unchanged; change only direct constructor returns to local assignment inside the branch and add one final return:

```python
from tradingagents.graph.model_usage import describe_llm, instrument_llm_for_model_usage


def create_llm_by_provider(provider: str, model: str, backend_url: str, temperature: float, max_tokens: int, timeout: int, api_key: str = None):
    provider_key = provider.lower()
    llm = None
    if provider_key == "codex":
        llm = ChatCodexOAuth(model=model, access_token=api_key, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
    if llm is None:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    return instrument_llm_for_model_usage(llm, provider=provider, model=model)
```

Preserve existing provider-specific validation and exception messages while changing the return structure.

- [ ] **Step 5: Update model info derivation**

In `TradingAgentsGraph.propagate()`, replace direct `self.deep_thinking_llm.__class__.__name__` formatting with:

```python
model_info = describe_llm(self.deep_thinking_llm)
```

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run pytest tests/unit/test_model_usage_llm_wrapper.py tests/unit/test_create_llm_by_provider_subscription.py -q
```

Expected: wrapper tests pass and subscription factory tests still pass.

Commit:

```bash
git add tradingagents/graph/model_usage.py tradingagents/graph/trading_graph.py tests/unit/test_model_usage_llm_wrapper.py tests/unit/test_create_llm_by_provider_subscription.py
git commit -m "feat: instrument graph llm usage"
```

---

### Task 3: Graph Node Context and Value Analyst Deep Model

**Files:**
- Modify: `tradingagents/graph/setup.py`
- Modify: `tradingagents/graph/trading_graph.py`
- Modify: `tests/unit/test_value_analyst_entry.py`
- Create: `tests/unit/test_graph_model_usage_context.py`

- [ ] **Step 1: Add failing tests for Value Analyst model assignment and node wrapping**

Update `tests/unit/test_value_analyst_entry.py` with assertions that the Value Analyst factory receives `deep_thinking_llm`. Use monkeypatches for `create_value_analyst` and at least one quick-role factory to capture arguments.

Create `tests/unit/test_graph_model_usage_context.py`:

```python
from tradingagents.graph.model_usage import get_model_usage_snapshot, model_usage_context, record_llm_call
from tradingagents.graph.setup import GraphSetup


def test_wrapped_graph_node_sets_canonical_context():
    def node(state):
        record_llm_call(provider="codex", model="gpt-5.5", duration_seconds=0.1)
        return {"ok": True}

    wrapped = GraphSetup._wrap_model_usage_node("Value Analyst", node)

    with model_usage_context(task_id="task-node"):
        assert wrapped({}) == {"ok": True}

    assert get_model_usage_snapshot("task-node")["nodes"]["value_analyst"]["calls"] == 1
```

- [ ] **Step 2: Run tests and verify failures**

Run:

```bash
uv run pytest tests/unit/test_value_analyst_entry.py tests/unit/test_graph_model_usage_context.py -q
```

Expected: Value Analyst still uses quick LLM, and `_wrap_model_usage_node` does not exist.

- [ ] **Step 3: Switch Value Analyst to deep LLM**

In `tradingagents/graph/setup.py`, change:

```python
analyst_nodes["value"] = create_value_analyst(
    self.quick_thinking_llm, self.toolkit
)
```

to:

```python
analyst_nodes["value"] = create_value_analyst(
    self.deep_thinking_llm, self.toolkit
)
```

- [ ] **Step 4: Add graph node wrapper**

Add a static helper in `GraphSetup`:

```python
@staticmethod
def _wrap_model_usage_node(graph_node_name, node):
    from tradingagents.graph.model_usage import model_usage_context

    def wrapped_node(state):
        with model_usage_context(node_name=graph_node_name):
            return node(state)

    return wrapped_node
```

Use it for all LLM-bearing graph nodes and tool nodes when calling `workflow.add_node`. Do not wrap `Msg Clear <analyst>` delete nodes because they do not make LLM calls.

- [ ] **Step 5: Establish run context and SignalProcessor context in `propagate()`**

In `tradingagents/graph/trading_graph.py`:

- Create a `run_id = task_id or f"local-{uuid.uuid4().hex}"`.
- Wrap graph execution in `with model_usage_context(task_id=run_id):`.
- Wrap signal processing in `with model_usage_context(node_name="SignalProcessor")`.
- Assign `final_state["model_usage"] = get_model_usage_snapshot(run_id)` before `self.curr_state = final_state`.
- Call `clear_model_usage(run_id)` in a `finally` block after the snapshot is copied.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
uv run pytest tests/unit/test_value_analyst_entry.py tests/unit/test_graph_model_usage_context.py tests/unit/test_model_usage.py tests/unit/test_model_usage_llm_wrapper.py -q
```

Expected: all listed tests pass.

Commit:

```bash
git add tradingagents/graph/setup.py tradingagents/graph/trading_graph.py tests/unit/test_value_analyst_entry.py tests/unit/test_graph_model_usage_context.py
git commit -m "feat: record model usage by graph node"
```

---

### Task 4: Direct OpenAI Dataflow Recording

**Files:**
- Modify: `tradingagents/dataflows/interface.py`
- Modify: `tradingagents/graph/model_usage.py`
- Create: `tests/unit/test_model_usage_dataflow_openai.py`

- [ ] **Step 1: Add failing tests for direct OpenAI response recording**

Create `tests/unit/test_model_usage_dataflow_openai.py` with a small helper test that calls a new function named `record_openai_response_usage`:

```python
from types import SimpleNamespace

from tradingagents.graph.model_usage import (
    get_model_usage_snapshot,
    model_usage_context,
    record_openai_response_usage,
)


def test_record_openai_response_usage_uses_active_context():
    response = SimpleNamespace(
        model="gpt-4.1-mini",
        usage=SimpleNamespace(input_tokens=20, output_tokens=6),
    )

    with model_usage_context(task_id="task-openai", node_name="tools_news"):
        record_openai_response_usage(response, provider="openai", default_model="gpt-4.1-mini", duration_seconds=0.2, currency="USD")

    node = get_model_usage_snapshot("task-openai")["nodes"]["news_analyst"]
    assert node["calls"] == 1
    assert node["provider"] == "openai"
    assert node["model"] == "gpt-4.1-mini"
    assert node["input_tokens"] == 20
    assert node["output_tokens"] == 6
```

- [ ] **Step 2: Implement helper and instrument direct paths**

Add `record_openai_response_usage` in `model_usage.py`; it should extract usage from both object attributes and dict-like responses. In `tradingagents/dataflows/interface.py`, wrap each direct `client.responses.create` call with timing:

```python
start = time.perf_counter()
response = client.responses.create(
    model=config["quick_think_llm"],
    input=query,
)
duration = time.perf_counter() - start
record_openai_response_usage(
    response,
    provider="openai",
    default_model=config.get("quick_think_llm") or config.get("deep_think_llm") or "",
    duration_seconds=duration,
    currency="USD",
)
```

Import `time` and `record_openai_response_usage` where needed. If a helper uses a provider-specific config value, pass that model string as `default_model`.

- [ ] **Step 3: Run tests and commit**

Run:

```bash
uv run pytest tests/unit/test_model_usage_dataflow_openai.py tests/unit/test_model_usage.py -q
```

Expected: all listed tests pass.

Commit:

```bash
git add tradingagents/dataflows/interface.py tradingagents/graph/model_usage.py tests/unit/test_model_usage_dataflow_openai.py
git commit -m "feat: record direct openai tool usage"
```

---

### Task 5: Persistence and Result API Propagation

**Files:**
- Modify: `app/models/analysis.py`
- Modify: `app/services/analysis_service.py`
- Modify: `app/services/simple_analysis_service.py`
- Modify: `app/services/analysis/status_update_utils.py`
- Modify: `app/routers/analysis.py`
- Modify: `app/routers/reports.py`
- Create or update focused backend tests under `tests/unit/`

- [ ] **Step 1: Add failing service/router tests**

Add tests that build result dictionaries containing:

```python
MODEL_USAGE_SAMPLE = {
    "summary": {"total_calls": 1, "total_input_tokens": 10, "total_output_tokens": 4, "total_duration_seconds": 0.5, "costs_by_currency": {"CNY": 0.01}},
    "nodes": {"value_analyst": {"display_name": "价值投资分析", "provider": "codex", "model": "gpt-5.5", "providers": ["codex"], "models": ["gpt-5.5"], "calls": 1, "input_tokens": 10, "output_tokens": 4, "cost": 0.01, "currency": "CNY", "costs_by_currency": {"CNY": 0.01}, "duration_seconds": 0.5, "partial": False, "partial_reason": None}},
}
```

Cover these expectations:

- `AnalysisResult(model_usage=MODEL_USAGE_SAMPLE).dict()` includes `model_usage`.
- `analysis_service.py` calls `trading_graph.propagate` with `task_id=task.task_id` in all three execution paths.
- Result reconstruction from `analysis_reports` includes `model_info` and `model_usage`.
- Result reconstruction from `analysis_tasks.result` includes `model_info` and `model_usage`.
- Report list/detail responses include `model_usage` whenever `model_info` is returned.

- [ ] **Step 2: Extend typed model**

In `app/models/analysis.py`, add:

```python
model_usage: Optional[Dict[str, Any]] = None
```

to `AnalysisResult` next to `model_info`.

- [ ] **Step 3: Pass task ids into graph execution**

In `app/services/analysis_service.py`, update all three call sites:

```python
_, decision = trading_graph.propagate(task.symbol, analysis_date, progress_callback, task_id=task.task_id)
_, decision = trading_graph.propagate(task.symbol, analysis_date, task_id=task.task_id)
```

Use the current task id variable in the local scope.

- [ ] **Step 4: Copy model usage into service results**

Where a service receives `(state, decision)`, extract:

```python
model_usage = state.get("model_usage", {}) if isinstance(state, dict) else {}
```

Add `model_usage` to runtime result dictionaries, `AnalysisResult` construction, `analysis_reports` documents, and `analysis_tasks.result` mirrors. Preserve existing `model_info`.

- [ ] **Step 5: Update whitelist routers**

In `app/routers/analysis.py` and `app/routers/reports.py`, add:

```python
"model_usage": source.get("model_usage", {}),
```

to every explicit response builder that already includes or should include `model_info`.

- [ ] **Step 6: Run focused backend tests and commit**

Run:

```bash
uv run pytest tests/unit/test_model_usage.py tests/unit/test_model_usage_llm_wrapper.py tests/unit/test_value_analyst_entry.py tests/unit/test_turtle_save_canonical_payload.py -q
```

Run any new service/router test file directly:

```bash
uv run pytest tests/unit/test_model_usage_persistence.py -q
```

Expected: all listed tests pass.

Commit:

```bash
git add app/models/analysis.py app/services/analysis_service.py app/services/simple_analysis_service.py app/services/analysis/status_update_utils.py app/routers/analysis.py app/routers/reports.py tests/unit/test_model_usage_persistence.py
git commit -m "feat: persist node model usage"
```

---

### Task 6: Report Detail UI

**Files:**
- Modify: `frontend/src/types/analysis.ts`
- Modify: `frontend/src/views/Reports/ReportDetail.vue`

- [ ] **Step 1: Add frontend types**

In `frontend/src/types/analysis.ts`, add:

```ts
export interface ModelUsageNode {
  display_name: string
  provider: string
  model: string
  providers?: string[]
  models?: string[]
  calls: number
  input_tokens: number
  output_tokens: number
  cost?: number | null
  currency?: string | null
  costs_by_currency?: Record<string, number>
  duration_seconds: number
  partial?: boolean
  partial_reason?: string | null
}

export interface ModelUsage {
  summary?: {
    total_calls: number
    total_input_tokens: number
    total_output_tokens: number
    total_duration_seconds: number
    costs_by_currency?: Record<string, number>
  }
  nodes?: Record<string, ModelUsageNode>
}
```

Add optional fields to `AnalysisResult`:

```ts
model_info?: string
model_usage?: ModelUsage
```

- [ ] **Step 2: Add ReportDetail helpers**

In `ReportDetail.vue`, add a module-to-node map:

```ts
const moduleModelUsageMap: Record<string, string> = {
  market_report: 'market_analyst',
  fundamentals_report: 'fundamentals_analyst',
  news_report: 'news_analyst',
  sentiment_report: 'social_analyst',
  value_report: 'value_analyst',
  bull_researcher: 'bull_researcher',
  bear_researcher: 'bear_researcher',
  research_team_decision: 'research_manager',
  trader_investment_plan: 'trader',
  investment_plan: 'trader',
  risky_analyst: 'risky_analyst',
  safe_analyst: 'safe_analyst',
  neutral_analyst: 'neutral_analyst',
  risk_management_decision: 'risk_judge',
  final_trade_decision: 'risk_judge'
}
```

Add helpers `getModuleModelUsage(moduleName)`, `formatModelUsageTokens(node)`, `formatModelUsageCost(node)`, and `formatModelUsageDuration(node)`. `formatModelUsageCost` should render `¥`, `$`, or currency code based on `currency`/`costs_by_currency`.

- [ ] **Step 3: Render metadata before content branches**

Inside the tab pane `<div class="module-content">`, add a metadata row before the `value_report` branch:

```vue
<div v-if="getModuleModelUsage(String(moduleName))" class="module-model-usage">
  <el-tag size="small" type="info">{{ getModuleModelUsage(String(moduleName))?.provider }} / {{ getModuleModelUsage(String(moduleName))?.model }}</el-tag>
  <span>{{ formatModelUsageTokens(getModuleModelUsage(String(moduleName))) }}</span>
  <span>{{ formatModelUsageCost(getModuleModelUsage(String(moduleName))) }}</span>
  <span>{{ formatModelUsageDuration(getModuleModelUsage(String(moduleName))) }}</span>
</div>
```

The helper should return `undefined` when `report.model_usage` or the node entry is absent.

- [ ] **Step 4: Add scoped styles**

Add styles that do not shift the module layout:

```css
.module-model-usage {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
  margin-bottom: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
```

- [ ] **Step 5: Run frontend checks and commit**

Run:

```bash
cd frontend && npm run type-check
```

Expected: TypeScript check succeeds.

Commit:

```bash
git add frontend/src/types/analysis.ts frontend/src/views/Reports/ReportDetail.vue
git commit -m "feat: show report module model usage"
```

---

### Task 7: Final Verification and PR

**Files:**
- No new implementation files unless a previous task exposes a focused fix.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
uv run pytest tests/unit/test_model_usage.py tests/unit/test_model_usage_llm_wrapper.py tests/unit/test_model_usage_dataflow_openai.py tests/unit/test_graph_model_usage_context.py tests/unit/test_value_analyst_entry.py tests/unit/test_model_usage_persistence.py -q
```

Expected: all listed tests pass.

- [ ] **Step 2: Run existing Turtle/report regression tests**

Run:

```bash
uv run pytest tests/unit/test_financial_report_adapter.py tests/unit/test_financial_report_config.py tests/unit/test_turtle_facts.py tests/unit/test_turtle_report_adapter.py tests/unit/test_turtle_save_canonical_payload.py -q
```

Expected: all listed tests pass.

- [ ] **Step 3: Run frontend type check**

Run:

```bash
cd frontend && npm run type-check
```

Expected: TypeScript check succeeds.

- [ ] **Step 4: Push branch and create PR**

Run:

```bash
git status --short --branch
git push -u origin codex/node-model-usage
gh pr create --draft --title "feat: record node model usage" --body-file /tmp/node-model-usage-pr.md
```

PR body should mention:

- Value Analyst now uses the deep model.
- Node-level `model_usage` records provider/model/tokens/cost/duration.
- Existing `model_info` remains.
- API and ReportDetail handle missing `model_usage`.
- Test commands and results from the verification steps above.
