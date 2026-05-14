# FinancialReportClient Adapter Phase 1b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate `financial-report-llm-extractor` Phase 1a `FinancialReportClient` into TradingAgents-CN as the authoritative annual-report field source for fundamentals output and value-investment calculations.

**Architecture:** Add an isolated `tradingagents/dataflows/financial_reports/` package with adapter, policy, mapper, and formatter modules. Existing agent tools call this package through narrow helper functions; extractor internals remain invisible, report-collector remains a PDF provider only, and missing extractor installation degrades to warnings without breaking current workflows.

**Tech Stack:** Python 3.11+, dataclasses, `Decimal`, pytest unit tests with fake extractor dataclasses, optional in-process import of `financial_report_llm_extractor.client`.

---

## Spec Inputs

- TradingAgents-CN spec: `docs/superpowers/specs/2026-05-14-financial-report-client-adapter-design.md`
- Upstream extractor spec: `/home/like/git/financial-report-llm-extractor/docs/superpowers/specs/2026-05-13-financial-report-client-productization-design.md`
- Prior cleanup commit: `2df57bf feat: keep report collector as pdf provider`

## Locked Decisions

- `period_end` is optional at TradingAgents call sites. If omitted, use the latest likely annual period end: if reference date is May or later, use previous year `12-31`; otherwise use the year before previous `12-31`.
- PDF resolver checks configured local directories first, then asks report-collector to download/select the latest PDF and returns its path. It never calls report-collector content extraction.
- Stale extraction results are display-only by default. They do not override calculation inputs unless a future explicit config flag is added.
- LLM supplement fetching is off by default. If `FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT=true` and `FINANCIAL_REPORT_LLM_CONFIG_PATH` is set, LLM supplement fields are display-only by default except for allow-listed Codex/GPT-style models configured through `FINANCIAL_REPORT_ALLOW_LLM_MODELS`.
- TradingAgents-CN passes `FINANCIAL_REPORT_LLM_CONFIG_PATH` to extractor but never reads API keys or parses extractor LLM config schema.

## File Structure

Create:

- `tradingagents/dataflows/financial_reports/__init__.py`  
  Public exports for the local adapter package.
- `tradingagents/dataflows/financial_reports/config.py`  
  Lightweight env/default config parsing. No extractor import.
- `tradingagents/dataflows/financial_reports/policy.py`  
  Field reliability and LLM supplement compute policy.
- `tradingagents/dataflows/financial_reports/mapper.py`  
  Turtle `ExtractionResult.fields` to TradingAgents `financial_data` dict merge.
- `tradingagents/dataflows/financial_reports/formatter.py`  
  User-facing annual-report authority section and value report source note.
- `tradingagents/dataflows/financial_reports/adapter.py`  
  Lazy extractor import, `FinancialReportClient` construction, staleness/error conversion, PDF resolver.
- `tradingagents/dataflows/financial_reports/integration.py`  
  Lightweight application helpers used by heavy tool modules. This keeps tests from importing LangChain and full dataflow dependencies.

Modify:

- `pyproject.toml`  
  Align runtime requirement to Python 3.11+ for in-process extractor import.
- `tradingagents/default_config.py`  
  Add `financial_report_*` defaults.
- `tradingagents/tools/value_investment_tool.py`  
  Apply Turtle annual-report merge after AKShare structured financial fetch and before calculations. Append source/caveat section.
- `tradingagents/agents/utils/agent_utils.py`  
  Insert annual-report authority section into `get_stock_fundamentals_unified` without disrupting existing data sources.

Test:

- `tests/unit/test_financial_report_config.py`
- `tests/unit/test_financial_report_policy.py`
- `tests/unit/test_financial_report_mapper.py`
- `tests/unit/test_financial_report_formatter.py`
- `tests/unit/test_financial_report_adapter.py`
- `tests/unit/test_financial_report_integration.py`
- `tests/unit/test_financial_report_value_integration.py`
- `tests/unit/test_financial_report_fundamentals_integration.py`

---

### Task 1: Runtime Config and Python Version Gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `tradingagents/default_config.py`
- Create: `tradingagents/dataflows/financial_reports/__init__.py`
- Create: `tradingagents/dataflows/financial_reports/config.py`
- Test: `tests/unit/test_financial_report_config.py`

- [ ] **Step 1: Write the failing config tests**

Create `tests/unit/test_financial_report_config.py`:

```python
from tradingagents.dataflows.financial_reports.config import (
    FinancialReportClientConfig,
    get_financial_report_client_config,
)
from tradingagents.default_config import DEFAULT_CONFIG


def test_default_config_disables_financial_report_client():
    assert DEFAULT_CONFIG["financial_report_client_enabled"] is False
    assert DEFAULT_CONFIG["financial_report_cache_only"] is True
    assert DEFAULT_CONFIG["financial_report_force_refresh"] is False
    assert DEFAULT_CONFIG["financial_report_include_llm_supplement"] is False
    assert DEFAULT_CONFIG["financial_report_allow_llm_models"] == "gpt-5.5,codex"
    assert DEFAULT_CONFIG["financial_report_llm_config_path"] == ""


def test_env_config_parses_booleans_and_paths(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "true")
    monkeypatch.setenv("FINANCIAL_REPORT_CACHE_ONLY", "false")
    monkeypatch.setenv("FINANCIAL_REPORT_FORCE_REFRESH", "true")
    monkeypatch.setenv("FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT", "true")
    monkeypatch.setenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "gpt-5.5,codex,gpt-4.1")
    monkeypatch.setenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", "/tmp/fr-cache")
    monkeypatch.setenv("FINANCIAL_REPORT_LLM_CONFIG_PATH", "/tmp/extractor-llm.json")
    monkeypatch.setenv("FINANCIAL_REPORT_PDF_ROOT", "/tmp/reports")

    config = get_financial_report_client_config()

    assert config == FinancialReportClientConfig(
        enabled=True,
        cache_only=False,
        force_refresh=True,
        include_llm_supplement=True,
        allow_llm_models=("gpt-5.5", "codex", "gpt-4.1"),
        extractor_cache_root="/tmp/fr-cache",
        llm_config_path="/tmp/extractor-llm.json",
        pdf_root="/tmp/reports",
    )


def test_env_config_ignores_empty_llm_model_entries(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", " codex, , gpt-5.5 ,,")

    config = get_financial_report_client_config()

    assert config.allow_llm_models == ("codex", "gpt-5.5")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/unit/test_financial_report_config.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.dataflows.financial_reports'`.

- [ ] **Step 3: Add the local package and config implementation**

Create `tradingagents/dataflows/financial_reports/__init__.py`:

```python
"""Financial report client adapter package."""

from .config import FinancialReportClientConfig, get_financial_report_client_config

__all__ = [
    "FinancialReportClientConfig",
    "get_financial_report_client_config",
]
```

Create `tradingagents/dataflows/financial_reports/config.py`:

```python
"""Configuration for FinancialReportClient integration."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class FinancialReportClientConfig:
    enabled: bool
    cache_only: bool
    force_refresh: bool
    include_llm_supplement: bool
    allow_llm_models: tuple[str, ...]
    extractor_cache_root: str
    llm_config_path: str
    pdf_root: str


def get_financial_report_client_config() -> FinancialReportClientConfig:
    return FinancialReportClientConfig(
        enabled=_env_bool("FINANCIAL_REPORT_CLIENT_ENABLED", False),
        cache_only=_env_bool("FINANCIAL_REPORT_CACHE_ONLY", True),
        force_refresh=_env_bool("FINANCIAL_REPORT_FORCE_REFRESH", False),
        include_llm_supplement=_env_bool("FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT", False),
        allow_llm_models=_split_csv(os.getenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "gpt-5.5,codex")),
        extractor_cache_root=os.getenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", ""),
        llm_config_path=os.getenv("FINANCIAL_REPORT_LLM_CONFIG_PATH", ""),
        pdf_root=os.getenv("FINANCIAL_REPORT_PDF_ROOT", ""),
    )
```

Modify `tradingagents/default_config.py` inside `DEFAULT_CONFIG`:

```python
    "financial_report_client_enabled": os.getenv("FINANCIAL_REPORT_CLIENT_ENABLED", "false").lower() == "true",
    "financial_report_cache_only": os.getenv("FINANCIAL_REPORT_CACHE_ONLY", "true").lower() == "true",
    "financial_report_force_refresh": os.getenv("FINANCIAL_REPORT_FORCE_REFRESH", "false").lower() == "true",
    "financial_report_include_llm_supplement": os.getenv("FINANCIAL_REPORT_INCLUDE_LLM_SUPPLEMENT", "false").lower() == "true",
    "financial_report_allow_llm_models": os.getenv("FINANCIAL_REPORT_ALLOW_LLM_MODELS", "gpt-5.5,codex"),
    "financial_report_extractor_cache_root": os.getenv("FINANCIAL_REPORT_EXTRACTOR_CACHE_ROOT", ""),
    "financial_report_llm_config_path": os.getenv("FINANCIAL_REPORT_LLM_CONFIG_PATH", ""),
    "financial_report_pdf_root": os.getenv("FINANCIAL_REPORT_PDF_ROOT", ""),
```

Modify `pyproject.toml`:

```toml
requires-python = ">=3.11"
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/unit/test_financial_report_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tradingagents/default_config.py tradingagents/dataflows/financial_reports/__init__.py tradingagents/dataflows/financial_reports/config.py tests/unit/test_financial_report_config.py
git commit -m "feat: add financial report client config"
```

---

### Task 2: Field Reliability Policy

**Files:**
- Create: `tradingagents/dataflows/financial_reports/policy.py`
- Modify: `tradingagents/dataflows/financial_reports/__init__.py`
- Test: `tests/unit/test_financial_report_policy.py`

- [ ] **Step 1: Write the failing policy tests**

Create `tests/unit/test_financial_report_policy.py`:

```python
from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.financial_reports.policy import (
    FinancialReportPolicy,
    field_source_label,
)


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    confidence: object
    source: str | None = None
    raw_bucket: str | None = None
    is_reliable: bool = False
    is_present: bool = True


@dataclass(frozen=True)
class FakeResult:
    llm_provider: str | None
    llm_model: str | None


def test_reliable_field_can_compute():
    policy = FinancialReportPolicy(allow_llm_models=())
    field = FakeField(field_id="net_profit", value=Decimal("100"), source="akshare", is_reliable=True)

    decision = policy.decide(field=field, result=FakeResult(None, None))

    assert decision.can_compute is True
    assert decision.can_display is True
    assert decision.source_label == "financial-report-client"
    assert decision.caveat is None


def test_deepseek_llm_supplement_is_display_only_by_default():
    policy = FinancialReportPolicy(allow_llm_models=("codex", "gpt-5.5"))
    field = FakeField(
        field_id="operating_cash_flow",
        value=Decimal("88"),
        source="llm",
        is_reliable=False,
        is_present=True,
    )

    decision = policy.decide(field=field, result=FakeResult("deepseek", "deepseek-v3"))

    assert decision.can_compute is False
    assert decision.can_display is True
    assert decision.source_label == "financial-report-client:llm:deepseek-v3"
    assert decision.caveat == "LLM supplement from deepseek-v3 is display-only by policy"


def test_allowlisted_codex_llm_supplement_can_compute_with_caveat():
    policy = FinancialReportPolicy(allow_llm_models=("codex", "gpt-5.5"))
    field = FakeField(
        field_id="capital_expenditures",
        value=Decimal("9"),
        source="llm",
        is_reliable=False,
        is_present=True,
    )

    decision = policy.decide(field=field, result=FakeResult("openai", "codex-subscription"))

    assert decision.can_compute is True
    assert decision.can_display is True
    assert decision.source_label == "financial-report-client:llm:codex-subscription"
    assert decision.caveat == "LLM supplement from codex-subscription allowed by policy"


def test_unavailable_field_cannot_compute_or_display():
    policy = FinancialReportPolicy(allow_llm_models=("codex",))
    field = FakeField(
        field_id="gross_profit",
        value=None,
        source=None,
        raw_bucket="definition_unverified",
        is_reliable=False,
        is_present=False,
    )

    decision = policy.decide(field=field, result=FakeResult(None, None))

    assert decision.can_compute is False
    assert decision.can_display is False
    assert decision.source_label == "financial-report-client:unavailable"
    assert decision.caveat == "gross_profit unavailable: definition_unverified"


def test_field_source_label_uses_model_metadata_for_llm_source():
    label = field_source_label(
        field=FakeField(field_id="revenue", value=Decimal("1"), source="llm"),
        result=FakeResult("openai", "gpt-5.5"),
    )

    assert label == "financial-report-client:llm:gpt-5.5"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/unit/test_financial_report_policy.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.dataflows.financial_reports.policy'`.

- [ ] **Step 3: Implement policy module**

Create `tradingagents/dataflows/financial_reports/policy.py`:

```python
"""Reliability policy for FinancialReportClient fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldUseDecision:
    can_compute: bool
    can_display: bool
    source_label: str
    caveat: str | None = None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _model_name(result: Any) -> str:
    model = _text(getattr(result, "llm_model", ""))
    provider = _text(getattr(result, "llm_provider", ""))
    return model or provider or "unknown"


def _is_llm_field(field: Any) -> bool:
    source = _text(getattr(field, "source", "")).lower()
    confidence = _text(getattr(getattr(field, "confidence", None), "value", getattr(field, "confidence", ""))).lower()
    return source == "llm" or confidence == "llm_supplement"


def _is_model_allowed(model: str, allow_llm_models: tuple[str, ...]) -> bool:
    normalized = model.lower()
    return any(token.lower() in normalized for token in allow_llm_models)


def field_source_label(field: Any, result: Any) -> str:
    if _is_llm_field(field):
        return f"financial-report-client:llm:{_model_name(result)}"
    source = _text(getattr(field, "source", ""))
    if source in {"akshare", "yahoo", "derived"}:
        return "financial-report-client"
    if getattr(field, "value", None) is None:
        return "financial-report-client:unavailable"
    return "financial-report-client"


@dataclass(frozen=True)
class FinancialReportPolicy:
    allow_llm_models: tuple[str, ...]
    allow_stale_for_compute: bool = False

    def decide(self, *, field: Any, result: Any) -> FieldUseDecision:
        field_id = _text(getattr(field, "field_id", "unknown"))
        source_label = field_source_label(field, result)
        value = getattr(field, "value", None)
        is_present = bool(getattr(field, "is_present", value is not None))
        is_reliable = bool(getattr(field, "is_reliable", False))

        if value is None or not is_present:
            raw_bucket = _text(getattr(field, "raw_bucket", "")) or "missing"
            return FieldUseDecision(
                can_compute=False,
                can_display=False,
                source_label=source_label,
                caveat=f"{field_id} unavailable: {raw_bucket}",
            )

        if is_reliable:
            return FieldUseDecision(
                can_compute=True,
                can_display=True,
                source_label=source_label,
            )

        if _is_llm_field(field):
            model = _model_name(result)
            if _is_model_allowed(model, self.allow_llm_models):
                return FieldUseDecision(
                    can_compute=True,
                    can_display=True,
                    source_label=source_label,
                    caveat=f"LLM supplement from {model} allowed by policy",
                )
            return FieldUseDecision(
                can_compute=False,
                can_display=True,
                source_label=source_label,
                caveat=f"LLM supplement from {model} is display-only by policy",
            )

        raw_bucket = _text(getattr(field, "raw_bucket", "")) or "non-reliable"
        return FieldUseDecision(
            can_compute=False,
            can_display=True,
            source_label=source_label,
            caveat=f"{field_id} is display-only: {raw_bucket}",
        )
```

Modify `tradingagents/dataflows/financial_reports/__init__.py`:

```python
from .policy import FieldUseDecision, FinancialReportPolicy

__all__ = [
    "FieldUseDecision",
    "FinancialReportClientConfig",
    "FinancialReportPolicy",
    "get_financial_report_client_config",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/unit/test_financial_report_policy.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/financial_reports/__init__.py tradingagents/dataflows/financial_reports/policy.py tests/unit/test_financial_report_policy.py
git commit -m "feat: add financial report field policy"
```

---

### Task 3: Turtle Field Mapper

**Files:**
- Create: `tradingagents/dataflows/financial_reports/mapper.py`
- Modify: `tradingagents/dataflows/financial_reports/__init__.py`
- Test: `tests/unit/test_financial_report_mapper.py`

- [ ] **Step 1: Write the failing mapper tests**

Create `tests/unit/test_financial_report_mapper.py`:

```python
from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.financial_reports.mapper import (
    FinancialReportMergeResult,
    merge_financial_report_data,
)
from tradingagents.dataflows.financial_reports.policy import FinancialReportPolicy


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    currency: str | None = "CNY"
    unit: str | None = None
    confidence: object = "verified"
    source: str | None = "akshare"
    raw_bucket: str | None = "clean_present"
    evidence_page: int | None = None
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeResult:
    company: str = "600519"
    market: str = "CN"
    period_end: str = "2024-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    fields: dict[str, FakeField] | None = None


def test_reliable_fields_override_financial_data_and_compute_fcf():
    extraction = FakeResult(fields={
        "net_profit": FakeField("net_profit", Decimal("100")),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("130")),
        "capital_expenditures": FakeField("capital_expenditures", Decimal("30")),
        "total_equity": FakeField("total_equity", Decimal("800")),
        "current_assets": FakeField("current_assets", Decimal("500")),
        "current_liabilities": FakeField("current_liabilities", Decimal("250")),
        "total_assets": FakeField("total_assets", Decimal("1000")),
        "total_liabilities": FakeField("total_liabilities", Decimal("400")),
    })
    base = {
        "net_profits": [1.0],
        "operating_cash_flow": 1.0,
        "free_cash_flow": None,
        "financials_list": [{"net_profit": 1.0}],
        "_data_source": {"net_profits": "akshare"},
    }

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=()),
    )

    assert isinstance(merged, FinancialReportMergeResult)
    assert merged.financial_data["net_profits"][0] == 100.0
    assert merged.financial_data["operating_cash_flow"] == 130.0
    assert merged.financial_data["capex"] == 30.0
    assert merged.financial_data["free_cash_flow"] == 100.0
    assert merged.financial_data["current_ratio"] == 2.0
    assert merged.financial_data["debt_ratio"] == 0.4
    assert merged.financial_data["_data_source"]["net_profits"] == "financial-report-client"
    assert merged.financial_data["_data_source"]["free_cash_flow"] == "financial-report-client:derived"
    assert merged.caveats == []


def test_unavailable_field_does_not_override_existing_value():
    extraction = FakeResult(fields={
        "operating_cash_flow": FakeField(
            "operating_cash_flow",
            None,
            source=None,
            raw_bucket="source_unavailable",
            is_reliable=False,
            is_present=False,
        )
    })
    base = {"operating_cash_flow": 77.0, "_data_source": {"operating_cash_flow": "akshare"}}

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=()),
    )

    assert merged.financial_data["operating_cash_flow"] == 77.0
    assert merged.financial_data["_data_source"]["operating_cash_flow"] == "akshare"
    assert merged.details["operating_cash_flow"]["status"] == "not_used"
    assert merged.details["operating_cash_flow"]["caveat"] == "operating_cash_flow unavailable: source_unavailable"


def test_deepseek_llm_supplement_is_not_used_for_compute():
    extraction = FakeResult(
        llm_provider="deepseek",
        llm_model="deepseek-v3",
        fields={
            "capital_expenditures": FakeField(
                "capital_expenditures",
                Decimal("50"),
                source="llm",
                raw_bucket="llm_supplement_present",
                is_reliable=False,
            )
        },
    )
    base = {"capex": None}

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=("codex",)),
    )

    assert merged.financial_data["capex"] is None
    assert merged.details["capital_expenditures"]["status"] == "display_only"
    assert "display-only" in merged.details["capital_expenditures"]["caveat"]


def test_codex_llm_supplement_can_be_used_when_allowlisted():
    extraction = FakeResult(
        llm_provider="openai",
        llm_model="codex-subscription",
        fields={
            "interest_bearing_debt": FakeField(
                "interest_bearing_debt",
                Decimal("12"),
                source="llm",
                raw_bucket="llm_supplement_present",
                is_reliable=False,
            )
        },
    )
    base = {"interest_bearing_debt": None}

    merged = merge_financial_report_data(
        financial_data=base,
        extraction=extraction,
        policy=FinancialReportPolicy(allow_llm_models=("codex",)),
    )

    assert merged.financial_data["interest_bearing_debt"] == 12.0
    assert merged.financial_data["_data_source"]["interest_bearing_debt"] == "financial-report-client:llm:codex-subscription"
    assert "allowed by policy" in merged.caveats[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/unit/test_financial_report_mapper.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.dataflows.financial_reports.mapper'`.

- [ ] **Step 3: Implement mapper module**

Create `tradingagents/dataflows/financial_reports/mapper.py`:

```python
"""Map FinancialReportClient fields into TradingAgents financial_data."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .policy import FinancialReportPolicy


@dataclass(frozen=True)
class FinancialReportMergeResult:
    financial_data: dict[str, Any]
    details: dict[str, dict[str, Any]]
    caveats: list[str]


FIELD_TO_KEY = {
    "net_profit": "net_profits",
    "operating_cash_flow": "operating_cash_flow",
    "capital_expenditures": "capex",
    "total_equity": "total_equity",
    "cash_and_equivalents": "cash_and_equivalents",
    "money_cap": "cash_and_equivalents",
    "interest_bearing_debt": "interest_bearing_debt",
    "current_assets": "current_assets",
    "current_liabilities": "current_liabilities",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fields(extraction: Any) -> dict[str, Any]:
    fields = getattr(extraction, "fields", None)
    return fields if isinstance(fields, dict) else {}


def _set_net_profit(data: dict[str, Any], value: float) -> None:
    existing = data.get("net_profits")
    net_profits = list(existing) if isinstance(existing, list) else []
    if net_profits:
        net_profits[0] = value
    else:
        net_profits = [value]
    data["net_profits"] = net_profits

    financials_list = data.get("financials_list")
    if isinstance(financials_list, list) and financials_list:
        if isinstance(financials_list[0], dict):
            financials_list[0]["net_profit"] = value
    else:
        data["financials_list"] = [{"net_profit": value}]


def _write_value(data: dict[str, Any], key: str, value: float) -> None:
    if key == "net_profits":
        _set_net_profit(data, value)
    else:
        data[key] = value


def _record_detail(
    *,
    details: dict[str, dict[str, Any]],
    field_id: str,
    key: str,
    status: str,
    value: float | None,
    source_label: str,
    caveat: str | None,
) -> None:
    details[field_id] = {
        "target_key": key,
        "status": status,
        "value": value,
        "source": source_label,
        "caveat": caveat,
    }


def _derive_metrics(data: dict[str, Any], details: dict[str, dict[str, Any]]) -> None:
    data_source = data.setdefault("_data_source", {})

    operating_cash_flow = _to_float(data.get("operating_cash_flow"))
    capex = _to_float(data.get("capex"))
    if operating_cash_flow is not None and capex is not None:
        data["free_cash_flow"] = operating_cash_flow - abs(capex)
        data_source["free_cash_flow"] = "financial-report-client:derived"
        details["free_cash_flow"] = {
            "target_key": "free_cash_flow",
            "status": "derived",
            "value": data["free_cash_flow"],
            "source": "financial-report-client:derived",
            "caveat": None,
        }

    current_assets = _to_float(data.get("current_assets"))
    current_liabilities = _to_float(data.get("current_liabilities"))
    if current_assets is not None and current_liabilities and current_liabilities > 0:
        data["current_ratio"] = current_assets / current_liabilities
        data_source["current_ratio"] = "financial-report-client:derived"

    total_assets = _to_float(data.get("total_assets"))
    total_liabilities = _to_float(data.get("total_liabilities"))
    if total_assets is not None and total_assets > 0 and total_liabilities is not None:
        data["debt_ratio"] = total_liabilities / total_assets
        data_source["debt_ratio"] = "financial-report-client:derived"


def merge_financial_report_data(
    *,
    financial_data: dict[str, Any],
    extraction: Any,
    policy: FinancialReportPolicy,
) -> FinancialReportMergeResult:
    merged = dict(financial_data)
    merged["_data_source"] = dict(financial_data.get("_data_source") or {})
    merged["_supplemented_details"] = dict(financial_data.get("_supplemented_details") or {})
    details: dict[str, dict[str, Any]] = {}
    caveats: list[str] = []

    for field_id, key in FIELD_TO_KEY.items():
        field = _fields(extraction).get(field_id)
        if field is None:
            continue
        decision = policy.decide(field=field, result=extraction)
        value = _to_float(getattr(field, "value", None))

        if decision.caveat:
            caveats.append(decision.caveat)

        if decision.can_compute and value is not None:
            _write_value(merged, key, value)
            merged["_data_source"][key] = decision.source_label
            status = "used"
        elif decision.can_display and value is not None:
            status = "display_only"
        else:
            status = "not_used"

        _record_detail(
            details=details,
            field_id=field_id,
            key=key,
            status=status,
            value=value,
            source_label=decision.source_label,
            caveat=decision.caveat,
        )

    _derive_metrics(merged, details)
    merged["_supplemented_details"].update(details)
    merged["_financial_report_client"] = {
        "company": getattr(extraction, "company", None),
        "market": getattr(extraction, "market", None),
        "period_end": getattr(extraction, "period_end", None),
        "catalog_version": getattr(extraction, "catalog_version", None),
        "caveats": caveats,
    }
    return FinancialReportMergeResult(financial_data=merged, details=details, caveats=caveats)
```

Modify `tradingagents/dataflows/financial_reports/__init__.py`:

```python
from .mapper import FinancialReportMergeResult, merge_financial_report_data

__all__ = [
    "FieldUseDecision",
    "FinancialReportClientConfig",
    "FinancialReportMergeResult",
    "FinancialReportPolicy",
    "get_financial_report_client_config",
    "merge_financial_report_data",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/unit/test_financial_report_mapper.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/financial_reports/__init__.py tradingagents/dataflows/financial_reports/mapper.py tests/unit/test_financial_report_mapper.py
git commit -m "feat: map annual report fields to financial data"
```

---

### Task 4: Annual Report Formatter

**Files:**
- Create: `tradingagents/dataflows/financial_reports/formatter.py`
- Modify: `tradingagents/dataflows/financial_reports/__init__.py`
- Test: `tests/unit/test_financial_report_formatter.py`

- [ ] **Step 1: Write the failing formatter tests**

Create `tests/unit/test_financial_report_formatter.py`:

```python
from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.financial_reports.formatter import (
    format_annual_report_section,
    format_value_report_source_note,
)


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    currency: str | None = "CNY"
    unit: str | None = "yuan"
    confidence: object = "verified"
    source: str | None = "akshare"
    raw_bucket: str | None = "clean_present"
    evidence_page: int | None = 8
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False
    value: str = "fresh"


@dataclass(frozen=True)
class FakeExtraction:
    company: str = "600519"
    market: str = "CN"
    period_end: str = "2024-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    staleness: FakeStaleness = FakeStaleness()
    fields: dict[str, FakeField] | None = None


def test_format_annual_report_section_includes_reliable_fields():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("100")),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("130")),
    })

    text = format_annual_report_section(
        extraction=extraction,
        caveats=[],
        max_fields=5,
    )

    assert "## 年报权威数据（FinancialReportClient）" in text
    assert "600519 CN 2024-12-31" in text
    assert "catalog_version: 2026-05-02" in text
    assert "net_profit: 100 CNY yuan" in text
    assert "operating_cash_flow: 130 CNY yuan" in text
    assert "page 8" in text


def test_format_annual_report_section_marks_stale_and_llm_caveats():
    extraction = FakeExtraction(
        llm_provider="openai",
        llm_model="codex-subscription",
        staleness=FakeStaleness(is_fresh=False, is_stale=True, value="stale"),
        fields={
            "capital_expenditures": FakeField(
                "capital_expenditures",
                Decimal("30"),
                source="llm",
                raw_bucket="llm_supplement_present",
                is_reliable=False,
            )
        },
    )

    text = format_annual_report_section(
        extraction=extraction,
        caveats=["LLM supplement from codex-subscription allowed by policy"],
        max_fields=5,
    )

    assert "staleness: stale" in text
    assert "stale extraction; display-only unless explicitly allowed" in text
    assert "LLM supplement from codex-subscription allowed by policy" in text


def test_format_value_report_source_note_uses_financial_data_metadata():
    financial_data = {
        "_data_source": {
            "net_profits": "financial-report-client",
            "free_cash_flow": "financial-report-client:derived",
            "roe_avg_3y": "akshare",
        },
        "_financial_report_client": {
            "company": "600519",
            "market": "CN",
            "period_end": "2024-12-31",
            "catalog_version": "2026-05-02",
            "caveats": ["capital_expenditures unavailable: source_unavailable"],
        },
    }

    text = format_value_report_source_note(financial_data)

    assert "▶ 七、年报数据来源说明" in text
    assert "net_profits, free_cash_flow" in text
    assert "600519 CN 2024-12-31" in text
    assert "capital_expenditures unavailable: source_unavailable" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/unit/test_financial_report_formatter.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.dataflows.financial_reports.formatter'`.

- [ ] **Step 3: Implement formatter module**

Create `tradingagents/dataflows/financial_reports/formatter.py`:

```python
"""Format FinancialReportClient data for agent-visible reports."""

from __future__ import annotations

from typing import Any


CORE_FIELDS = (
    "net_profit",
    "operating_cash_flow",
    "capital_expenditures",
    "total_equity",
    "cash_and_equivalents",
    "interest_bearing_debt",
    "current_assets",
    "current_liabilities",
    "total_assets",
    "total_liabilities",
)


def _staleness_text(extraction: Any) -> str:
    staleness = getattr(extraction, "staleness", None)
    return str(getattr(staleness, "value", staleness or "unknown"))


def _format_value(field: Any) -> str:
    value = getattr(field, "value", None)
    currency = getattr(field, "currency", None)
    unit = getattr(field, "unit", None)
    suffix = " ".join(part for part in (currency, unit) if part)
    return f"{value} {suffix}".strip()


def _field_line(field_id: str, field: Any) -> str:
    page = getattr(field, "evidence_page", None)
    source = getattr(field, "source", None) or "unknown"
    page_text = f", page {page}" if page is not None else ""
    reliability = "reliable" if bool(getattr(field, "is_reliable", False)) else "display-only"
    return f"- {field_id}: {_format_value(field)} ({reliability}, source={source}{page_text})"


def format_annual_report_section(*, extraction: Any, caveats: list[str], max_fields: int = 12) -> str:
    fields = getattr(extraction, "fields", {}) if isinstance(getattr(extraction, "fields", {}), dict) else {}
    company = getattr(extraction, "company", "")
    market = getattr(extraction, "market", "")
    period_end = getattr(extraction, "period_end", "")
    catalog_version = getattr(extraction, "catalog_version", "")
    staleness = _staleness_text(extraction)

    lines = [
        "## 年报权威数据（FinancialReportClient）",
        f"- extraction: {company} {market} {period_end}",
        f"- catalog_version: {catalog_version}",
        f"- staleness: {staleness}",
    ]

    staleness_obj = getattr(extraction, "staleness", None)
    if bool(getattr(staleness_obj, "is_stale", False)):
        lines.append("- caveat: stale extraction; display-only unless explicitly allowed")
    if bool(getattr(staleness_obj, "is_missing", False)):
        lines.append("- caveat: missing annual-report extraction")

    shown = 0
    for field_id in CORE_FIELDS:
        if shown >= max_fields:
            break
        field = fields.get(field_id)
        if field is None or getattr(field, "value", None) is None:
            continue
        lines.append(_field_line(field_id, field))
        shown += 1

    for caveat in caveats:
        if caveat:
            lines.append(f"- caveat: {caveat}")

    if shown == 0 and not caveats:
        lines.append("- no usable annual-report fields")

    return "\n".join(lines)


def format_value_report_source_note(financial_data: dict[str, Any]) -> str:
    source_map = financial_data.get("_data_source") if isinstance(financial_data.get("_data_source"), dict) else {}
    frc_fields = [
        key for key, source in source_map.items()
        if isinstance(source, str) and source.startswith("financial-report-client")
    ]
    if not frc_fields:
        return ""

    meta = financial_data.get("_financial_report_client") if isinstance(financial_data.get("_financial_report_client"), dict) else {}
    caveats = meta.get("caveats") if isinstance(meta.get("caveats"), list) else []
    lines = [
        "",
        "▶ 七、年报数据来源说明",
        "───────────────────────────────────────────────────────────────",
        f"  FinancialReportClient 字段参与计算: {', '.join(frc_fields)}",
        f"  extraction: {meta.get('company', '')} {meta.get('market', '')} {meta.get('period_end', '')}",
        f"  catalog_version: {meta.get('catalog_version', '')}",
    ]
    for caveat in caveats[:5]:
        lines.append(f"  caveat: {caveat}")
    return "\n".join(lines) + "\n"
```

Modify `tradingagents/dataflows/financial_reports/__init__.py`:

```python
from .formatter import format_annual_report_section, format_value_report_source_note

__all__ = [
    "FieldUseDecision",
    "FinancialReportClientConfig",
    "FinancialReportMergeResult",
    "FinancialReportPolicy",
    "format_annual_report_section",
    "format_value_report_source_note",
    "get_financial_report_client_config",
    "merge_financial_report_data",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/unit/test_financial_report_formatter.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/financial_reports/__init__.py tradingagents/dataflows/financial_reports/formatter.py tests/unit/test_financial_report_formatter.py
git commit -m "feat: format financial report client output"
```

---

### Task 5: FinancialReportClient Adapter and PDF Resolver

**Files:**
- Create: `tradingagents/dataflows/financial_reports/adapter.py`
- Modify: `tradingagents/dataflows/financial_reports/__init__.py`
- Test: `tests/unit/test_financial_report_adapter.py`

- [ ] **Step 1: Write the failing adapter tests**

Create `tests/unit/test_financial_report_adapter.py`:

```python
from dataclasses import dataclass
from pathlib import Path
import sys
import types

from tradingagents.dataflows.financial_reports.adapter import (
    FinancialReportAdapter,
    FinancialReportAdapterResult,
    infer_annual_period_end,
)
from tradingagents.dataflows.financial_reports.config import FinancialReportClientConfig


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False
    value: str = "fresh"


@dataclass(frozen=True)
class FakeExtraction:
    company: str
    market: str
    period_end: str
    staleness: FakeStaleness
    fields: dict
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None


class FakeRefreshPolicy:
    CACHE_ONLY = "cache_only"
    CACHE_FIRST = "cache_first"
    FORCE_REFRESH = "force_refresh"


class FakeExtractorConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakePdfQuery:
    def __init__(self, company, market, period_end):
        self.company = company
        self.market = market
        self.period_end = period_end


class FakeClient:
    calls = []
    last_config = None

    def __init__(self, config):
        self.config = config
        FakeClient.last_config = config

    def get_extraction(self, **kwargs):
        self.calls.append(kwargs)
        return FakeExtraction(
            company=kwargs["company"],
            market=kwargs["market"],
            period_end=kwargs["period_end"],
            staleness=FakeStaleness(),
            fields={},
        )


class FakeExtractorError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def install_fake_extractor(monkeypatch):
    module = types.ModuleType("financial_report_llm_extractor.client")
    module.ExtractorConfig = FakeExtractorConfig
    module.FinancialReportClient = FakeClient
    module.PdfQuery = FakePdfQuery
    module.RefreshPolicy = FakeRefreshPolicy
    module.ExtractorError = FakeExtractorError
    package = types.ModuleType("financial_report_llm_extractor")
    monkeypatch.setitem(sys.modules, "financial_report_llm_extractor", package)
    monkeypatch.setitem(sys.modules, "financial_report_llm_extractor.client", module)


def test_infer_annual_period_end_uses_conservative_report_calendar():
    assert infer_annual_period_end("2026-05-14") == "2025-12-31"
    assert infer_annual_period_end("2026-03-01") == "2024-12-31"
    assert infer_annual_period_end(None).endswith("-12-31")


def test_adapter_returns_disabled_when_config_disabled():
    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=False,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=False,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ))

    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert result == FinancialReportAdapterResult(
        available=False,
        company="600519",
        market="CN",
        period_end="2024-12-31",
        extraction=None,
        warnings=["FinancialReportClient disabled"],
        errors=[],
    )


def test_adapter_degrades_when_extractor_not_installed(monkeypatch):
    monkeypatch.delitem(sys.modules, "financial_report_llm_extractor.client", raising=False)
    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=False,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ))

    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert result.available is False
    assert "financial-report-llm-extractor is not installed" in result.warnings[0]


def test_adapter_calls_extractor_with_cache_only(monkeypatch):
    install_fake_extractor(monkeypatch)
    FakeClient.calls.clear()
    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=True,
        allow_llm_models=("codex",),
        extractor_cache_root="/tmp/cache",
        llm_config_path="/tmp/llm.json",
        pdf_root="",
    ))

    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert result.available is True
    assert result.extraction.company == "600519"
    assert FakeClient.calls[0]["refresh_policy"] == "cache_only"
    assert FakeClient.calls[0]["include_llm_supplement"] is True
    assert isinstance(FakeClient.last_config.kwargs["cache_root"], Path)
    assert isinstance(FakeClient.last_config.kwargs["llm_config_path"], Path)


def test_adapter_does_not_request_llm_supplement_without_llm_config(monkeypatch):
    install_fake_extractor(monkeypatch)
    FakeClient.calls.clear()
    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=True,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ))

    result = adapter.get_annual_report_data(ticker="600519", market="CN", period_end="2024-12-31")

    assert result.available is True
    assert FakeClient.calls[0]["include_llm_supplement"] is False


def test_pdf_resolver_uses_report_collector_pdf_info(tmp_path):
    pdf = tmp_path / "annual.pdf"
    pdf.write_text("pdf", encoding="utf-8")

    class FakeReportCollector:
        def fetch_latest_pdf_info(self, stock_code, market, report_types):
            return {"file_path": str(pdf)}

    adapter = FinancialReportAdapter(config=FinancialReportClientConfig(
        enabled=True,
        cache_only=True,
        force_refresh=False,
        include_llm_supplement=False,
        allow_llm_models=("codex",),
        extractor_cache_root="",
        llm_config_path="",
        pdf_root="",
    ), report_collector=FakeReportCollector())

    resolved = adapter.resolve_pdf(FakePdfQuery(company="00001", market="HK", period_end="2025-12-31"))

    assert resolved == pdf
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/unit/test_financial_report_adapter.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.dataflows.financial_reports.adapter'`.

- [ ] **Step 3: Implement adapter module**

Create `tradingagents/dataflows/financial_reports/adapter.py`:

```python
"""Adapter around financial-report-llm-extractor public client API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import FinancialReportClientConfig


@dataclass(frozen=True)
class FinancialReportAdapterResult:
    available: bool
    company: str
    market: str
    period_end: str
    extraction: Any | None
    warnings: list[str]
    errors: list[str]


def infer_annual_period_end(reference_date: str | None) -> str:
    if reference_date:
        ref = datetime.strptime(reference_date[:10], "%Y-%m-%d").date()
    else:
        ref = date.today()
    report_year = ref.year - 1 if ref.month >= 5 else ref.year - 2
    return f"{report_year}-12-31"


def _load_extractor_client():
    try:
        from financial_report_llm_extractor.client import (  # type: ignore
            ExtractorConfig,
            ExtractorError,
            FinancialReportClient,
            RefreshPolicy,
        )
        return ExtractorConfig, ExtractorError, FinancialReportClient, RefreshPolicy
    except ImportError:
        return None


def _path_from_pdf_info(pdf_info: dict[str, Any] | None) -> Path | None:
    if not isinstance(pdf_info, dict):
        return None
    for key in ("file_path", "path", "pdf_path", "local_path"):
        raw = pdf_info.get(key)
        if raw:
            path = Path(str(raw))
            if path.exists():
                return path
    return None


def _optional_path(raw: str) -> Path | None:
    return Path(raw) if raw else None


class FinancialReportAdapter:
    def __init__(
        self,
        *,
        config: FinancialReportClientConfig,
        report_collector: Any | None = None,
    ) -> None:
        self.config = config
        self.report_collector = report_collector

    def resolve_pdf(self, query: Any) -> Path | None:
        if self.config.pdf_root:
            root = Path(self.config.pdf_root)
            candidates = (
                root / str(query.market).lower() / str(query.company) / f"{query.period_end}.pdf",
                root / str(query.market).upper() / str(query.company) / f"{query.period_end}.pdf",
                root / str(query.company) / f"{query.period_end}.pdf",
            )
            for candidate in candidates:
                if candidate.exists():
                    return candidate

        if self.report_collector is not None:
            pdf_info = self.report_collector.fetch_latest_pdf_info(
                stock_code=str(query.company),
                market=str(query.market),
                report_types=("annual",),
            )
            return _path_from_pdf_info(pdf_info)
        return None

    def _refresh_policy(self, refresh_policy_module: Any) -> Any:
        if self.config.force_refresh:
            return refresh_policy_module.FORCE_REFRESH
        if self.config.cache_only:
            return refresh_policy_module.CACHE_ONLY
        return refresh_policy_module.CACHE_FIRST

    def get_annual_report_data(
        self,
        *,
        ticker: str,
        market: str,
        period_end: str | None,
        reference_date: str | None = None,
    ) -> FinancialReportAdapterResult:
        resolved_period_end = period_end or infer_annual_period_end(reference_date)
        if not self.config.enabled:
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=["FinancialReportClient disabled"],
                errors=[],
            )

        loaded = _load_extractor_client()
        if loaded is None:
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=["financial-report-llm-extractor is not installed"],
                errors=[],
            )

        ExtractorConfig, ExtractorError, FinancialReportClient, RefreshPolicy = loaded
        try:
            extractor_config = ExtractorConfig(
                llm_config_path=_optional_path(self.config.llm_config_path),
                cache_root=_optional_path(self.config.extractor_cache_root),
                pdf_resolver=self.resolve_pdf,
            )
            client = FinancialReportClient(config=extractor_config)
            include_llm = bool(self.config.include_llm_supplement and self.config.llm_config_path)
            extraction = client.get_extraction(
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                include_llm_supplement=include_llm,
                refresh_policy=self._refresh_policy(RefreshPolicy),
            )
            warnings: list[str] = []
            staleness = getattr(extraction, "staleness", None)
            if bool(getattr(staleness, "is_missing", False)):
                warnings.append("annual-report extraction missing")
            if bool(getattr(staleness, "is_stale", False)):
                warnings.append("annual-report extraction stale")
            return FinancialReportAdapterResult(
                available=not bool(getattr(staleness, "is_missing", False)),
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=extraction,
                warnings=warnings,
                errors=[],
            )
        except ExtractorError as exc:
            reason = getattr(exc, "reason", "extractor_error")
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=[],
                errors=[f"{reason}: {exc}"],
            )
        except Exception as exc:
            return FinancialReportAdapterResult(
                available=False,
                company=ticker,
                market=market,
                period_end=resolved_period_end,
                extraction=None,
                warnings=[],
                errors=[f"unexpected_error: {exc}"],
            )


def create_financial_report_adapter(config: FinancialReportClientConfig) -> FinancialReportAdapter:
    """Create adapter with report-collector wired only as a PDF provider."""
    report_collector = None
    try:
        from tradingagents.services.report_collector_config import get_report_collector_config
        from tradingagents.services.report_collector_client import ReportCollectorClient

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

Modify `tradingagents/dataflows/financial_reports/__init__.py`:

```python
from .adapter import (
    FinancialReportAdapter,
    FinancialReportAdapterResult,
    create_financial_report_adapter,
    infer_annual_period_end,
)

__all__ = [
    "FieldUseDecision",
    "FinancialReportAdapter",
    "FinancialReportAdapterResult",
    "FinancialReportClientConfig",
    "FinancialReportMergeResult",
    "FinancialReportPolicy",
    "create_financial_report_adapter",
    "format_annual_report_section",
    "format_value_report_source_note",
    "get_financial_report_client_config",
    "infer_annual_period_end",
    "merge_financial_report_data",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/unit/test_financial_report_adapter.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/financial_reports/__init__.py tradingagents/dataflows/financial_reports/adapter.py tests/unit/test_financial_report_adapter.py
git commit -m "feat: add financial report client adapter"
```

---

### Task 6: Value Investment Integration

**Files:**
- Create: `tradingagents/dataflows/financial_reports/integration.py`
- Modify: `tradingagents/dataflows/financial_reports/__init__.py`
- Modify: `tradingagents/tools/value_investment_tool.py`
- Test: `tests/unit/test_financial_report_integration.py`
- Test: `tests/unit/test_financial_report_value_integration.py`

- [ ] **Step 1: Write the failing value integration tests**

Create `tests/unit/test_financial_report_integration.py`:

```python
from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.financial_reports.integration import (
    apply_financial_report_client_data,
)


def _load_tool_module():
    langchain_core = types.ModuleType("langchain_core")
    langchain_tools = types.ModuleType("langchain_core.tools")
    langchain_tools.tool = lambda func=None, *args, **kwargs: func if func is not None else (lambda f: f)
    sys.modules.setdefault("langchain_core", langchain_core)
    sys.modules.setdefault("langchain_core.tools", langchain_tools)

    module_path = Path(__file__).resolve().parents[2] / "tradingagents" / "tools" / "value_investment_tool.py"
    spec = importlib.util.spec_from_file_location("value_investment_tool_local", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    source: str | None = "akshare"
    confidence: object = "verified"
    raw_bucket: str | None = "clean_present"
    currency: str | None = "CNY"
    unit: str | None = "yuan"
    evidence_page: int | None = 1
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeExtraction:
    company: str = "600519"
    market: str = "CN"
    period_end: str = "2024-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    staleness: object | None = None
    fields: dict | None = None


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False
    value: str = "fresh"


@dataclass(frozen=True)
class FakeAdapterResult:
    available: bool
    company: str
    market: str
    period_end: str
    extraction: object | None
    warnings: list[str]
    errors: list[str]


def test_apply_financial_report_client_data_merges_reliable_fields(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "true")
    extraction = FakeExtraction(staleness=FakeStaleness(), fields={
        "net_profit": FakeField("net_profit", Decimal("100")),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("150")),
        "capital_expenditures": FakeField("capital_expenditures", Decimal("40")),
    })

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass

        def get_annual_report_data(self, **kwargs):
            return FakeAdapterResult(
                available=True,
                company=kwargs["ticker"],
                market=kwargs["market"],
                period_end=kwargs["period_end"],
                extraction=extraction,
                warnings=[],
                errors=[],
            )

    monkeypatch.setattr("tradingagents.dataflows.financial_reports.integration.create_financial_report_adapter", lambda config: FakeAdapter())
    base = {"net_profits": [1.0], "operating_cash_flow": None, "free_cash_flow": None}

    merged = apply_financial_report_client_data(
        financial_data=base,
        ticker="600519",
        market="CN",
        period_end="2024-12-31",
    )

    assert merged["net_profits"][0] == 100.0
    assert merged["operating_cash_flow"] == 150.0
    assert merged["free_cash_flow"] == 110.0
    assert merged["_data_source"]["net_profits"] == "financial-report-client"


def test_apply_financial_report_client_data_noops_when_disabled(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "false")
    base = {"net_profits": [1.0]}

    merged = apply_financial_report_client_data(
        financial_data=base,
        ticker="600519",
        market="CN",
        period_end="2024-12-31",
    )

    assert merged == base


def test_stale_extraction_is_display_only_and_does_not_override(monkeypatch):
    monkeypatch.setenv("FINANCIAL_REPORT_CLIENT_ENABLED", "true")
    extraction = FakeExtraction(
        staleness=FakeStaleness(is_fresh=False, is_stale=True, value="stale"),
        fields={"net_profit": FakeField("net_profit", Decimal("100"))},
    )

    class FakeAdapter:
        def get_annual_report_data(self, **kwargs):
            return FakeAdapterResult(
                available=True,
                company=kwargs["ticker"],
                market=kwargs["market"],
                period_end=kwargs["period_end"],
                extraction=extraction,
                warnings=["annual-report extraction stale"],
                errors=[],
            )

    monkeypatch.setattr("tradingagents.dataflows.financial_reports.integration.create_financial_report_adapter", lambda config: FakeAdapter())
    base = {"net_profits": [1.0], "_data_source": {"net_profits": "akshare"}}

    merged = apply_financial_report_client_data(
        financial_data=base,
        ticker="600519",
        market="CN",
        period_end="2024-12-31",
    )

    assert merged["net_profits"] == [1.0]
    assert merged["_data_source"]["net_profits"] == "akshare"
    assert "stale" in " ".join(merged["_financial_report_client"]["caveats"])
```

Create `tests/unit/test_financial_report_value_integration.py`:

```python
import importlib.util
from pathlib import Path
import sys
import types


def _load_tool_module():
    langchain_core = types.ModuleType("langchain_core")
    langchain_tools = types.ModuleType("langchain_core.tools")
    langchain_tools.tool = lambda func=None, *args, **kwargs: func if func is not None else (lambda f: f)
    sys.modules.setdefault("langchain_core", langchain_core)
    sys.modules.setdefault("langchain_core.tools", langchain_tools)

    module_path = Path(__file__).resolve().parents[2] / "tradingagents" / "tools" / "value_investment_tool.py"
    spec = importlib.util.spec_from_file_location("value_investment_tool_local", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_value_tool_imports_lightweight_financial_report_helper():
    source = Path("tradingagents/tools/value_investment_tool.py").read_text(encoding="utf-8")

    assert "apply_financial_report_client_data" in source
    assert "format_value_report_source_note" in source
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/unit/test_financial_report_integration.py tests/unit/test_financial_report_value_integration.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.dataflows.financial_reports.integration'`.

- [ ] **Step 3: Add lightweight integration helper and value tool wiring**

Create `tradingagents/dataflows/financial_reports/integration.py`:

```python
"""Lightweight integration helpers for TradingAgents tools."""

from __future__ import annotations

from typing import Any

from .adapter import create_financial_report_adapter
from .config import get_financial_report_client_config
from .mapper import merge_financial_report_data
from .policy import FinancialReportPolicy


def _metadata_only(
    financial_data: dict[str, Any],
    *,
    company: str,
    market: str,
    period_end: str,
    catalog_version: str | None,
    caveats: list[str],
) -> dict[str, Any]:
    updated = dict(financial_data)
    updated["_financial_report_client"] = {
        "company": company,
        "market": market,
        "period_end": period_end,
        "catalog_version": catalog_version,
        "caveats": caveats,
    }
    return updated


def apply_financial_report_client_data(
    *,
    financial_data: dict[str, Any],
    ticker: str,
    market: str,
    period_end: str | None = None,
) -> dict[str, Any]:
    """Merge authoritative annual-report fields into financial_data when enabled."""
    frc_config = get_financial_report_client_config()
    if not frc_config.enabled:
        return financial_data

    adapter = create_financial_report_adapter(frc_config)
    normalized_market = "CN" if market == "A" else market
    result = adapter.get_annual_report_data(
        ticker=ticker,
        market=normalized_market,
        period_end=period_end,
    )
    if result.extraction is None:
        return _metadata_only(
            financial_data,
            company=result.company,
            market=result.market,
            period_end=result.period_end,
            catalog_version=None,
            caveats=result.warnings + result.errors,
        )

    staleness = getattr(result.extraction, "staleness", None)
    if bool(getattr(staleness, "is_stale", False)):
        return _metadata_only(
            financial_data,
            company=result.company,
            market=result.market,
            period_end=result.period_end,
            catalog_version=getattr(result.extraction, "catalog_version", None),
            caveats=result.warnings + result.errors + ["stale extraction is display-only by policy"],
        )

    policy = FinancialReportPolicy(allow_llm_models=frc_config.allow_llm_models)
    merge_result = merge_financial_report_data(
        financial_data=financial_data,
        extraction=result.extraction,
        policy=policy,
    )
    if result.warnings or result.errors:
        meta = merge_result.financial_data.setdefault("_financial_report_client", {})
        caveats = list(meta.get("caveats") or [])
        caveats.extend(result.warnings)
        caveats.extend(result.errors)
        meta["caveats"] = caveats
    return merge_result.financial_data
```

Modify `tradingagents/dataflows/financial_reports/__init__.py`:

```python
from .integration import apply_financial_report_client_data
```

Add imports near existing imports in `tradingagents/tools/value_investment_tool.py`:

```python
from tradingagents.dataflows.financial_reports import (
    apply_financial_report_client_data,
    format_value_report_source_note,
)
```

Modify `get_value_investment_analysis()` after the report-collector block and before market data:

```python
        financial_data = apply_financial_report_client_data(
            financial_data=financial_data,
            ticker=ticker,
            market=market,
            period_end=None,
        )
```

Modify `_generate_report()` before the disclaimer block:

```python
    financial_report_note = format_value_report_source_note(financial_data or {})
    if financial_report_note:
        report += financial_report_note
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python -m pytest tests/unit/test_financial_report_integration.py tests/unit/test_financial_report_value_integration.py -v
```

Expected: PASS.

- [ ] **Step 5: Run focused regression tests**

Run:

```bash
python -m pytest tests/unit/test_financial_report_integration.py tests/unit/test_financial_report_value_integration.py tests/unit/test_report_collector_analysis_disabled.py -v
```

Expected: PASS. This proves FinancialReportClient is the new default annual-report field path and report-collector analysis remains opt-in.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/dataflows/financial_reports/__init__.py tradingagents/dataflows/financial_reports/integration.py tradingagents/tools/value_investment_tool.py tests/unit/test_financial_report_integration.py tests/unit/test_financial_report_value_integration.py
git commit -m "feat: use financial report client in value analysis"
```

---

### Task 7: Fundamentals Tool Integration

**Files:**
- Modify: `tradingagents/agents/utils/agent_utils.py`
- Test: `tests/unit/test_financial_report_fundamentals_integration.py`

- [ ] **Step 1: Write the failing fundamentals integration tests**

Create `tests/unit/test_financial_report_fundamentals_integration.py`:

```python
from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.financial_reports.formatter import format_annual_report_section


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    currency: str | None = "HKD"
    unit: str | None = "million"
    confidence: object = "verified"
    source: str | None = "yahoo"
    raw_bucket: str | None = "clean_present"
    evidence_page: int | None = 10
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False
    value: str = "fresh"


@dataclass(frozen=True)
class FakeExtraction:
    company: str = "00001"
    market: str = "HK"
    period_end: str = "2025-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    staleness: FakeStaleness = FakeStaleness()
    fields: dict | None = None


def test_fundamentals_formatter_section_is_ready_for_agent_output():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("32000")),
        "operating_cash_flow": FakeField("operating_cash_flow", Decimal("41000")),
    })

    section = format_annual_report_section(extraction=extraction, caveats=[], max_fields=10)

    assert section.startswith("## 年报权威数据（FinancialReportClient）")
    assert "00001 HK 2025-12-31" in section
    assert "net_profit: 32000 HKD million" in section
```

- [ ] **Step 2: Run test to verify it fails or confirms formatter behavior**

Run:

```bash
python -m pytest tests/unit/test_financial_report_fundamentals_integration.py -v
```

Expected before `formatter.py` exists: FAIL with module import error. Expected after Task 4: PASS. If it passes before editing `agent_utils.py`, keep it as a stable formatter-level contract and add source-code integration in Step 3.

- [ ] **Step 3: Add annual-report helper to `agent_utils.py`**

Inside `get_stock_fundamentals_unified()`, before market-specific data fetch branches build the final `result_data`, add this nested helper near other local helpers:

```python
        def _try_financial_report_client_section() -> str:
            try:
                from tradingagents.dataflows.financial_reports import (
                    FinancialReportPolicy,
                    create_financial_report_adapter,
                    format_annual_report_section,
                    get_financial_report_client_config,
                )

                frc_config = get_financial_report_client_config()
                if not frc_config.enabled:
                    return ""

                normalized_market = "HK" if str(ticker).upper().endswith(".HK") else "CN"
                normalized_ticker = str(ticker).upper().replace(".HK", "").zfill(5) if normalized_market == "HK" else str(ticker).split(".")[0]
                adapter = create_financial_report_adapter(frc_config)
                result = adapter.get_annual_report_data(
                    ticker=normalized_ticker,
                    market=normalized_market,
                    period_end=None,
                    reference_date=curr_date or end_date,
                )
                if result.extraction is None:
                    caveats = result.warnings + result.errors
                    if not caveats:
                        return ""
                    return "## 年报权威数据（FinancialReportClient）\n" + "\n".join(f"- caveat: {item}" for item in caveats)

                policy = FinancialReportPolicy(allow_llm_models=frc_config.allow_llm_models)
                caveats = []
                for field in getattr(result.extraction, "fields", {}).values():
                    decision = policy.decide(field=field, result=result.extraction)
                    if decision.caveat:
                        caveats.append(decision.caveat)
                caveats.extend(result.warnings)
                caveats.extend(result.errors)
                return format_annual_report_section(
                    extraction=result.extraction,
                    caveats=caveats,
                    max_fields=12,
                )
            except Exception as exc:
                logger.debug(f"[FinancialReportClient] fundamentals section unavailable: {exc}")
                return ""
```

Then, immediately after `result_data = []` is initialized in `get_stock_fundamentals_unified()`, insert:

```python
        financial_report_section = _try_financial_report_client_section()
        if financial_report_section:
            result_data.append(financial_report_section)
```

If `result_data` is initialized inside market-specific branches rather than once at function top, insert the same two lines at the start of each A-share and HK branch after that branch initializes `result_data`.

- [ ] **Step 4: Add source-level guard test**

Append to `tests/unit/test_financial_report_fundamentals_integration.py`:

```python
from pathlib import Path


def test_agent_utils_calls_financial_report_client_before_report_collector_analysis():
    source = Path("tradingagents/agents/utils/agent_utils.py").read_text(encoding="utf-8")

    assert "_try_financial_report_client_section" in source
    assert "format_annual_report_section" in source
    assert "create_financial_report_adapter" in source
    assert "get_financial_report_client_config" in source
    assert "report_collector_analysis_enabled" in source
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
python -m pytest tests/unit/test_financial_report_fundamentals_integration.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tradingagents/agents/utils/agent_utils.py tests/unit/test_financial_report_fundamentals_integration.py
git commit -m "feat: show annual report authority in fundamentals"
```

---

### Task 8: Full Verification and Documentation Check

**Files:**
- Review only unless verification exposes failures.

- [ ] **Step 1: Run all new unit tests**

Run:

```bash
python -m pytest \
  tests/unit/test_financial_report_config.py \
  tests/unit/test_financial_report_policy.py \
  tests/unit/test_financial_report_mapper.py \
  tests/unit/test_financial_report_formatter.py \
  tests/unit/test_financial_report_adapter.py \
  tests/unit/test_financial_report_integration.py \
  tests/unit/test_financial_report_value_integration.py \
  tests/unit/test_financial_report_fundamentals_integration.py \
  tests/unit/test_report_collector_analysis_disabled.py \
  -v
```

Expected: PASS. If pytest is unavailable in the local environment, run the same command with the project virtualenv after installing test dependencies, then record the environment gap in the final handoff.

- [ ] **Step 2: Run compile check**

Run:

```bash
python -m py_compile \
  tradingagents/dataflows/financial_reports/__init__.py \
  tradingagents/dataflows/financial_reports/config.py \
  tradingagents/dataflows/financial_reports/policy.py \
  tradingagents/dataflows/financial_reports/mapper.py \
  tradingagents/dataflows/financial_reports/formatter.py \
  tradingagents/dataflows/financial_reports/adapter.py \
  tradingagents/dataflows/financial_reports/integration.py \
  tradingagents/tools/value_investment_tool.py \
  tradingagents/agents/utils/agent_utils.py
```

Expected: exit code 0.

- [ ] **Step 3: Confirm extractor internals are not referenced**

Run:

```bash
rg "extracted.db|tmp/runs|evaluation.json|llm_evidence_supplement|sqlite3|query_extraction" tradingagents tests docs/superpowers/specs docs/superpowers/plans
```

Expected: no matches in `tradingagents/` implementation files. Matches in spec/plan text are acceptable because they document forbidden internals.

- [ ] **Step 4: Confirm report-collector analysis remains default-off**

Run:

```bash
python -m pytest tests/unit/test_report_collector_analysis_disabled.py -v
```

Expected: PASS.

- [ ] **Step 5: Check git status**

Run:

```bash
git status --short
```

Expected: only intended files are modified. `.codex/` may remain untracked and must not be committed.

- [ ] **Step 6: Final commit**

If Task 8 required edits, commit them:

```bash
git add docs/superpowers/plans/2026-05-14-financial-report-client-adapter-phase1b.md
git commit -m "docs: add financial report client adapter phase1b plan"
```

If no implementation edits were made in Task 8, commit only the plan document:

```bash
git add docs/superpowers/plans/2026-05-14-financial-report-client-adapter-phase1b.md
git commit -m "docs: add financial report client adapter phase1b plan"
```

---

## Self-Review

**Spec coverage:**

- Public `FinancialReportClient` only, no DB/JSON/CLI reads: Task 5 adapter imports only `financial_report_llm_extractor.client`; Task 8 `rg` guard checks forbidden internals.
- Python 3.11 precondition: Task 1 updates `pyproject.toml`.
- Independent adapter/mapper/policy/formatter/integration package: Tasks 1-6 create `tradingagents/dataflows/financial_reports/`.
- `FieldValue.is_reliable` default compute rule: Task 2 policy and Task 3 mapper enforce it.
- LLM supplement policy: Task 2 tests DeepSeek display-only and Codex allow-list compute with caveat.
- Staleness handling: Task 5 returns warnings; Task 4 formats stale caveat; Task 6 blocks stale extraction from overriding calculation inputs.
- Value-investment integration: Task 6 merges fresh annual-report data before calculations and appends source note.
- Fundamentals integration: Task 7 inserts annual-report authority section.
- report-collector boundary: prior commit keeps analysis default-off; Task 5 uses only `fetch_latest_pdf_info()` in PDF resolver; Task 8 verifies default-off test.
- LLM config boundary: Task 1 and Task 5 pass `llm_config_path` only; Task 5 requests LLM supplement only when explicitly enabled and config path is set; no API key management in TradingAgents-CN.

**Placeholder scan:**

- No forbidden placeholder markers or unspecified handler steps are present.
- Every new module has concrete test code and implementation code.
- Every modification step names exact files and insertion points.

**Type consistency:**

- `FinancialReportClientConfig` fields match all adapter/value call sites.
- `FinancialReportPolicy.decide(field=..., result=...)` is used consistently by mapper and fundamentals integration.
- `FinancialReportMergeResult.financial_data/details/caveats` is used consistently by mapper and value integration.
- Formatter functions are named consistently: `format_annual_report_section()` and `format_value_report_source_note()`.
