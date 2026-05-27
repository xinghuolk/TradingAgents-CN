# Extractor Codex Token Injection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let TradingAgents-CN inject its per-request codex OAuth access token into the in-process `financial-report-llm-extractor`, so the LLM-supplement step works under the web-OAuth deployment — while fully preserving the extractor's standalone `~/.codex/auth.json` resolution.

**Architecture:** Two repos. (1) The extractor gains an optional `ExtractorConfig.subscription_token` threaded down to `CodexResponsesClient`, which prefers it over file resolution (additive, backward-compatible). (2) TradingAgents-CN's adapter accepts a `subscription_token` and the fundamentals tool reads the per-request token from the (already class-global) analysis config — only when the bridged deep provider is `codex`. The token travels programmatically only: never written to the transport JSON, never an env var, never cached or logged.

**Tech Stack:** Python 3.10+/3.11+, pytest. Extractor: zero runtime deps, stdlib, frozen dataclasses. TradingAgents-CN: existing financial_reports adapter (Apache).

**Repos & branches:**
- **Extractor** (`/Users/like/source/financial-report-llm-extractor`): new branch `feat/inject-subscription-token`. Tests via its own `.venv/bin/pytest`. Follow its AGENTS.md (zero deps, keyword-only where idiomatic).
- **TradingAgents-CN** (`/Users/like/source/TradingAgents-CN`): continue on existing branch `feat/financial-report-llm-config-reuse` (stacked on PR #13). Tests via `.venv/bin/pytest`.

**Spec:** `docs/superpowers/specs/2026-05-28-extractor-codex-token-injection-design.md`

---

## File Structure

**Extractor repo:**
| File | Responsibility | Action |
|---|---|---|
| `src/financial_report_llm_extractor/llm_transport.py` | `create_llm_client` + `CodexResponsesClient` accept/prefer injected token | Modify |
| `src/financial_report_llm_extractor/client.py` | `ExtractorConfig.subscription_token` field; `get_extraction` forwards it | Modify |
| `src/financial_report_llm_extractor/pipeline_core.py` | `run_pipeline` forwards `subscription_token` | Modify |
| `src/financial_report_llm_extractor/structured_sources/company_evaluation.py` | `run_company_evaluation` + `_run_llm_supplement_step` forward it to `create_llm_client` | Modify |
| `tests/test_llm_transport.py` | behavioral tests for injected token (Task 1) | Modify |
| `tests/test_subscription_token_threading.py` | field + forwarding-contract tests (Task 2) | Create |

**TradingAgents-CN repo:**
| File | Responsibility | Action |
|---|---|---|
| `tradingagents/dataflows/financial_reports/adapter.py` | adapter accepts/propagates `subscription_token`; `resolve_injected_codex_token` helper | Modify |
| `tradingagents/agents/utils/agent_utils.py` | fundamentals call site reads token (codex only), passes it | Modify |
| `tests/unit/test_financial_report_adapter.py` | adapter + helper tests | Modify |
| `docs/LLM_CONFIG_AND_EXTRACTOR_INTEGRATION.md` | flip ③ to supported + concurrency caveat | Modify |

---

# Part 1 — Extractor repo (`/Users/like/source/financial-report-llm-extractor`)

First: `cd /Users/like/source/financial-report-llm-extractor && git checkout -b feat/inject-subscription-token`

## Task 1: CodexResponsesClient prefers an injected token

**Files:**
- Modify: `src/financial_report_llm_extractor/llm_transport.py` (`create_llm_client` ~line 322; `CodexResponsesClient.__init__` ~line 472; `_post_with_retries` ~line 583)
- Test: `tests/test_llm_transport.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_llm_transport.py`; reuses existing `FakeHttpTransport`, `_jwt_with_exp` at top of that file)

```python
def test_codex_client_prefers_injected_token(monkeypatch: Any, tmp_path: Path) -> None:
    # No ~/.codex auth file: if the client fell back to file resolution it would raise.
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    injected = _jwt_with_exp(
        int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        account_id="acct-injected",
    )
    transport = FakeHttpTransport(
        [
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {"fields": [{"field_id": "cash", "status": "missing"}]}
                                ),
                            }
                        ],
                    }
                ]
            }
        ]
    )
    client = create_llm_client(
        LlmTransportConfig(
            provider="openai-codex",
            model="gpt-5.3-codex",
            base_url="https://chatgpt.com/backend-api/codex",
            api_key_env="",
        ),
        transport=transport,
        subscription_token=injected,
    )
    client.extract(PromptRequest(field_id="cash", candidates=()))

    _url, headers, _payload, _timeout = transport.calls[0]
    assert headers["Authorization"] == f"Bearer {injected}"
    assert headers["ChatGPT-Account-ID"] == "acct-injected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_llm_transport.py::test_codex_client_prefers_injected_token -v`
Expected: FAIL — `create_llm_client() got an unexpected keyword argument 'subscription_token'`.

- [ ] **Step 3: Implement** — three edits in `src/financial_report_llm_extractor/llm_transport.py`:

(3a) `create_llm_client` — add the keyword and forward it to the codex client only:

```python
def create_llm_client(
    config: LlmTransportConfig,
    *,
    transport: HttpTransport | None = None,
    cache_root: Path | None = None,
    subscription_token: str | None = None,
) -> LlmJsonClient:
    kind = resolve_provider_kind(config)
    if kind == "openai-compatible":
        return OpenAiCompatibleClient(config, transport=transport, cache_root=cache_root)
    if kind == "gemini":
        return GeminiGenerateContentClient(config, transport=transport, cache_root=cache_root)
    if kind == "codex-responses":
        return CodexResponsesClient(
            config,
            transport=transport,
            cache_root=cache_root,
            subscription_token=subscription_token,
        )
    if kind == "anthropic-messages":
        return ClaudeCodeMessagesClient(config, transport=transport, cache_root=cache_root)
    raise ValueError(f"unsupported provider kind: {kind}")
```

(3b) `CodexResponsesClient.__init__` — accept and store the token:

```python
    def __init__(
        self,
        config: LlmTransportConfig,
        *,
        transport: HttpTransport | None = None,
        cache_root: Path | None = None,
        subscription_token: str | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibHttpTransport()
        self.raw_exchanges: list[RawExchange] = []
        self.cache_root = cache_root
        self.subscription_token = subscription_token
```

(3c) `_post_with_retries` — prefer the injected token over file resolution. Replace the body of the `try:` (currently `credentials = resolve_subscription_credentials("openai-codex")` then `post_json(... _codex_headers(credentials.access_token) ...)`) with:

```python
        for _ in range(attempts):
            try:
                access_token = (
                    self.subscription_token
                    or resolve_subscription_credentials("openai-codex").access_token
                )
                return self.transport.post_json(
                    f"{self.config.base_url.rstrip('/')}/responses",
                    _codex_headers(access_token),
                    payload,
                    self.config.timeout_seconds,
                )
            except (TimeoutError, URLError) as error:
                last_error = error
```

- [ ] **Step 4: Run tests to verify pass** (new test + the existing fallback regression)

Run: `.venv/bin/pytest tests/test_llm_transport.py -k "codex" -v`
Expected: PASS — both `test_codex_client_prefers_injected_token` and the existing `test_codex_client_builds_responses_request` (the latter is the None/file-fallback regression: it sets no `subscription_token`, so resolution falls back to `~/.codex/auth.json` and still asserts `Bearer {file token}`).

- [ ] **Step 5: Commit**

```bash
git add src/financial_report_llm_extractor/llm_transport.py tests/test_llm_transport.py
git commit -m "feat(codex): prefer injected subscription token over file resolution"
```

## Task 2: Thread subscription_token from ExtractorConfig to the codex client

**Files:**
- Modify: `src/financial_report_llm_extractor/client.py` (`ExtractorConfig` ~line 86; `get_extraction` `run_pipeline(...)` call ~line 443)
- Modify: `src/financial_report_llm_extractor/pipeline_core.py` (`run_pipeline` ~line 39; `run_company_evaluation(...)` call ~line 129)
- Modify: `src/financial_report_llm_extractor/structured_sources/company_evaluation.py` (`run_company_evaluation` ~line 342; `_run_llm_supplement_step` ~line 450; `create_llm_client(...)` call ~line 494)
- Test: `tests/test_subscription_token_threading.py`

> **Note on test strategy:** the actual token-usage behavior is fully covered by Task 1. Task 2 is mechanical keyword forwarding through four functions. We guard it with (a) a real field test and (b) a signature-contract test asserting each function exposes an optional `subscription_token`. Each forwarding edit is a single shown line, verified in review.

- [ ] **Step 1: Write the failing tests** — create `tests/test_subscription_token_threading.py`:

```python
import inspect

import pytest

from financial_report_llm_extractor.client import ExtractorConfig, FinancialReportClient
from financial_report_llm_extractor.pipeline_core import run_pipeline
from financial_report_llm_extractor.structured_sources.company_evaluation import (
    _run_llm_supplement_step,
    run_company_evaluation,
)


def test_extractor_config_has_optional_subscription_token():
    assert ExtractorConfig().subscription_token is None
    assert ExtractorConfig(subscription_token="tok").subscription_token == "tok"


@pytest.mark.parametrize(
    "func",
    [
        FinancialReportClient.get_extraction,
        run_pipeline,
        run_company_evaluation,
        _run_llm_supplement_step,
    ],
)
def test_subscription_token_is_optional_keyword(func):
    param = inspect.signature(func).parameters.get("subscription_token")
    assert param is not None, f"{func.__name__} is missing subscription_token"
    assert param.default is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_subscription_token_threading.py -v`
Expected: FAIL — `ExtractorConfig` has no `subscription_token`; parametrized cases fail on missing param.

- [ ] **Step 3: Implement the threading** (5 edits):

(3a) `client.py` `ExtractorConfig` — add field after `taxonomy_path` (keep all existing fields):

```python
    subscription_token: str | None = None
```

(3b) `client.py` `get_extraction` — in the `run_pipeline(...)` call (~line 443-460), add:

```python
                subscription_token=self.config.subscription_token,
```

(3c) `pipeline_core.py` `run_pipeline` — add to the keyword-only signature (after `no_cache: bool = False`):

```python
    subscription_token: str | None = None,
```
and in the `run_company_evaluation(...)` call (~line 129-140) add:

```python
        subscription_token=subscription_token,
```

(3d) `company_evaluation.py` `run_company_evaluation` — add to its keyword-only signature (after `cache_root`):

```python
    subscription_token: str | None = None,
```
and in the `_run_llm_supplement_step(...)` call (~line 377-387) add:

```python
            subscription_token=subscription_token,
```

(3e) `company_evaluation.py` `_run_llm_supplement_step` — add to its keyword-only signature (after `cache_root`):

```python
    subscription_token: str | None = None,
```
and change the client construction (~line 494) from
`json_client = create_llm_client(config, cache_root=cache_root)`
to:

```python
        json_client = create_llm_client(
            config, cache_root=cache_root, subscription_token=subscription_token
        )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/test_subscription_token_threading.py -v`
Expected: PASS (1 field test + 4 parametrized signature cases).

- [ ] **Step 5: Run the full extractor suite (standalone-unaffected regression)**

Run: `.venv/bin/pytest -q`
Expected: all pre-existing tests still pass (the new field defaults to None → CLI / existing callers unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/financial_report_llm_extractor/client.py src/financial_report_llm_extractor/pipeline_core.py src/financial_report_llm_extractor/structured_sources/company_evaluation.py tests/test_subscription_token_threading.py
git commit -m "feat: thread subscription_token from ExtractorConfig to codex client"
```

---

# Part 2 — TradingAgents-CN (`/Users/like/source/TradingAgents-CN`, branch `feat/financial-report-llm-config-reuse`)

## Task 3: Adapter accepts and propagates subscription_token

**Files:**
- Modify: `tradingagents/dataflows/financial_reports/adapter.py` (`FinancialReportAdapter.__init__` ~line 63; `ExtractorConfig(...)` in `get_annual_report_data` ~line 134; `create_financial_report_adapter` ~line 185)
- Test: `tests/unit/test_financial_report_adapter.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_financial_report_adapter.py`; `install_fake_extractor`, `FakeClient`, `FinancialReportClientConfig` already defined in the file)

```python
def test_adapter_passes_subscription_token_to_extractor_config(monkeypatch):
    install_fake_extractor(monkeypatch)
    adapter = FinancialReportAdapter(
        FinancialReportClientConfig(
            enabled=True, cache_only=True, force_refresh=False,
            include_llm_supplement=True, allow_llm_models=(),
            extractor_cache_root="", llm_config_path="/tmp/llm.json", pdf_root="",
        ),
        subscription_token="codex-tok",
    )

    adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert FakeClient.last_config.kwargs["subscription_token"] == "codex-tok"


def test_factory_forwards_subscription_token(monkeypatch):
    import tradingagents.dataflows.financial_reports.adapter as adapter_module

    monkeypatch.setattr(
        adapter_module, "get_report_collector_config",
        lambda: {"enabled": False}, raising=False,
    )
    cfg = FinancialReportClientConfig(
        enabled=True, cache_only=True, force_refresh=False,
        include_llm_supplement=True, allow_llm_models=(),
        extractor_cache_root="", llm_config_path="/explicit.json", pdf_root="",
    )

    adapter = adapter_module.create_financial_report_adapter(cfg, subscription_token="tok")

    assert adapter.subscription_token == "tok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_financial_report_adapter.py -k "subscription_token" -v`
Expected: FAIL — `FinancialReportAdapter.__init__` / `create_financial_report_adapter` don't accept `subscription_token` (TypeError) or `last_config.kwargs` lacks the key.

- [ ] **Step 3: Implement** — three edits in `tradingagents/dataflows/financial_reports/adapter.py`:

(3a) `FinancialReportAdapter.__init__` — accept and store the token:

```python
    def __init__(
        self,
        config: FinancialReportClientConfig,
        report_collector: Any | None = None,
        subscription_token: str | None = None,
    ) -> None:
        self.config = config
        self.report_collector = report_collector
        self.subscription_token = subscription_token
```

(3b) `get_annual_report_data` — pass it into the `ExtractorConfig(...)` construction (~line 134):

```python
            extractor_config = ExtractorConfig(
                llm_config_path=_optional_path(self.config.llm_config_path),
                cache_root=_optional_path(self.config.extractor_cache_root),
                pdf_resolver=self.resolve_pdf if include_llm else None,
                subscription_token=self.subscription_token,
            )
```

(3c) `create_financial_report_adapter` — accept the token and pass it to BOTH `FinancialReportAdapter(...)` constructions:

```python
def create_financial_report_adapter(
    config: FinancialReportClientConfig,
    subscription_token: str | None = None,
) -> FinancialReportAdapter:
    """Create adapter with report-collector wired only as a PDF provider.

    When the LLM supplement is requested but no explicit FINANCIAL_REPORT_LLM_CONFIG_PATH
    was provided, materialize a transport-config JSON from TradingAgents-CN's bridged
    deep-role LLM env vars (provider/model/backend_url). The extractor is unchanged.

    subscription_token: per-request codex OAuth token (caller-resolved); forwarded to
    the extractor so codex subscriptions work without a local ~/.codex login.
    """
    if config.enabled and config.include_llm_supplement and not config.llm_config_path:
        generated = materialize_extractor_llm_config(cache_root=config.extractor_cache_root)
        if generated:
            config = replace(config, llm_config_path=generated)

    if not config.enabled or not (config.include_llm_supplement and config.llm_config_path):
        return FinancialReportAdapter(config=config, subscription_token=subscription_token)

    report_collector = None
    try:
        from tradingagents.services.report_collector_client import ReportCollectorClient
        from tradingagents.services.report_collector_config import get_report_collector_config

        rc_config = get_report_collector_config()
        if rc_config.get("enabled"):
            client = ReportCollectorClient(
                base_url=rc_config["url"],
                port=rc_config["port"],
                timeout=rc_config["timeout"],
            )
            report_collector = client if client.is_available() else None
    except Exception:
        report_collector = None
    return FinancialReportAdapter(
        config=config,
        report_collector=report_collector,
        subscription_token=subscription_token,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/unit/test_financial_report_adapter.py -v`
Expected: PASS (all existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/financial_reports/adapter.py tests/unit/test_financial_report_adapter.py
git commit -m "feat(financial-reports): adapter forwards subscription token to extractor"
```

## Task 4: Resolve the per-request codex token and wire the fundamentals call site

**Files:**
- Modify: `tradingagents/dataflows/financial_reports/adapter.py` (add `import os`; add `resolve_injected_codex_token`)
- Modify: `tradingagents/agents/utils/agent_utils.py` (`_try_financial_report_client_section` ~line 855-883)
- Test: `tests/unit/test_financial_report_adapter.py`

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_financial_report_adapter.py`)

```python
def test_resolve_injected_codex_token_only_for_codex(monkeypatch):
    from tradingagents.dataflows.financial_reports.adapter import resolve_injected_codex_token

    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "codex")
    assert resolve_injected_codex_token({"deep_api_key": "tok"}) == "tok"

    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "deepseek")
    assert resolve_injected_codex_token({"deep_api_key": "tok"}) is None

    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "codex")
    assert resolve_injected_codex_token({}) is None

    monkeypatch.delenv("TRADINGAGENTS_DEEP_PROVIDER", raising=False)
    assert resolve_injected_codex_token({"deep_api_key": "tok"}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_financial_report_adapter.py::test_resolve_injected_codex_token_only_for_codex -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_injected_codex_token'`.

- [ ] **Step 3a: Add the helper to `adapter.py`** — add `import os` to the import block at the top (it currently imports `dataclass`/`datetime`/`Path`/`Any` but NOT `os`), then add the module-level function:

```python
def resolve_injected_codex_token(deep_config: dict) -> str | None:
    """Per-request codex OAuth token to hand the extractor.

    analysis_service injects the user's OAuth token into the analysis config as
    ``deep_api_key`` for subscription providers. Return it ONLY when the bridged
    deep provider is codex, so non-codex runs pass None. The token is never written
    to disk or an env var — it travels as a call argument.
    """
    if os.getenv("TRADINGAGENTS_DEEP_PROVIDER", "").strip().lower() != "codex":
        return None
    token = (deep_config or {}).get("deep_api_key")
    return token or None
```

- [ ] **Step 3b: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_financial_report_adapter.py::test_resolve_injected_codex_token_only_for_codex -v`
Expected: PASS.

- [ ] **Step 3c: Wire the call site in `agent_utils.py`** — in `_try_financial_report_client_section`, extend the import block (currently `from tradingagents.dataflows.financial_reports import (FinancialReportPolicy, create_financial_report_adapter, format_annual_report_section, get_financial_report_client_config,)`) to also import `resolve_injected_codex_token`. Then change the adapter construction (currently `adapter = create_financial_report_adapter(frc_config)`, ~line 883) to:

```python
                    subscription_token = resolve_injected_codex_token(Toolkit._config)
                    adapter = create_financial_report_adapter(
                        frc_config, subscription_token=subscription_token
                    )
```

- [ ] **Step 4: Verify nothing broke**

Run: `.venv/bin/pytest tests/unit/test_financial_report_adapter.py tests/unit/test_financial_report_llm_export.py tests/unit/test_config_bridge_deep_role.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/financial_reports/adapter.py tradingagents/agents/utils/agent_utils.py tests/unit/test_financial_report_adapter.py
git commit -m "feat(financial-reports): inject per-request codex token at fundamentals call site"
```

## Task 5: Docs + runtime install + full verification

**Files:**
- Modify: `docs/LLM_CONFIG_AND_EXTRACTOR_INTEGRATION.md`

- [ ] **Step 1: Update the integration doc** — in the section describing the Codex/OAuth gap (方案 B 的 codex 部分 + 第三部分补充), change the wording from "codex 凭据 OS 级共享 / 仅本地 ~/.codex" to reflect the new behavior. Add this note under the codex bullets:

```
> 更新 (2026-05-28)：codex token 现由 TA-CN 在请求时解析（oauth_service，按用户）并经
> `create_financial_report_adapter(..., subscription_token=...)` → `ExtractorConfig.subscription_token`
> 注入 extractor，**无需**容器内有本地 `~/.codex` 登录。token 仅程序传参，不落盘/不进环境变量/不进缓存。
> ⚠️ 并发限制：调用点从类级全局 `Toolkit._config` 读 `deep_api_key`，并发多用户 codex 分析下可能串 token；
> 本特性 opt-in、工具定位单次运行，多用户并发 codex 场景请谨慎启用。
```

- [ ] **Step 2: Reinstall the extractor into TradingAgents-CN's venv** so the new `ExtractorConfig.subscription_token` field exists at runtime (TA-CN unit tests use a fake extractor and don't need this, but runtime does):

Run: `.venv/bin/pip install -e /Users/like/source/financial-report-llm-extractor`
Then verify the field is importable:
Run: `.venv/bin/python -c "from financial_report_llm_extractor.client import ExtractorConfig; print('subscription_token' in ExtractorConfig().__dict__)"`
Expected: prints `True`.

- [ ] **Step 3: Full verification — TradingAgents-CN feature suite**

Run: `.venv/bin/pytest tests/unit/test_financial_report_config.py tests/unit/test_financial_report_adapter.py tests/unit/test_financial_report_llm_export.py tests/unit/test_config_bridge_deep_role.py -q`
Expected: all pass.

- [ ] **Step 4: Lint the changed Python files**

Run: `.venv/bin/ruff check tradingagents/dataflows/financial_reports/adapter.py`
Expected: no NEW findings (pre-existing findings elsewhere are fine; this file should be clean).

- [ ] **Step 5: Commit**

```bash
git add docs/LLM_CONFIG_AND_EXTRACTOR_INTEGRATION.md
git commit -m "docs(financial-reports): codex token now injected; document concurrency caveat"
```

---

## Self-Review (completed at authoring)

- **Spec coverage:** ✅ injection mechanism = Option A field (Task 2); precedence/fallback in `CodexResponsesClient` (Task 1); standalone preserved (Task 1 fallback regression + Task 2 full-suite); TA-CN adapter propagation (Task 3); per-request token resolved + gated on codex (Task 4); concurrency limitation documented (Task 5); token off disk/env/cache/logs (no token written to JSON or env in any task; not logged). claude_code & ② explicitly out of scope.
- **Placeholder scan:** ✅ every code step has full code; no TBD/"handle errors".
- **Type/name consistency:** ✅ `subscription_token: str | None` and keyword name identical across `create_llm_client`, `CodexResponsesClient`, `ExtractorConfig`, `run_pipeline`, `run_company_evaluation`, `_run_llm_supplement_step`, `create_financial_report_adapter`, `FinancialReportAdapter.__init__`, `resolve_injected_codex_token`. `TRADINGAGENTS_DEEP_PROVIDER` matches the bridge env from PR #13.
- **Cross-repo order:** Extractor (Part 1) must land + be reinstalled (Task 5 Step 2) before TA-CN runtime works; TA-CN unit tests use the fake extractor so they pass independently of the install.
- **Known weak spot:** Task 2's forwarding is guarded by a signature-contract test (not value-passing), accepted because each forwarding edit is a single shown line and the token-usage behavior is fully covered by Task 1. Reviewers should confirm the four one-line forwards in code.
