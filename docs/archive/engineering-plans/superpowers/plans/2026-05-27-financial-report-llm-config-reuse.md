# Financial-Report Extractor LLM Config Reuse — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the in-process `financial-report-llm-extractor` LLM-supplement step reuse TradingAgents-CN's **deep-role** LLM config (provider/model/backend_url + shared `{PROVIDER}_API_KEY`) instead of requiring a separate hand-written `FINANCIAL_REPORT_LLM_CONFIG_PATH` JSON.

**Architecture:** Keep the established one-way bridge. The **proprietary `app/`** side resolves the deep model → provider/backend_url (DB-aware, via the existing `get_provider_and_url_by_model_sync`) and projects them into two new env vars. The **Apache `tradingagents/`** side reads only those env vars, materializes the extractor's transport-config JSON on demand, and feeds it through the existing `ExtractorConfig.llm_config_path`. The extractor itself is **not modified**. API keys are never written to the JSON — the extractor reads them at call time from `{PROVIDER}_API_KEY`, which the bridge already sets. Codex (OAuth) is special-cased: only `{provider, model}` is written; the extractor resolves the token itself from `~/.codex/auth.json`.

**Tech Stack:** Python 3.10+, pytest, frozen dataclasses, stdlib `json`/`os`/`tempfile`. No new dependencies. The extractor is consumed as an installed library (`financial_report_llm_extractor`).

**License note:** All `app/` edits are proprietary; all `tradingagents/` edits are Apache 2.0. The model→provider resolver stays in `app/` (it needs DB access); `tradingagents/` only reads env. Do not import `app.*` from `tradingagents.*`.

**Out of scope:** quick-role reuse (deep only, per decision); adding domestic providers to the extractor's `PROVIDER_DEFAULTS` (unnecessary — generated JSON carries `base_url`+`api_key_env`); plain `anthropic` API-key provider (extractor has no api-key Anthropic client — defensively degraded to "no supplement").

---

## File Structure

| File | License | Responsibility | Action |
|---|---|---|---|
| `tradingagents/dataflows/financial_reports/llm_config_export.py` | Apache | Read bridged deep-role env → write extractor transport JSON; branch codex vs api-key; degrade unsupported | **Create** |
| `tests/unit/test_financial_report_llm_export.py` | Apache | Unit tests for the above | **Create** |
| `app/core/config_bridge.py` | Proprietary | New helper `bridge_deep_llm_role_to_env()` + wire into `bridge_config_to_env()` | **Modify** |
| `tests/unit/test_config_bridge_deep_role.py` | — | Unit test for the bridge helper (resolver injected, no DB) | **Create** |
| `tradingagents/dataflows/financial_reports/adapter.py` | Apache | `create_financial_report_adapter` falls back to materialized JSON when `include_llm_supplement` and no explicit path | **Modify** |
| `tests/unit/test_financial_report_adapter.py` | Apache | Add cases for the new fallback | **Modify** |
| `.env.example`, `.env.docker` | — | Document `FINANCIAL_REPORT_LLM_CONFIG_PATH` is now optional + Codex `~/.codex` mount note | **Modify** |
| `docs/LLM_CONFIG_AND_EXTRACTOR_INTEGRATION.md` | — | Flip "落地清单" to "已实现" with file refs | **Modify** |

**Env-var contract (the seam):**
- `TRADINGAGENTS_DEEP_MODEL` — already set by bridge (`config_bridge.py:162`).
- `TRADINGAGENTS_DEEP_PROVIDER` — **new**, set by bridge.
- `TRADINGAGENTS_DEEP_BACKEND_URL` — **new**, set by bridge.
- `{PROVIDER}_API_KEY` — already set by bridge (`config_bridge.py:102/129`).

---

## Task 1: `materialize_extractor_llm_config` (Apache core, pure env→JSON)

**Files:**
- Create: `tradingagents/dataflows/financial_reports/llm_config_export.py`
- Test: `tests/unit/test_financial_report_llm_export.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_financial_report_llm_export.py
import json
from pathlib import Path

from tradingagents.dataflows.financial_reports.llm_config_export import (
    materialize_extractor_llm_config,
)


def _clear_env(monkeypatch):
    for k in (
        "TRADINGAGENTS_DEEP_PROVIDER",
        "TRADINGAGENTS_DEEP_BACKEND_URL",
        "TRADINGAGENTS_DEEP_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)


def test_returns_none_when_provider_or_model_missing(monkeypatch):
    _clear_env(monkeypatch)
    assert materialize_extractor_llm_config() is None
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "deepseek")
    # model still missing
    assert materialize_extractor_llm_config() is None


def test_api_key_provider_writes_full_config(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "deepseek")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "deepseek-chat")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_BACKEND_URL", "https://api.deepseek.com")

    path = materialize_extractor_llm_config(cache_root=str(tmp_path))

    assert path == str(tmp_path / "frle_llm_config.deep.json")
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    assert cfg == {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
    }


def test_api_key_provider_omits_empty_base_url(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "deepseek")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "deepseek-chat")
    # no backend url

    cfg = json.loads(Path(materialize_extractor_llm_config(cache_root=str(tmp_path))).read_text("utf-8"))
    assert "base_url" not in cfg
    assert cfg["api_key_env"] == "DEEPSEEK_API_KEY"


def test_codex_writes_provider_and_model_only(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "codex")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "gpt-5.5-codex")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_BACKEND_URL", "ignored")

    cfg = json.loads(Path(materialize_extractor_llm_config(cache_root=str(tmp_path))).read_text("utf-8"))
    assert cfg == {"provider": "codex", "model": "gpt-5.5-codex"}


def test_unsupported_provider_degrades_to_none(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TRADINGAGENTS_DEEP_PROVIDER", "anthropic")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_MODEL", "claude-3-5-sonnet")
    assert materialize_extractor_llm_config(cache_root=str(tmp_path)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_financial_report_llm_export.py -v`
Expected: FAIL — `ModuleNotFoundError: ...llm_config_export`.

- [ ] **Step 3: Write the implementation**

```python
# tradingagents/dataflows/financial_reports/llm_config_export.py
"""Materialize a financial-report-llm-extractor transport-config JSON from
TradingAgents-CN's bridged deep-role LLM env vars.

The extractor reads its LLM config from a JSON file (LlmTransportConfig.from_json).
Instead of a separate hand-written file, we generate one from the deep-role
provider/model/backend_url that app.core.config_bridge has already projected into
the environment. The API key itself is NOT written here — the extractor reads it
at call time from {PROVIDER}_API_KEY, which the bridge also sets.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tradingagents.utils.logging_manager import get_logger

logger = get_logger("financial_reports.llm_config_export")

# Codex 走 OAuth：token 由 extractor 自行从 ~/.codex/auth.json 解析，不写 api_key_env
_SUBSCRIPTION_PROVIDERS = {"codex", "openai-codex"}
# extractor 无 api-key 版 Anthropic 客户端（仅诊断态 OAuth）；遇到则降级，不生成破损配置
_UNSUPPORTED_PROVIDERS = {"anthropic", "claude_code", "claude-code"}

_CONFIG_FILENAME = "frle_llm_config.deep.json"


def materialize_extractor_llm_config(cache_root: str = "") -> str | None:
    """Write a transport-config JSON for the extractor's LLM-supplement step.

    Reads TRADINGAGENTS_DEEP_PROVIDER / _BACKEND_URL / TRADINGAGENTS_DEEP_MODEL
    (set by app.core.config_bridge). Returns the JSON file path, or None when the
    deep-role config is unavailable/unsupported (caller then skips the supplement).
    """
    provider = (os.getenv("TRADINGAGENTS_DEEP_PROVIDER") or "").strip()
    model = (os.getenv("TRADINGAGENTS_DEEP_MODEL") or "").strip()
    if not provider or not model:
        logger.info("⏭️ 未找到 deep provider/model 环境变量，跳过为 extractor 生成 LLM 配置")
        return None

    if provider.lower() in _UNSUPPORTED_PROVIDERS:
        logger.warning(
            f"⚠️ extractor 不支持 provider={provider} 的 api-key 调用，跳过 LLM 补充配置生成"
        )
        return None

    backend_url = (os.getenv("TRADINGAGENTS_DEEP_BACKEND_URL") or "").strip()

    if provider.lower() in _SUBSCRIPTION_PROVIDERS:
        cfg: dict[str, str] = {"provider": "codex", "model": model}
    else:
        cfg = {
            "provider": provider,
            "model": model,
            "api_key_env": f"{provider.upper()}_API_KEY",
        }
        if backend_url:
            cfg["base_url"] = backend_url

    target_dir = Path(cache_root) if cache_root else Path(tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _CONFIG_FILENAME
    target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"✅ 已为 extractor 生成 LLM 配置: provider={provider}, model={model} → {target}")
    return str(target)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_financial_report_llm_export.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/financial_reports/llm_config_export.py tests/unit/test_financial_report_llm_export.py
git commit -m "feat(financial-reports): materialize extractor LLM config from deep-role env"
```

---

## Task 2: Bridge deep provider/backend_url into env

**Files:**
- Modify: `app/core/config_bridge.py` (add helper after imports ~line 13; call it after `deep_model` block ~line 164)
- Test: `tests/unit/test_config_bridge_deep_role.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_config_bridge_deep_role.py
import os

from app.core.config_bridge import bridge_deep_llm_role_to_env


def _clear(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_DEEP_PROVIDER", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_DEEP_BACKEND_URL", raising=False)


def test_empty_model_sets_nothing(monkeypatch):
    _clear(monkeypatch)
    assert bridge_deep_llm_role_to_env("", resolver=lambda m: {}) == {}
    assert "TRADINGAGENTS_DEEP_PROVIDER" not in os.environ


def test_sets_provider_and_backend_url(monkeypatch):
    _clear(monkeypatch)
    resolver = lambda m: {
        "provider": "dashscope",
        "backend_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "k",
    }
    result = bridge_deep_llm_role_to_env("qwen-max", resolver=resolver)
    assert result == {
        "TRADINGAGENTS_DEEP_PROVIDER": "dashscope",
        "TRADINGAGENTS_DEEP_BACKEND_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    assert os.environ["TRADINGAGENTS_DEEP_PROVIDER"] == "dashscope"
    assert os.environ["TRADINGAGENTS_DEEP_BACKEND_URL"].endswith("/compatible-mode/v1")


def test_codex_sets_provider_without_backend_url(monkeypatch):
    _clear(monkeypatch)
    resolver = lambda m: {"provider": "codex", "backend_url": "", "api_key": None}
    result = bridge_deep_llm_role_to_env("gpt-5.5-codex", resolver=resolver)
    assert result == {"TRADINGAGENTS_DEEP_PROVIDER": "codex"}
    assert "TRADINGAGENTS_DEEP_BACKEND_URL" not in os.environ
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_config_bridge_deep_role.py -v`
Expected: FAIL — `ImportError: cannot import name 'bridge_deep_llm_role_to_env'`.

- [ ] **Step 3: Add the helper to `app/core/config_bridge.py`**

Insert after `logger = logging.getLogger("app.config_bridge")` (after line 12):

```python


def bridge_deep_llm_role_to_env(deep_model: str, resolver=None) -> dict:
    """将 deep 角色模型对应的 provider / backend_url 桥接到环境变量。

    供 financial-report-llm-extractor 适配器（Apache 核心，仅读环境变量）复用本项目
    的深度分析 LLM 配置。API Key 不在此设置——已由 {PROVIDER}_API_KEY 桥接负责。

    Args:
        deep_model: 深度分析模型名（来自 unified_config.get_deep_analysis_model）。
        resolver: 可注入的解析函数（测试用）；默认用 simple_analysis_service 的同步解析器。

    Returns:
        实际写入的环境变量字典（便于计数与测试）。
    """
    if not deep_model:
        return {}
    if resolver is None:
        from app.services.simple_analysis_service import get_provider_and_url_by_model_sync
        resolver = get_provider_and_url_by_model_sync

    info = resolver(deep_model) or {}
    written: dict = {}

    provider = (info.get("provider") or "").strip()
    if provider:
        os.environ["TRADINGAGENTS_DEEP_PROVIDER"] = provider
        written["TRADINGAGENTS_DEEP_PROVIDER"] = provider

    backend_url = (info.get("backend_url") or "").strip()
    if backend_url:
        os.environ["TRADINGAGENTS_DEEP_BACKEND_URL"] = backend_url
        written["TRADINGAGENTS_DEEP_BACKEND_URL"] = backend_url

    return written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_config_bridge_deep_role.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Wire the helper into `bridge_config_to_env()`**

In `app/core/config_bridge.py`, immediately after the `deep_model` block (the lines that set `TRADINGAGENTS_DEEP_MODEL`, ending ~line 164), add:

```python
        # 3b. 桥接 deep 角色的 provider / backend_url（供 financial-report-llm-extractor 复用）
        if deep_model:
            try:
                bridged_count += len(bridge_deep_llm_role_to_env(deep_model))
            except Exception as e:
                logger.warning(f"⚠️ 桥接 deep provider/backend_url 失败: {e}")
```

- [ ] **Step 6: Verify nothing else broke + commit**

Run: `pytest tests/unit/test_config_bridge_deep_role.py tests/unit/test_financial_report_llm_export.py -v`
Expected: PASS (8 passed).

```bash
git add app/core/config_bridge.py tests/unit/test_config_bridge_deep_role.py
git commit -m "feat(config-bridge): project deep-role provider/backend_url to env"
```

---

## Task 3: Adapter falls back to materialized config

**Files:**
- Modify: `tradingagents/dataflows/financial_reports/adapter.py` (imports near top; `create_financial_report_adapter` at lines 185-205)
- Test: `tests/unit/test_financial_report_adapter.py` (append cases)

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_financial_report_adapter.py`)

```python
def test_factory_materializes_config_when_path_missing(monkeypatch):
    from tradingagents.dataflows.financial_reports import adapter as adapter_module
    from tradingagents.dataflows.financial_reports.config import FinancialReportClientConfig

    monkeypatch.setattr(
        adapter_module,
        "materialize_extractor_llm_config",
        lambda cache_root="": "/tmp/generated-llm.json",
    )
    config = FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=True,
        allow_llm_models=(),
        extractor_cache_root="/tmp/cache",
        llm_config_path="",  # not explicitly provided → should be generated
        pdf_root="",
    )

    result = adapter_module.create_financial_report_adapter(config)
    assert result.config.llm_config_path == "/tmp/generated-llm.json"


def test_factory_keeps_explicit_path_over_materialized(monkeypatch):
    from tradingagents.dataflows.financial_reports import adapter as adapter_module
    from tradingagents.dataflows.financial_reports.config import FinancialReportClientConfig

    monkeypatch.setattr(
        adapter_module,
        "materialize_extractor_llm_config",
        lambda cache_root="": "/tmp/should-not-be-used.json",
    )
    config = FinancialReportClientConfig(
        enabled=True, cache_only=True, force_refresh=False,
        include_llm_supplement=True, allow_llm_models=(),
        extractor_cache_root="", llm_config_path="/explicit/llm.json", pdf_root="",
    )

    result = adapter_module.create_financial_report_adapter(config)
    assert result.config.llm_config_path == "/explicit/llm.json"


def test_factory_degrades_when_materialize_returns_none(monkeypatch):
    from tradingagents.dataflows.financial_reports import adapter as adapter_module
    from tradingagents.dataflows.financial_reports.config import FinancialReportClientConfig

    monkeypatch.setattr(
        adapter_module, "materialize_extractor_llm_config", lambda cache_root="": None
    )
    config = FinancialReportClientConfig(
        enabled=True, cache_only=True, force_refresh=False,
        include_llm_supplement=True, allow_llm_models=(),
        extractor_cache_root="", llm_config_path="", pdf_root="",
    )

    result = adapter_module.create_financial_report_adapter(config)
    assert result.config.llm_config_path == ""  # no crash; supplement effectively off
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_financial_report_adapter.py -k factory -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'materialize_extractor_llm_config'` (or assertion fail on llm_config_path).

- [ ] **Step 3: Edit `create_financial_report_adapter` in `adapter.py`**

At the top of the file, add to the imports block (after `from .config import FinancialReportClientConfig`):

```python
from dataclasses import replace

from .llm_config_export import materialize_extractor_llm_config
```

Replace the current function body (lines 185-205) so the early-return guard is preceded by a materialization fallback:

```python
def create_financial_report_adapter(config: FinancialReportClientConfig) -> FinancialReportAdapter:
    """Create adapter with report-collector wired only as a PDF provider.

    When the LLM supplement is requested but no explicit FINANCIAL_REPORT_LLM_CONFIG_PATH
    was provided, materialize a transport-config JSON from TradingAgents-CN's bridged
    deep-role LLM env vars (provider/model/backend_url). The extractor is unchanged.
    """
    if config.enabled and config.include_llm_supplement and not config.llm_config_path:
        generated = materialize_extractor_llm_config(cache_root=config.extractor_cache_root)
        if generated:
            config = replace(config, llm_config_path=generated)

    if not config.enabled or not (config.include_llm_supplement and config.llm_config_path):
        return FinancialReportAdapter(config=config)

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
    return FinancialReportAdapter(config=config, report_collector=report_collector)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_financial_report_adapter.py -v`
Expected: PASS (existing cases + 3 new factory cases all pass).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/financial_reports/adapter.py tests/unit/test_financial_report_adapter.py
git commit -m "feat(financial-reports): auto-generate extractor LLM config when path unset"
```

---

## Task 4: Docs + env templates + full verification

**Files:**
- Modify: `.env.example` (around lines 595-609), `.env.docker` (around 392-404)
- Modify: `docs/LLM_CONFIG_AND_EXTRACTOR_INTEGRATION.md`

- [ ] **Step 1: Mark `FINANCIAL_REPORT_LLM_CONFIG_PATH` optional in `.env.example`**

Find the line `# FINANCIAL_REPORT_LLM_CONFIG_PATH=/app/external/financial-report-llm-extractor/llm_config_deepseek.json`
and add **above** it:

```
# 【可选】不填则自动复用本项目"深度分析模型"的 provider/model/backend_url 生成 LLM 配置；
#   API Key 走已桥接的 {PROVIDER}_API_KEY。仅当要让 extractor 用一份独立 LLM 配置时才显式设置。
# 【Codex 订阅】容器内跑 codex 需另把宿主机 ~/.codex 挂进容器或设 CODEX_HOME（凭据非本项目配置）。
```

- [ ] **Step 2: Apply the same two comment lines to `.env.docker`**

Find the matching commented `# FINANCIAL_REPORT_LLM_CONFIG_PATH=...` line in `.env.docker` and add the same two `# ...` lines above it.

- [ ] **Step 3: Flip the integration doc's status**

In `docs/LLM_CONFIG_AND_EXTRACTOR_INTEGRATION.md`, in "第五部分", change the TradingAgents-CN 侧 numbered list header to note it is **已实现 (2026-05-27)** and append file refs:
`tradingagents/dataflows/financial_reports/llm_config_export.py`、`app/core/config_bridge.py::bridge_deep_llm_role_to_env`、`adapter.py::create_financial_report_adapter`.

- [ ] **Step 4: Full verification — run the whole financial-report + bridge unit subset**

Run: `pytest tests/unit/test_financial_report_config.py tests/unit/test_financial_report_adapter.py tests/unit/test_financial_report_llm_export.py tests/unit/test_config_bridge_deep_role.py -v`
Expected: ALL PASS. (Confirms the new fields didn't disturb the existing config/adapter tests.)

- [ ] **Step 5: Lint the changed files**

Run: `ruff check tradingagents/dataflows/financial_reports/llm_config_export.py app/core/config_bridge.py tradingagents/dataflows/financial_reports/adapter.py`
Expected: no errors (or only pre-existing, unrelated warnings — do not fix unrelated lines).

- [ ] **Step 6: Commit**

```bash
git add .env.example .env.docker docs/LLM_CONFIG_AND_EXTRACTOR_INTEGRATION.md
git commit -m "docs(financial-reports): make extractor LLM config path optional; document codex creds"
```

---

## Self-Review (completed at authoring)

- **Spec coverage:** ✅ deep-role reuse (Task 2/3), openai-compatible + deepseek (Task 1 api-key branch), codex subscription (Task 1 codex branch + Task 4 creds note), extractor unchanged (no extractor edits), env-var seam (Task 2 contract). Unsupported `anthropic` degraded (Task 1).
- **Placeholder scan:** ✅ no TBD/"handle edge cases" — every code step has full code.
- **Type/name consistency:** ✅ `materialize_extractor_llm_config(cache_root="")→str|None`, `bridge_deep_llm_role_to_env(deep_model, resolver=None)→dict`, env names `TRADINGAGENTS_DEEP_PROVIDER`/`_BACKEND_URL`/`_DEEP_MODEL`, `{PROVIDER}_API_KEY` — used identically across tasks. `FinancialReportClientConfig` field list matches the existing dataclass (no new fields → existing `test_financial_report_config.py` stays green).
- **Open risk:** the bridge helper's real resolver (`get_provider_and_url_by_model_sync`) needs MongoDB; this is exercised only at runtime, not in unit tests (tests inject a fake resolver). Acceptable — the resolver is pre-existing and tested behavior.
