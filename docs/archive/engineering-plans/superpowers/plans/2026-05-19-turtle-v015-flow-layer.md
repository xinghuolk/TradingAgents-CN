# Turtle v0.15 Flow Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Turtle v0.15-compatible value-investment flow behind the existing opt-in `value` analyst without rebuilding PDF extraction.

**Architecture:** Reuse `report-collector` only as PDF resolver and `FinancialReportClient` as the annual-report authority. Add a focused `tradingagents/dataflows/value_investment/turtle/` package for facts, adapters, deterministic calculations, and prompt formatting, then modify the existing value analyst so tools only prepare facts/signals and the final LLM report generation runs with no bound tools.

**Tech Stack:** Python 3.11, pytest, LangChain messages/tools, LangGraph `ToolNode`, existing `financial_reports` adapter package, existing value-investment fetchers.

---

## Spec Reference

- Design spec: `docs/superpowers/specs/2026-05-19-turtle-v015-flow-layer-design.md`
- Current value analyst: `tradingagents/agents/analysts/value_analyst.py`
- Existing value tool wrapper: `tradingagents/agents/utils/agent_utils.py::Toolkit.get_value_investment_analysis`
- Existing value graph tool node: `tradingagents/graph/trading_graph.py`
- Existing financial report adapter: `tradingagents/dataflows/financial_reports/`

## File Structure

- Create: `tradingagents/dataflows/value_investment/turtle/__init__.py`
  Public exports for Turtle flow internals used by tools and tests.
- Create: `tradingagents/dataflows/value_investment/turtle/facts.py`
  Dataclasses for `MoneyAmount`, run context, facts, calculation results, and status.
- Create: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`
  Converts public `FinancialReportClient` output into `TurtleReportFacts`; owns Turtle annual-period inference.
- Create: `tradingagents/dataflows/value_investment/turtle/market_adapter.py`
  Converts existing market/dividend/buyback fetchers and default holding-channel rules into `TurtleMarketFacts`.
- Create: `tradingagents/dataflows/value_investment/turtle/calculations.py`
  Deterministic Turtle calculations for payout anchor, R, GG, HH, EV switch, cash protection, and status.
- Create: `tradingagents/dataflows/value_investment/turtle/formatting.py`
  Markdown/JSON-safe formatting for facts and computed signals.
- Create: `tradingagents/dataflows/value_investment/turtle/decision.py`
  Prompt builder and non-decisionable report builder; no external-data tools.
- Create: `tradingagents/tools/turtle_analysis_tool.py`
  Tool entry that returns prepared Turtle facts/signals as JSON.
- Modify: `tradingagents/dataflows/value_investment/__init__.py`
  Export Turtle package symbols where useful.
- Modify: `tradingagents/agents/utils/agent_utils.py`
  Add `Toolkit.prepare_turtle_analysis` as a LangChain tool while preserving legacy `get_value_investment_analysis`.
- Modify: `tradingagents/graph/trading_graph.py`
  Use `prepare_turtle_analysis` in the `tools_value` node.
- Modify: `tradingagents/agents/analysts/value_analyst.py`
  Split value analyst into tool-backed fact preparation and plain no-tool final decision generation.
- Create: `tests/unit/test_turtle_facts.py`
- Create: `tests/unit/test_turtle_report_adapter.py`
- Create: `tests/unit/test_turtle_market_adapter.py`
- Create: `tests/unit/test_turtle_calculations.py`
- Create: `tests/unit/test_turtle_decision.py`
- Create: `tests/unit/test_turtle_value_analyst_integration.py`
- Create: `scripts/smoke_test_turtle_value.py`

## Task 1: Turtle Facts And Money Model

**Files:**
- Create: `tests/unit/test_turtle_facts.py`
- Create: `tradingagents/dataflows/value_investment/turtle/__init__.py`
- Create: `tradingagents/dataflows/value_investment/turtle/facts.py`

- [ ] **Step 1: Write failing facts and money tests**

Create `tests/unit/test_turtle_facts.py`:

```python
import pytest

from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleRunContext,
    infer_turtle_period_end,
)


def test_infer_turtle_period_end_uses_march_cutoff():
    assert infer_turtle_period_end("2026-03-31") == "2024-12-31"
    assert infer_turtle_period_end("2026-04-01") == "2025-12-31"
    assert infer_turtle_period_end("2026-12-31") == "2025-12-31"


def test_money_amount_converts_report_units_to_hundred_million():
    assert MoneyAmount(100, "CNY", "million", "src", "ref").to_hundred_million().value == pytest.approx(1.0)
    assert MoneyAmount(100000, "CNY", "thousand", "src", "ref").to_hundred_million().value == pytest.approx(1.0)
    assert MoneyAmount(10000, "CNY", "ten_thousand", "src", "ref").to_hundred_million().value == pytest.approx(1.0)
    assert MoneyAmount(3, "CNY", "hundred_million", "src", "ref").to_hundred_million().value == pytest.approx(3.0)


def test_money_amount_requires_fx_for_non_rmb_normalization():
    amount = MoneyAmount(100, "HKD", "million", "src", "ref")

    with pytest.raises(ValueError, match="FX rate required"):
        amount.to_hundred_million(target_currency="CNY")

    converted = amount.to_hundred_million(target_currency="CNY", fx_rates={"HKD:CNY": 0.92})

    assert converted.value == pytest.approx(0.92)
    assert converted.currency == "CNY"
    assert "FX HKD:CNY=0.92" in converted.source_reference


def test_turtle_run_context_tracks_defaults_and_period_end():
    context = TurtleRunContext.for_ticker(
        ticker="00001",
        market="HK",
        trade_date="2026-05-19",
        company_name="CK Hutchison",
    )

    assert context.period_end == "2025-12-31"
    assert context.holding_channel == "stock_connect"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_facts.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.dataflows.value_investment.turtle'`.

- [ ] **Step 3: Implement the facts module**

Create `tradingagents/dataflows/value_investment/turtle/facts.py`:

```python
"""Core Turtle v0.15 fact and calculation data structures."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


MoneyUnit = Literal["yuan", "thousand", "ten_thousand", "million", "hundred_million"]
TurtleStatus = Literal["complete", "degraded", "non_decisionable", "unsupported"]


def infer_turtle_period_end(reference_date: str | None) -> str:
    ref = datetime.strptime((reference_date or datetime.today().strftime("%Y-%m-%d"))[:10], "%Y-%m-%d")
    report_year = ref.year - 2 if ref.month <= 3 else ref.year - 1
    return f"{report_year}-12-31"


def default_holding_channel(market: str) -> str:
    normalized = market.upper()
    if normalized in {"HK", "HKG"}:
        return "stock_connect"
    if normalized in {"A", "CN", "CHINA"}:
        return "long_term_domestic"
    if normalized in {"US", "USA"}:
        return "w8ben"
    return "unknown"


@dataclass(frozen=True)
class MoneyAmount:
    value: float
    currency: str
    unit: MoneyUnit
    source_label: str
    source_reference: str
    reliability: str = "reliable"

    def to_hundred_million(
        self,
        *,
        target_currency: str = "CNY",
        fx_rates: dict[str, float] | None = None,
    ) -> "MoneyAmount":
        multipliers = {
            "yuan": 1 / 100_000_000,
            "thousand": 1 / 100_000,
            "ten_thousand": 1 / 10_000,
            "million": 1 / 100,
            "hundred_million": 1,
        }
        normalized_value = float(self.value) * multipliers[self.unit]
        normalized_currency = self.currency.upper()
        desired_currency = target_currency.upper()
        source_reference = self.source_reference

        if normalized_currency != desired_currency:
            pair = f"{normalized_currency}:{desired_currency}"
            rates = fx_rates or {}
            if pair not in rates:
                raise ValueError(f"FX rate required for {pair}")
            normalized_value *= rates[pair]
            normalized_currency = desired_currency
            source_reference = f"{source_reference}; FX {pair}={rates[pair]}"

        return MoneyAmount(
            value=normalized_value,
            currency=normalized_currency,
            unit="hundred_million",
            source_label=self.source_label,
            source_reference=source_reference,
            reliability=self.reliability,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TurtleRunContext:
    ticker: str
    market: str
    trade_date: str
    period_end: str
    holding_channel: str
    company_name: str

    @classmethod
    def for_ticker(
        cls,
        *,
        ticker: str,
        market: str,
        trade_date: str,
        company_name: str,
        holding_channel: str | None = None,
        period_end: str | None = None,
    ) -> "TurtleRunContext":
        return cls(
            ticker=ticker,
            market=market,
            trade_date=trade_date,
            period_end=period_end or infer_turtle_period_end(trade_date),
            holding_channel=holding_channel or default_holding_channel(market),
            company_name=company_name,
        )


@dataclass(frozen=True)
class TurtleFactValue:
    name: str
    value: Any
    source_label: str
    source_reference: str
    reliability: str = "reliable"
    caveat: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(self.value, MoneyAmount):
            data["value"] = self.value.to_dict()
        return data


@dataclass(frozen=True)
class TurtleReportFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "metadata": self.metadata,
            "caveats": self.caveats,
        }


@dataclass(frozen=True)
class TurtleMarketFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "caveats": self.caveats,
        }


@dataclass(frozen=True)
class TurtleFacts:
    context: TurtleRunContext
    report: TurtleReportFacts
    market: TurtleMarketFacts
    status: TurtleStatus
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": asdict(self.context),
            "report": self.report.to_dict(),
            "market": self.market.to_dict(),
            "status": self.status,
            "caveats": self.caveats,
        }


@dataclass(frozen=True)
class FormulaResult:
    name: str
    formula: str
    substitution: str
    value: float | None
    unit: str
    sources: list[str]
    missing_inputs: list[str] = field(default_factory=list)
    status: TurtleStatus = "complete"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TurtleComputedSignals:
    status: TurtleStatus
    results: dict[str, FormulaResult] = field(default_factory=dict)
    veto_reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "results": {key: value.to_dict() for key, value in self.results.items()},
            "veto_reasons": self.veto_reasons,
            "caveats": self.caveats,
        }
```

Create `tradingagents/dataflows/value_investment/turtle/__init__.py`:

```python
"""Turtle v0.15 value-investment flow helpers."""

from .facts import (
    FormulaResult,
    MoneyAmount,
    TurtleComputedSignals,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
    default_holding_channel,
    infer_turtle_period_end,
)

__all__ = [
    "FormulaResult",
    "MoneyAmount",
    "TurtleComputedSignals",
    "TurtleFactValue",
    "TurtleFacts",
    "TurtleMarketFacts",
    "TurtleReportFacts",
    "TurtleRunContext",
    "default_holding_channel",
    "infer_turtle_period_end",
]
```

- [ ] **Step 4: Run facts tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_facts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add tradingagents/dataflows/value_investment/turtle/__init__.py tradingagents/dataflows/value_investment/turtle/facts.py tests/unit/test_turtle_facts.py
git commit -m "feat: add turtle fact model"
```

## Task 2: Report Adapter From FinancialReportClient

**Files:**
- Create: `tests/unit/test_turtle_report_adapter.py`
- Create: `tradingagents/dataflows/value_investment/turtle/report_adapter.py`
- Modify: `tradingagents/dataflows/value_investment/turtle/__init__.py`

- [ ] **Step 1: Write failing report adapter tests**

Create `tests/unit/test_turtle_report_adapter.py`:

```python
from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    build_report_facts_from_extraction,
    get_turtle_report_facts,
)


@dataclass(frozen=True)
class FakeField:
    field_id: str
    value: object
    source: str = "akshare"
    confidence: object = "verified"
    raw_bucket: str = "clean_present"
    currency: str = "CNY"
    unit: str = "yuan"
    evidence_page: int | None = 7
    is_reliable: bool = True
    is_present: bool = True


@dataclass(frozen=True)
class FakeStaleness:
    is_fresh: bool = True
    is_stale: bool = False
    is_missing: bool = False


@dataclass(frozen=True)
class FakeExtraction:
    company: str = "600519"
    market: str = "CN"
    period_end: str = "2025-12-31"
    catalog_version: str = "2026-05-02"
    llm_provider: str | None = None
    llm_model: str | None = None
    staleness: object = FakeStaleness()
    fields: dict | None = None


@dataclass(frozen=True)
class FakeAdapterResult:
    available: bool
    company: str
    market: str
    period_end: str
    extraction: object | None
    warnings: list[str]
    errors: list[str]


def test_build_report_facts_preserves_reliable_and_display_only_boundaries():
    extraction = FakeExtraction(fields={
        "net_profit": FakeField("net_profit", Decimal("10000000000")),
        "cash": FakeField("cash", Decimal("25000000000")),
        "audit_opinion": FakeField(
            "audit_opinion",
            "标准无保留意见",
            source="llm",
            confidence="llm_supplement",
            is_reliable=False,
        ),
    })

    facts = build_report_facts_from_extraction(
        extraction=extraction,
        allow_llm_models=(),
        adapter_caveats=[],
    )

    assert facts.fields["net_profit"].value.value == 10_000_000_000.0
    assert facts.fields["net_profit"].reliability == "reliable"
    assert facts.fields["audit_opinion"].reliability == "display_only"
    assert "display-only" in " ".join(facts.caveats)


def test_missing_extraction_returns_caveat_only_facts():
    facts = build_report_facts_from_extraction(
        extraction=None,
        allow_llm_models=(),
        adapter_caveats=["annual-report extraction missing"],
    )

    assert facts.fields == {}
    assert facts.caveats == ["annual-report extraction missing"]


def test_get_turtle_report_facts_passes_turtle_period_end_to_adapter(monkeypatch):
    captured = {}

    class FakeAdapter:
        def get_annual_report_data(self, **kwargs):
            captured.update(kwargs)
            return FakeAdapterResult(
                available=True,
                company=kwargs["ticker"],
                market=kwargs["market"],
                period_end=kwargs["period_end"],
                extraction=FakeExtraction(period_end=kwargs["period_end"], fields={}),
                warnings=[],
                errors=[],
            )

    facts = get_turtle_report_facts(
        ticker="600519",
        market="A",
        trade_date="2026-04-01",
        adapter=FakeAdapter(),
        allow_llm_models=(),
    )

    assert captured["period_end"] == "2025-12-31"
    assert captured["market"] == "CN"
    assert facts.metadata["period_end"] == "2025-12-31"
```

- [ ] **Step 2: Run report adapter tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `turtle.report_adapter`.

- [ ] **Step 3: Implement report adapter**

Create `tradingagents/dataflows/value_investment/turtle/report_adapter.py`:

```python
"""Adapt FinancialReportClient public results into Turtle facts."""

from __future__ import annotations

from typing import Any

from tradingagents.dataflows.financial_reports.adapter import create_financial_report_adapter
from tradingagents.dataflows.financial_reports.config import get_financial_report_client_config
from tradingagents.dataflows.financial_reports.policy import FinancialReportPolicy

from .facts import MoneyAmount, TurtleFactValue, TurtleReportFacts, infer_turtle_period_end


FIELD_ALIASES = {
    "cash_and_equivalents": ("cash_and_equivalents", "money_cap", "cash"),
    "contract_liabilities": ("contract_liabilities", "deferred_revenue"),
    "interest_bearing_debt": ("interest_bearing_debt", "st_borr", "lt_borr", "bond_payable"),
}


def _field_reference(field: Any) -> str:
    page = getattr(field, "evidence_page", None)
    field_id = getattr(field, "field_id", "unknown")
    return f"{field_id} p.{page}" if page is not None else str(field_id)


def _field_unit(field: Any) -> str:
    raw = str(getattr(field, "unit", "") or "").strip().lower()
    if raw in {"yuan", "rmb", "cny"}:
        return "yuan"
    if raw in {"thousand", "rmb'000", "000", "千元"}:
        return "thousand"
    if raw in {"ten_thousand", "万元"}:
        return "ten_thousand"
    if raw in {"million", "百万", "百万元"}:
        return "million"
    if raw in {"hundred_million", "亿元"}:
        return "hundred_million"
    return "yuan"


def _field_currency(field: Any) -> str:
    return str(getattr(field, "currency", None) or "CNY").upper()


def _is_numeric(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _adapt_value(field_id: str, field: Any, source_label: str, reliability: str, caveat: str | None) -> TurtleFactValue:
    raw_value = getattr(field, "value", None)
    if _is_numeric(raw_value):
        value = MoneyAmount(
            value=float(raw_value),
            currency=_field_currency(field),
            unit=_field_unit(field),
            source_label=source_label,
            source_reference=_field_reference(field),
            reliability=reliability,
        )
    else:
        value = raw_value
    return TurtleFactValue(
        name=field_id,
        value=value,
        source_label=source_label,
        source_reference=_field_reference(field),
        reliability=reliability,
        caveat=caveat,
    )


def build_report_facts_from_extraction(
    *,
    extraction: Any | None,
    allow_llm_models: tuple[str, ...],
    adapter_caveats: list[str],
) -> TurtleReportFacts:
    if extraction is None:
        return TurtleReportFacts(fields={}, metadata={}, caveats=list(adapter_caveats))

    policy = FinancialReportPolicy(allow_llm_models=allow_llm_models)
    fields = getattr(extraction, "fields", None) if extraction is not None else None
    source_fields = fields if isinstance(fields, dict) else {}
    adapted: dict[str, TurtleFactValue] = {}
    caveats = list(adapter_caveats)

    for field_id, field in source_fields.items():
        decision = policy.decide(field=field, result=extraction)
        if decision.caveat:
            caveats.append(decision.caveat)
        if not decision.can_compute and not decision.can_display:
            continue
        reliability = "reliable" if decision.can_compute else "display_only"
        adapted[field_id] = _adapt_value(field_id, field, decision.source_label, reliability, decision.caveat)

    metadata = {
        "company": getattr(extraction, "company", None),
        "market": getattr(extraction, "market", None),
        "period_end": getattr(extraction, "period_end", None),
        "catalog_version": getattr(extraction, "catalog_version", None),
    }
    return TurtleReportFacts(fields=adapted, metadata=metadata, caveats=caveats)


def get_turtle_report_facts(
    *,
    ticker: str,
    market: str,
    trade_date: str,
    adapter: Any | None = None,
    allow_llm_models: tuple[str, ...] | None = None,
) -> TurtleReportFacts:
    config = get_financial_report_client_config()
    active_adapter = adapter or create_financial_report_adapter(config)
    normalized_market = "CN" if market == "A" else market
    period_end = infer_turtle_period_end(trade_date)
    result = active_adapter.get_annual_report_data(
        ticker=ticker,
        market=normalized_market,
        period_end=period_end,
        reference_date=trade_date,
    )
    facts = build_report_facts_from_extraction(
        extraction=result.extraction,
        allow_llm_models=allow_llm_models if allow_llm_models is not None else config.allow_llm_models,
        adapter_caveats=result.warnings + result.errors,
    )
    if "period_end" not in facts.metadata or facts.metadata["period_end"] is None:
        metadata = dict(facts.metadata)
        metadata["period_end"] = period_end
        return TurtleReportFacts(fields=facts.fields, metadata=metadata, caveats=facts.caveats)
    return facts
```

Modify `tradingagents/dataflows/value_investment/turtle/__init__.py`:

```python
from .report_adapter import build_report_facts_from_extraction, get_turtle_report_facts

__all__.extend([
    "build_report_facts_from_extraction",
    "get_turtle_report_facts",
])
```

- [ ] **Step 4: Run report adapter tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_report_adapter.py tests/unit/test_turtle_facts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add tradingagents/dataflows/value_investment/turtle/__init__.py tradingagents/dataflows/value_investment/turtle/report_adapter.py tests/unit/test_turtle_report_adapter.py
git commit -m "feat: adapt financial reports for turtle facts"
```

## Task 3: Market Adapter And Holding-Channel Defaults

**Files:**
- Create: `tests/unit/test_turtle_market_adapter.py`
- Create: `tradingagents/dataflows/value_investment/turtle/market_adapter.py`
- Modify: `tradingagents/dataflows/value_investment/turtle/__init__.py`

- [ ] **Step 1: Write failing market adapter tests**

Create `tests/unit/test_turtle_market_adapter.py`:

```python
from tradingagents.dataflows.value_investment.turtle.market_adapter import (
    build_market_facts,
    default_tax_rate,
)


def test_default_tax_rate_by_holding_channel():
    assert default_tax_rate("A", "long_term_domestic") == 0.0
    assert default_tax_rate("HK", "stock_connect") == 0.20
    assert default_tax_rate("HK", "direct_h_share") == 0.28
    assert default_tax_rate("US", "w8ben") == 0.10


def test_build_market_facts_marks_missing_market_cap_as_caveat():
    facts = build_market_facts(
        ticker="600519",
        market="A",
        holding_channel="long_term_domestic",
        market_data={"close_price": 1500.0},
        dividend_data={"avg_payout_ratio_3y": 0.5, "records": []},
        buyback_data={"total_cancelled_amount": 0, "records": []},
        industry="白酒",
        rf_rate=0.025,
    )

    assert "market_cap missing" in " ".join(facts.caveats)
    assert facts.fields["tax_rate"].value == 0.0


def test_build_market_facts_preserves_buyback_missing_instead_of_zero_when_unverified():
    facts = build_market_facts(
        ticker="00001",
        market="HK",
        holding_channel="stock_connect",
        market_data={"market_cap": 200_000_000_000, "close_price": 40.0},
        dividend_data={"avg_payout_ratio_3y": 0.45, "records": []},
        buyback_data=None,
        industry="综合企业",
        rf_rate=0.025,
    )

    assert "buyback data missing" in " ".join(facts.caveats)
    assert "buyback_amount" not in facts.fields
```

- [ ] **Step 2: Run market adapter tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `turtle.market_adapter`.

- [ ] **Step 3: Implement market adapter**

Create `tradingagents/dataflows/value_investment/turtle/market_adapter.py`:

```python
"""Adapt market, dividend, and buyback inputs into Turtle market facts."""

from __future__ import annotations

import os
from typing import Any

from .facts import MoneyAmount, TurtleFactValue, TurtleMarketFacts, default_holding_channel


def default_tax_rate(market: str, holding_channel: str) -> float:
    normalized_market = market.upper()
    normalized_channel = holding_channel.lower()
    if normalized_market in {"A", "CN"} and normalized_channel == "long_term_domestic":
        return 0.0
    if normalized_market == "HK" and normalized_channel == "stock_connect":
        return 0.20
    if normalized_market == "HK" and normalized_channel == "direct_h_share":
        return 0.28
    if normalized_market == "HK":
        return 0.20
    if normalized_market == "US" and normalized_channel == "w8ben":
        return 0.10
    return 0.0


def _money_field(name: str, value: Any, currency: str, source_reference: str) -> TurtleFactValue | None:
    if value is None:
        return None
    return TurtleFactValue(
        name=name,
        value=MoneyAmount(
            value=float(value),
            currency=currency,
            unit="yuan",
            source_label="market-adapter",
            source_reference=source_reference,
        ),
        source_label="market-adapter",
        source_reference=source_reference,
    )


def _number_field(name: str, value: Any, source_reference: str) -> TurtleFactValue | None:
    if value is None:
        return None
    return TurtleFactValue(
        name=name,
        value=float(value),
        source_label="market-adapter",
        source_reference=source_reference,
    )


def _env_rf_rate(market: str) -> float | None:
    names = ["TURTLE_RF_RATE_HK"] if market.upper() == "HK" else ["TURTLE_RF_RATE_CN", "TURTLE_RF_RATE_A"]
    for name in names:
        raw = os.getenv(name)
        if raw:
            return float(raw)
    return None


def build_market_facts(
    *,
    ticker: str,
    market: str,
    holding_channel: str | None,
    market_data: dict[str, Any] | None,
    dividend_data: dict[str, Any] | None,
    buyback_data: dict[str, Any] | None,
    industry: str | None,
    rf_rate: float | None = None,
) -> TurtleMarketFacts:
    active_market_data = market_data or {}
    active_channel = holding_channel or default_holding_channel(market)
    currency = "HKD" if market.upper() == "HK" else "CNY"
    fields: dict[str, TurtleFactValue] = {}
    caveats: list[str] = []

    market_cap_field = _money_field("market_cap", active_market_data.get("market_cap"), currency, "market_data.market_cap")
    if market_cap_field is None:
        caveats.append("market_cap missing")
    else:
        fields["market_cap"] = market_cap_field

    close_field = _number_field("close_price", active_market_data.get("close_price"), "market_data.close_price")
    if close_field is not None:
        fields["close_price"] = close_field

    fields["tax_rate"] = TurtleFactValue(
        name="tax_rate",
        value=default_tax_rate(market, active_channel),
        source_label="holding-channel-default",
        source_reference=f"{market}:{active_channel}",
        caveat="default holding channel used until UI/API exposes channel selection",
    )
    fields["holding_channel"] = TurtleFactValue(
        name="holding_channel",
        value=active_channel,
        source_label="holding-channel-default",
        source_reference=f"{market}:{active_channel}",
    )

    active_rf = rf_rate if rf_rate is not None else _env_rf_rate(market)
    rf_field = _number_field("rf_rate", active_rf, "risk_free_rate")
    if rf_field is None:
        caveats.append("rf_rate missing")
    else:
        fields["rf_rate"] = rf_field

    if industry:
        fields["industry"] = TurtleFactValue("industry", industry, "market-adapter", "industry")

    if dividend_data:
        ratio = dividend_data.get("avg_payout_ratio_3y")
        ratio_field = _number_field("avg_payout_ratio_3y", ratio, "dividend_data.avg_payout_ratio_3y")
        if ratio_field is not None:
            fields["avg_payout_ratio_3y"] = ratio_field
        fields["dividend_records"] = TurtleFactValue(
            "dividend_records",
            dividend_data.get("records", []),
            "market-adapter",
            "dividend_data.records",
        )
    else:
        caveats.append("dividend data missing")

    if buyback_data is None:
        caveats.append("buyback data missing")
    else:
        amount = buyback_data.get("total_cancelled_amount")
        buyback_field = _money_field("buyback_amount", amount, currency, "buyback_data.total_cancelled_amount")
        if buyback_field is not None:
            fields["buyback_amount"] = buyback_field

    return TurtleMarketFacts(fields=fields, caveats=caveats)


def get_turtle_market_facts(
    *,
    ticker: str,
    market: str,
    holding_channel: str | None,
) -> TurtleMarketFacts:
    from tradingagents.tools.value_investment_tool import (
        _fetch_buyback_data_sync,
        _fetch_dividend_data_sync,
        _fetch_market_data_structured,
        _get_industry_dynamic,
    )

    return build_market_facts(
        ticker=ticker,
        market=market,
        holding_channel=holding_channel,
        market_data=_fetch_market_data_structured(ticker, market),
        dividend_data=_fetch_dividend_data_sync(ticker, market),
        buyback_data=_fetch_buyback_data_sync(ticker, market),
        industry=_get_industry_dynamic(ticker, market),
    )
```

Modify `tradingagents/dataflows/value_investment/turtle/__init__.py`:

```python
from .market_adapter import build_market_facts, default_tax_rate, get_turtle_market_facts

__all__.extend([
    "build_market_facts",
    "default_tax_rate",
    "get_turtle_market_facts",
])
```

- [ ] **Step 4: Run market adapter tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_market_adapter.py tests/unit/test_turtle_facts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add tradingagents/dataflows/value_investment/turtle/__init__.py tradingagents/dataflows/value_investment/turtle/market_adapter.py tests/unit/test_turtle_market_adapter.py
git commit -m "feat: add turtle market adapter"
```

## Task 4: Deterministic Turtle Calculations

**Files:**
- Create: `tests/unit/test_turtle_calculations.py`
- Create: `tradingagents/dataflows/value_investment/turtle/calculations.py`
- Modify: `tradingagents/dataflows/value_investment/turtle/__init__.py`

- [ ] **Step 1: Write failing calculation tests**

Create `tests/unit/test_turtle_calculations.py`:

```python
from tradingagents.dataflows.value_investment.turtle.calculations import compute_turtle_signals
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleFactValue,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
)


def money(name, value, source):
    return TurtleFactValue(
        name=name,
        value=MoneyAmount(value, "CNY", "hundred_million", "fixture", source),
        source_label="fixture",
        source_reference=source,
    )


def number(name, value, source):
    return TurtleFactValue(name=name, value=value, source_label="fixture", source_reference=source)


def base_facts():
    context = TurtleRunContext.for_ticker(
        ticker="600519",
        market="A",
        trade_date="2026-05-19",
        company_name="贵州茅台",
    )
    report = TurtleReportFacts(fields={
        "net_profit": money("net_profit", 100, "report.net_profit"),
        "operating_cash_flow": money("operating_cash_flow", 120, "report.ocf"),
        "capex": money("capex", 20, "report.capex"),
        "cash": money("cash", 500, "report.cash"),
        "interest_bearing_debt": money("interest_bearing_debt", 50, "report.debt"),
    })
    market = TurtleMarketFacts(fields={
        "market_cap": money("market_cap", 1000, "market.market_cap"),
        "buyback_amount": money("buyback_amount", 10, "market.buyback"),
        "avg_payout_ratio_3y": number("avg_payout_ratio_3y", 0.5, "market.payout"),
        "tax_rate": number("tax_rate", 0.2, "market.tax"),
        "rf_rate": number("rf_rate", 0.03, "market.rf"),
    })
    return TurtleFacts(context=context, report=report, market=market, status="complete")


def test_compute_turtle_signals_calculates_r_gg_hh():
    signals = compute_turtle_signals(base_facts())

    assert signals.status == "complete"
    assert signals.results["payout_anchor"].value == 0.5
    assert signals.results["R"].value == 5.0
    assert signals.results["GG"].value == 5.0
    assert signals.results["HH"].value == 0.0
    assert "100 * 0.5 * (1 - 0.2) + 10" in signals.results["R"].substitution


def test_compute_turtle_signals_switches_to_ev_when_cash_is_large():
    signals = compute_turtle_signals(base_facts())

    assert signals.results["net_cash_ratio"].value == 45.0
    assert signals.results["ev_switch"].value == 1.0
    assert signals.results["cash_protection"].value == 40.0


def test_compute_turtle_signals_is_non_decisionable_without_market_cap():
    facts = base_facts()
    market = TurtleMarketFacts(fields={key: value for key, value in facts.market.fields.items() if key != "market_cap"})
    broken = TurtleFacts(context=facts.context, report=facts.report, market=market, status="complete")

    signals = compute_turtle_signals(broken)

    assert signals.status == "non_decisionable"
    assert "market_cap" in signals.results["R"].missing_inputs
```

- [ ] **Step 2: Run calculation tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `turtle.calculations`.

- [ ] **Step 3: Implement calculations**

Create `tradingagents/dataflows/value_investment/turtle/calculations.py`:

```python
"""Deterministic Turtle v0.15 calculation helpers."""

from __future__ import annotations

from .facts import FormulaResult, MoneyAmount, TurtleComputedSignals, TurtleFactValue, TurtleFacts


def _field(facts: TurtleFacts, key: str) -> TurtleFactValue | None:
    return facts.report.fields.get(key) or facts.market.fields.get(key)


def _money_hm(facts: TurtleFacts, key: str) -> tuple[float | None, str | None]:
    field = _field(facts, key)
    if field is None or not isinstance(field.value, MoneyAmount):
        return None, None
    try:
        normalized = field.value.to_hundred_million()
    except ValueError:
        return None, field.source_reference
    return normalized.value, field.source_reference


def _number(facts: TurtleFacts, key: str) -> tuple[float | None, str | None]:
    field = _field(facts, key)
    if field is None:
        return None, None
    try:
        return float(field.value), field.source_reference
    except (TypeError, ValueError):
        return None, field.source_reference


def _missing(items: dict[str, object]) -> list[str]:
    return [key for key, value in items.items() if value is None]


def _result(
    *,
    name: str,
    formula: str,
    substitution: str,
    value: float | None,
    unit: str,
    sources: list[str],
    missing_inputs: list[str],
) -> FormulaResult:
    status = "complete" if not missing_inputs else "non_decisionable"
    return FormulaResult(
        name=name,
        formula=formula,
        substitution=substitution,
        value=value,
        unit=unit,
        sources=[source for source in sources if source],
        missing_inputs=missing_inputs,
        status=status,
    )


def compute_turtle_signals(facts: TurtleFacts) -> TurtleComputedSignals:
    results: dict[str, FormulaResult] = {}
    caveats = list(facts.caveats) + list(facts.report.caveats) + list(facts.market.caveats)

    net_profit, net_profit_src = _money_hm(facts, "net_profit")
    ocf, ocf_src = _money_hm(facts, "operating_cash_flow")
    capex, capex_src = _money_hm(facts, "capex")
    market_cap, market_cap_src = _money_hm(facts, "market_cap")
    buyback, buyback_src = _money_hm(facts, "buyback_amount")
    cash, cash_src = _money_hm(facts, "cash")
    debt, debt_src = _money_hm(facts, "interest_bearing_debt")
    payout_anchor, payout_src = _number(facts, "avg_payout_ratio_3y")
    tax_rate, tax_src = _number(facts, "tax_rate")

    buyback = 0.0 if buyback is None and "buyback data missing" not in caveats else buyback
    tax_rate = 0.0 if tax_rate is None else tax_rate

    payout_missing = _missing({"avg_payout_ratio_3y": payout_anchor})
    results["payout_anchor"] = _result(
        name="payout_anchor",
        formula="M = avg_payout_ratio_3y",
        substitution=f"M = {payout_anchor}",
        value=payout_anchor,
        unit="ratio",
        sources=[payout_src],
        missing_inputs=payout_missing,
    )

    owner_earnings = None
    owner_missing = _missing({"operating_cash_flow": ocf, "capex": capex})
    if not owner_missing:
        owner_earnings = ocf - abs(capex)
    results["owner_earnings"] = _result(
        name="owner_earnings",
        formula="owner_earnings = OCF - abs(Capex)",
        substitution=f"owner_earnings = {ocf} - abs({capex})",
        value=owner_earnings,
        unit="CNY hundred_million",
        sources=[ocf_src, capex_src],
        missing_inputs=owner_missing,
    )

    r_missing = _missing({
        "net_profit": net_profit,
        "avg_payout_ratio_3y": payout_anchor,
        "market_cap": market_cap,
    })
    r_value = None
    if not r_missing:
        r_value = ((net_profit * payout_anchor * (1 - tax_rate) + (buyback or 0.0)) / market_cap) * 100
    results["R"] = _result(
        name="R",
        formula="R = (net_profit * M * (1 - Q) + buyback) / market_cap",
        substitution=f"R = ({net_profit} * {payout_anchor} * (1 - {tax_rate}) + {buyback or 0.0}) / {market_cap}",
        value=round(r_value, 4) if r_value is not None else None,
        unit="percent",
        sources=[net_profit_src, payout_src, tax_src, buyback_src, market_cap_src],
        missing_inputs=r_missing,
    )

    gg_missing = _missing({
        "owner_earnings": owner_earnings,
        "avg_payout_ratio_3y": payout_anchor,
        "market_cap": market_cap,
    })
    gg_value = None
    if not gg_missing:
        gg_value = ((owner_earnings * payout_anchor * (1 - tax_rate) + (buyback or 0.0)) / market_cap) * 100
    results["GG"] = _result(
        name="GG",
        formula="GG = (owner_earnings * M * (1 - Q) + buyback) / market_cap",
        substitution=f"GG = ({owner_earnings} * {payout_anchor} * (1 - {tax_rate}) + {buyback or 0.0}) / {market_cap}",
        value=round(gg_value, 4) if gg_value is not None else None,
        unit="percent",
        sources=[ocf_src, capex_src, payout_src, tax_src, buyback_src, market_cap_src],
        missing_inputs=gg_missing,
    )

    hh_missing = _missing({"R": r_value, "GG": gg_value})
    hh_value = None if hh_missing else r_value - gg_value
    results["HH"] = _result(
        name="HH",
        formula="HH = R - GG",
        substitution=f"HH = {r_value} - {gg_value}",
        value=round(hh_value, 4) if hh_value is not None else None,
        unit="percentage_points",
        sources=[],
        missing_inputs=hh_missing,
    )

    net_cash = None
    net_cash_missing = _missing({"cash": cash, "interest_bearing_debt": debt})
    if not net_cash_missing:
        net_cash = cash - debt
    ratio_missing = _missing({"net_cash": net_cash, "market_cap": market_cap})
    net_cash_ratio = None if ratio_missing else net_cash / market_cap * 100
    results["net_cash_ratio"] = _result(
        name="net_cash_ratio",
        formula="net_cash_ratio = (cash - debt) / market_cap",
        substitution=f"net_cash_ratio = ({cash} - {debt}) / {market_cap}",
        value=round(net_cash_ratio, 4) if net_cash_ratio is not None else None,
        unit="percent",
        sources=[cash_src, debt_src, market_cap_src],
        missing_inputs=ratio_missing,
    )

    ev_switch = 1.0 if net_cash_ratio is not None and net_cash_ratio > 40 else 0.0
    results["ev_switch"] = FormulaResult(
        name="ev_switch",
        formula="ev_switch = 1 when net_cash_ratio > 40%",
        substitution=f"ev_switch = {net_cash_ratio} > 40",
        value=ev_switch,
        unit="boolean",
        sources=[cash_src, debt_src, market_cap_src],
        missing_inputs=ratio_missing,
        status="complete" if not ratio_missing else "degraded",
    )

    cash_protection = 0.0
    if net_cash_ratio is None:
        cash_protection = None
    elif net_cash_ratio < 20:
        cash_protection = 30.0
    elif net_cash_ratio < 40:
        cash_protection = 25.0
    elif net_cash_ratio < 60:
        cash_protection = 20.0
    else:
        cash_protection = 15.0
    results["cash_protection"] = FormulaResult(
        name="cash_protection",
        formula="safety_margin_discount by net cash / market cap",
        substitution=f"net_cash_ratio = {net_cash_ratio}",
        value=cash_protection,
        unit="target_discount_percent",
        sources=[cash_src, debt_src, market_cap_src],
        missing_inputs=ratio_missing,
        status="complete" if not ratio_missing else "degraded",
    )

    critical_missing = set(results["R"].missing_inputs + results["GG"].missing_inputs)
    status = "non_decisionable" if critical_missing else "complete"
    if facts.status == "unsupported":
        status = "unsupported"
    elif caveats and status == "complete":
        status = "degraded"

    return TurtleComputedSignals(status=status, results=results, caveats=caveats)
```

Modify `tradingagents/dataflows/value_investment/turtle/__init__.py`:

```python
from .calculations import compute_turtle_signals

__all__.append("compute_turtle_signals")
```

- [ ] **Step 4: Run calculation tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_calculations.py tests/unit/test_turtle_facts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add tradingagents/dataflows/value_investment/turtle/__init__.py tradingagents/dataflows/value_investment/turtle/calculations.py tests/unit/test_turtle_calculations.py
git commit -m "feat: add turtle deterministic calculations"
```

## Task 5: Decision Prompt And Non-Decisionable Formatting

**Files:**
- Create: `tests/unit/test_turtle_decision.py`
- Create: `tradingagents/dataflows/value_investment/turtle/formatting.py`
- Create: `tradingagents/dataflows/value_investment/turtle/decision.py`
- Modify: `tradingagents/dataflows/value_investment/turtle/__init__.py`

- [ ] **Step 1: Write failing decision tests**

Create `tests/unit/test_turtle_decision.py`:

```python
from tradingagents.dataflows.value_investment.turtle.decision import (
    build_non_decisionable_report,
    build_turtle_decision_prompt,
)
from tradingagents.dataflows.value_investment.turtle.facts import (
    FormulaResult,
    TurtleComputedSignals,
    TurtleFacts,
    TurtleMarketFacts,
    TurtleReportFacts,
    TurtleRunContext,
)


def empty_facts(status="complete"):
    context = TurtleRunContext.for_ticker(
        ticker="600519",
        market="A",
        trade_date="2026-05-19",
        company_name="贵州茅台",
    )
    return TurtleFacts(context=context, report=TurtleReportFacts(), market=TurtleMarketFacts(), status=status)


def test_decision_prompt_forbids_external_tools_and_contains_formulas():
    facts = empty_facts()
    signals = TurtleComputedSignals(status="complete", results={
        "R": FormulaResult("R", "R = profit / market_cap", "R = 10 / 100", 10.0, "percent", ["fixture"])
    })

    prompt = build_turtle_decision_prompt(facts=facts, signals=signals)

    assert "禁止调用任何外部工具" in prompt
    assert "R = 10 / 100" in prompt
    assert "不得编造缺失数据" in prompt


def test_non_decisionable_report_has_no_investable_hold_or_avoid_recommendation():
    facts = empty_facts(status="non_decisionable")
    signals = TurtleComputedSignals(
        status="non_decisionable",
        results={
            "R": FormulaResult(
                "R",
                "R = profit / market_cap",
                "R = None / None",
                None,
                "percent",
                [],
                missing_inputs=["market_cap"],
                status="non_decisionable",
            )
        },
        caveats=["market_cap missing"],
    )

    report = build_non_decisionable_report(facts=facts, signals=signals)

    assert "不可决策" in report
    assert "market_cap" in report
    assert "买入" not in report
    assert "持有" not in report
    assert "卖出" not in report
```

- [ ] **Step 2: Run decision tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_decision.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `turtle.decision`.

- [ ] **Step 3: Implement formatting and decision helpers**

Create `tradingagents/dataflows/value_investment/turtle/formatting.py`:

```python
"""Formatting helpers for Turtle facts and computed signals."""

from __future__ import annotations

import json

from .facts import TurtleComputedSignals, TurtleFacts


def facts_to_markdown(facts: TurtleFacts) -> str:
    return "```json\n" + json.dumps(facts.to_dict(), ensure_ascii=False, indent=2) + "\n```"


def signals_to_markdown(signals: TurtleComputedSignals) -> str:
    return "```json\n" + json.dumps(signals.to_dict(), ensure_ascii=False, indent=2) + "\n```"
```

Create `tradingagents/dataflows/value_investment/turtle/decision.py`:

```python
"""Decision prompt construction for Turtle v0.15 reports."""

from __future__ import annotations

from .facts import TurtleComputedSignals, TurtleFacts
from .formatting import facts_to_markdown, signals_to_markdown


def build_turtle_decision_prompt(*, facts: TurtleFacts, signals: TurtleComputedSignals) -> str:
    return f"""
你是一名龟龟投资 v0.15 最终决策分析师。

硬性规则：
- 禁止调用任何外部工具。
- 不得编造缺失数据。
- 只能使用下方 TurtleFacts 和 TurtleComputedSignals。
- 所有关键数值必须引用已提供的来源和公式代入。
- 如果 signals.status 为 non_decisionable，只能输出不可决策报告。
- 如果存在 veto_reasons，必须停止后续投资结论。

TurtleFacts:
{facts_to_markdown(facts)}

TurtleComputedSignals:
{signals_to_markdown(signals)}

输出结构：
1. 结论状态：complete / degraded / non_decisionable / unsupported
2. 数据完整性和 caveats
3. 因子1A 基础否决
4. 因子2 粗算 R
5. 因子3 精算 GG 和 HH = R - GG
6. EV 口径、现金保护、安全边际
7. 最终判断
"""


def build_non_decisionable_report(*, facts: TurtleFacts, signals: TurtleComputedSignals) -> str:
    missing = []
    for result in signals.results.values():
        missing.extend(result.missing_inputs)
    missing_text = ", ".join(sorted(set(missing))) or "关键输入缺失"
    caveats = "\n".join(f"- {item}" for item in signals.caveats + facts.caveats) or "- 无额外 caveat"
    return f"""# 龟龟投资 v0.15 分析：不可决策

标的：{facts.context.company_name}（{facts.context.ticker}）
状态：non_decisionable

## 缺失关键输入

{missing_text}

## Caveats

{caveats}

## 说明

本次 Turtle 流程未形成可执行投资结论。为避免绕过 Turtle 门槛，本报告不输出买入、持有或卖出建议。
"""
```

Modify `tradingagents/dataflows/value_investment/turtle/__init__.py`:

```python
from .decision import build_non_decisionable_report, build_turtle_decision_prompt

__all__.extend([
    "build_non_decisionable_report",
    "build_turtle_decision_prompt",
])
```

- [ ] **Step 4: Run decision tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_decision.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add tradingagents/dataflows/value_investment/turtle/__init__.py tradingagents/dataflows/value_investment/turtle/decision.py tradingagents/dataflows/value_investment/turtle/formatting.py tests/unit/test_turtle_decision.py
git commit -m "feat: add turtle decision prompt builder"
```

## Task 6: Tool Entry And Value Analyst Integration

**Files:**
- Create: `tests/unit/test_turtle_value_analyst_integration.py`
- Create: `tradingagents/tools/turtle_analysis_tool.py`
- Modify: `tradingagents/agents/utils/agent_utils.py`
- Modify: `tradingagents/graph/trading_graph.py`
- Modify: `tradingagents/agents/analysts/value_analyst.py`
- Test: `tests/unit/test_value_analyst_entry.py`

- [ ] **Step 1: Write failing integration tests**

Create `tests/unit/test_turtle_value_analyst_integration.py`:

```python
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from tradingagents.agents.analysts.value_analyst import create_value_analyst
from tradingagents.tools.turtle_analysis_tool import prepare_turtle_analysis_payload


class FakePlainLLM:
    def __init__(self):
        self.bound_tools = None
        self.plain_invocations = 0

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, payload):
        self.plain_invocations += 1
        return AIMessage(content="龟龟投资 v0.15 最终报告\nR = 5.0\nGG = 5.0\nHH = 0.0")


def test_prepare_turtle_analysis_payload_returns_json(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.tools.turtle_analysis_tool.get_turtle_report_facts",
        lambda **kwargs: SimpleNamespace(fields={}, metadata={}, caveats=[], to_dict=lambda: {"fields": {}, "metadata": {}, "caveats": []}),
    )
    monkeypatch.setattr(
        "tradingagents.tools.turtle_analysis_tool.get_turtle_market_facts",
        lambda **kwargs: SimpleNamespace(fields={}, caveats=["market_cap missing"], to_dict=lambda: {"fields": {}, "caveats": ["market_cap missing"]}),
    )

    payload = json.loads(prepare_turtle_analysis_payload("600519", "A", "2026-05-19", "贵州茅台"))

    assert payload["facts"]["status"] == "complete"
    assert payload["signals"]["status"] == "non_decisionable"


def test_value_analyst_final_report_after_tool_message_uses_plain_llm(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.agents.analysts.value_analyst._get_company_name",
        lambda ticker, market_info: "贵州茅台",
    )
    llm = FakePlainLLM()
    node = create_value_analyst(llm, SimpleNamespace(prepare_turtle_analysis=lambda *args, **kwargs: "unused"))
    payload = {
        "facts": {
            "context": {
                "ticker": "600519",
                "market": "A",
                "trade_date": "2026-05-19",
                "period_end": "2025-12-31",
                "holding_channel": "long_term_domestic",
                "company_name": "贵州茅台",
            },
            "report": {"fields": {}, "metadata": {}, "caveats": []},
            "market": {"fields": {}, "caveats": []},
            "status": "complete",
            "caveats": [],
        },
        "signals": {
            "status": "complete",
            "results": {
                "R": {
                    "name": "R",
                    "formula": "R = profit / market_cap",
                    "substitution": "R = 10 / 100",
                    "value": 10,
                    "unit": "percent",
                    "sources": [],
                    "missing_inputs": [],
                    "status": "complete",
                }
            },
            "veto_reasons": [],
            "caveats": [],
        },
    }

    result = node({
        "messages": [
            ToolMessage(
                content=json.dumps(payload, ensure_ascii=False),
                name="prepare_turtle_analysis",
                tool_call_id="call_turtle",
            )
        ],
        "trade_date": "2026-05-19",
        "company_of_interest": "600519",
        "value_tool_call_count": 1,
    })

    assert "value_report" in result
    assert "龟龟投资 v0.15" in result["value_report"]
    assert llm.bound_tools is None
    assert llm.plain_invocations == 1
```

- [ ] **Step 2: Run integration tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `tradingagents.tools.turtle_analysis_tool`.

- [ ] **Step 3: Add Turtle tool entry**

Create `tradingagents/tools/turtle_analysis_tool.py`:

```python
"""Tool entry for preparing Turtle v0.15 facts and deterministic signals."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from tradingagents.dataflows.value_investment.turtle.calculations import compute_turtle_signals
from tradingagents.dataflows.value_investment.turtle.facts import TurtleFacts, TurtleRunContext
from tradingagents.dataflows.value_investment.turtle.market_adapter import get_turtle_market_facts
from tradingagents.dataflows.value_investment.turtle.report_adapter import get_turtle_report_facts


def prepare_turtle_analysis_payload(
    ticker: str,
    market: str,
    trade_date: str,
    company_name: str,
    holding_channel: str | None = None,
) -> str:
    context = TurtleRunContext.for_ticker(
        ticker=ticker,
        market=market,
        trade_date=trade_date,
        company_name=company_name,
        holding_channel=holding_channel,
    )
    report_facts = get_turtle_report_facts(
        ticker=ticker,
        market=market,
        trade_date=trade_date,
    )
    market_facts = get_turtle_market_facts(
        ticker=ticker,
        market=market,
        holding_channel=context.holding_channel,
    )
    facts = TurtleFacts(
        context=context,
        report=report_facts,
        market=market_facts,
        status="complete",
        caveats=report_facts.caveats + market_facts.caveats,
    )
    signals = compute_turtle_signals(facts)
    return json.dumps({"facts": facts.to_dict(), "signals": signals.to_dict()}, ensure_ascii=False)


@tool
def prepare_turtle_analysis(
    ticker: str,
    market: str = "A",
    trade_date: str = "",
    company_name: str = "",
    holding_channel: str | None = None,
) -> str:
    """Prepare Turtle v0.15 facts and deterministic signals for the value analyst."""
    return prepare_turtle_analysis_payload(
        ticker=ticker,
        market=market,
        trade_date=trade_date,
        company_name=company_name or ticker,
        holding_channel=holding_channel,
    )
```

- [ ] **Step 4: Expose the Turtle tool through Toolkit and graph**

In `tradingagents/agents/utils/agent_utils.py`, add this method near `get_value_investment_analysis`:

```python
    @staticmethod
    @tool
    @log_tool_call(tool_name="prepare_turtle_analysis", log_args=True)
    def prepare_turtle_analysis(
        ticker: Annotated[str, "股票代码（支持A股、港股）"],
        market: Annotated[str, "市场类型：A=A股, HK=港股"] = "A",
        trade_date: Annotated[str, "交易日期，格式 yyyy-mm-dd"] = "",
        company_name: Annotated[str, "公司名称"] = "",
        holding_channel: Annotated[str | None, "持股渠道"] = None,
    ) -> str:
        """准备龟龟投资 v0.15 facts 和确定性计算结果。"""
        from tradingagents.tools.turtle_analysis_tool import prepare_turtle_analysis as _prepare_turtle_analysis

        return _prepare_turtle_analysis.invoke({
            "ticker": ticker,
            "market": market,
            "trade_date": trade_date,
            "company_name": company_name,
            "holding_channel": holding_channel,
        })
```

In `tradingagents/graph/trading_graph.py`, change the value tool node from:

```python
self.toolkit.get_value_investment_analysis,
```

to:

```python
self.toolkit.prepare_turtle_analysis,
```

- [ ] **Step 5: Modify value analyst final generation boundary**

In `tradingagents/agents/analysts/value_analyst.py`, update the tool selection from:

```python
tools = [toolkit.get_value_investment_analysis]
```

to:

```python
tools = [toolkit.prepare_turtle_analysis]
```

Add helper functions near `_get_company_name`:

```python
def _latest_turtle_tool_payload(messages):
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and getattr(message, "name", "") == "prepare_turtle_analysis":
            return message.content
    return None


def _plain_turtle_report_prompt(company_name: str, ticker: str, payload: str) -> str:
    from tradingagents.dataflows.value_investment.turtle.decision import build_turtle_decision_prompt
    from tradingagents.dataflows.value_investment.turtle.facts import (
        FormulaResult,
        TurtleComputedSignals,
        TurtleFacts,
        TurtleMarketFacts,
        TurtleReportFacts,
        TurtleRunContext,
    )
    import json

    data = json.loads(payload)
    context = TurtleRunContext(**data["facts"]["context"])
    facts = TurtleFacts(
        context=context,
        report=TurtleReportFacts(
            fields={},
            metadata=data["facts"].get("report", {}).get("metadata", {}),
            caveats=data["facts"].get("report", {}).get("caveats", []),
        ),
        market=TurtleMarketFacts(caveats=data["facts"].get("market", {}).get("caveats", [])),
        status=data["facts"].get("status", "degraded"),
        caveats=data["facts"].get("caveats", []),
    )
    results = {
        key: FormulaResult(**value)
        for key, value in data["signals"].get("results", {}).items()
    }
    signals = TurtleComputedSignals(
        status=data["signals"].get("status", "degraded"),
        results=results,
        veto_reasons=data["signals"].get("veto_reasons", []),
        caveats=data["signals"].get("caveats", []),
    )
    return build_turtle_decision_prompt(facts=facts, signals=signals)
```

At the start of `value_analyst_node`, after `ticker` and `company_name` are available, add:

```python
        turtle_payload = _latest_turtle_tool_payload(messages)
        if turtle_payload:
            try:
                prompt_text = _plain_turtle_report_prompt(company_name, ticker, turtle_payload)
                result = llm.invoke(prompt_text)
                report_content = result.content if hasattr(result, "content") else str(result)
                return {
                    "value_report": report_content,
                    "value_tool_call_count": tool_call_count,
                }
            except Exception as e:
                logger.error(f"❌ [价值投资分析师] Turtle 最终报告生成失败: {e}")
                return {
                    "value_report": f"龟龟投资分析失败: {str(e)}",
                    "value_tool_call_count": tool_call_count,
                }
```

Update the system message references from `get_value_investment_analysis` to `prepare_turtle_analysis` and pass `trade_date=current_date`, `company_name=company_name` in the required tool-call instructions.

- [ ] **Step 6: Run integration and existing value entry tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_turtle_value_analyst_integration.py tests/unit/test_value_analyst_entry.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add tradingagents/tools/turtle_analysis_tool.py tradingagents/agents/utils/agent_utils.py tradingagents/graph/trading_graph.py tradingagents/agents/analysts/value_analyst.py tests/unit/test_turtle_value_analyst_integration.py
git commit -m "feat: wire turtle preparation into value analyst"
```

## Task 7: Smoke Script And Full Regression

**Files:**
- Create: `scripts/smoke_test_turtle_value.py`
- Test: all Turtle and value entry unit tests

- [ ] **Step 1: Add local smoke script**

Create `scripts/smoke_test_turtle_value.py`:

```python
#!/usr/bin/env python3
"""Local smoke test for Turtle v0.15 value flow.

This script is intentionally environment-driven. It can use real PDFs from a local
financial-report-llm-extractor checkout through FINANCIAL_REPORT_PDF_ROOT, but the
normal unit tests do not depend on those absolute paths.
"""

from __future__ import annotations

import argparse
import json

from tradingagents.tools.turtle_analysis_tool import prepare_turtle_analysis_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="600519")
    parser.add_argument("--market", default="A")
    parser.add_argument("--trade-date", default="2026-05-19")
    parser.add_argument("--company-name", default="贵州茅台")
    args = parser.parse_args()

    payload = json.loads(prepare_turtle_analysis_payload(
        ticker=args.ticker,
        market=args.market,
        trade_date=args.trade_date,
        company_name=args.company_name,
    ))
    print(json.dumps({
        "facts_status": payload["facts"]["status"],
        "signals_status": payload["signals"]["status"],
        "available_results": sorted(payload["signals"]["results"].keys()),
        "caveats": payload["facts"].get("caveats", []) + payload["signals"].get("caveats", []),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Make the script executable**

Run:

```bash
chmod +x scripts/smoke_test_turtle_value.py
```

Expected: command succeeds.

- [ ] **Step 3: Run focused unit regression**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_turtle_facts.py \
  tests/unit/test_turtle_report_adapter.py \
  tests/unit/test_turtle_market_adapter.py \
  tests/unit/test_turtle_calculations.py \
  tests/unit/test_turtle_decision.py \
  tests/unit/test_turtle_value_analyst_integration.py \
  tests/unit/test_value_analyst_entry.py \
  tests/unit/test_financial_report_integration.py \
  -q
```

Expected: PASS.

- [ ] **Step 4: Run local smoke without real PDF dependency**

Run:

```bash
FINANCIAL_REPORT_CLIENT_ENABLED=false .venv/bin/python scripts/smoke_test_turtle_value.py --ticker 600519 --market A --company-name 贵州茅台
```

Expected: command exits 0 and prints JSON containing `signals_status`.

- [ ] **Step 5: Run local PDF-backed smoke when extractor checkout is available**

Run:

```bash
FINANCIAL_REPORT_PDF_ROOT=/home/like/git/financial-report-llm-extractor/downloads \
FINANCIAL_REPORT_CLIENT_ENABLED=true \
FINANCIAL_REPORT_CACHE_ONLY=false \
.venv/bin/python scripts/smoke_test_turtle_value.py --ticker 600519 --market A --company-name 贵州茅台
```

Expected: command exits 0. `facts_status` is present. If extractor config or LLM supplement is unavailable, the output may be `degraded` or `non_decisionable`, but it must not crash.

- [ ] **Step 6: Commit Task 7**

```bash
git add scripts/smoke_test_turtle_value.py
git commit -m "test: add turtle value smoke script"
```

## Final Verification

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_turtle_facts.py \
  tests/unit/test_turtle_report_adapter.py \
  tests/unit/test_turtle_market_adapter.py \
  tests/unit/test_turtle_calculations.py \
  tests/unit/test_turtle_decision.py \
  tests/unit/test_turtle_value_analyst_integration.py \
  tests/unit/test_value_analyst_entry.py \
  tests/unit/test_financial_report_integration.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run broader value/financial report tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/unit/test_financial_report_adapter.py \
  tests/unit/test_financial_report_mapper.py \
  tests/unit/test_financial_report_policy.py \
  tests/unit/test_financial_report_formatter.py \
  tests/unit/test_financial_report_value_integration.py \
  tests/unit/test_value_report_context.py \
  tests/unit/test_value_analyst_surface.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git status --short
git log --oneline --max-count=8
```

Expected: no unstaged tracked changes. Recent commits show the task commits above.
