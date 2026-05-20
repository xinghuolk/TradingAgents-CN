"""Core Turtle v0.15 fact and calculation data structures."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


MoneyUnit = Literal["yuan", "thousand", "ten_thousand", "million", "hundred_million"]
TurtleStatus = Literal["complete", "degraded", "non_decisionable", "unsupported"]


def _copy_dict(value: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(value)


def _copy_list(value: list[Any]) -> list[Any]:
    return deepcopy(value)


def infer_turtle_period_end(reference_date: str | None) -> str:
    if reference_date is None:
        date_text = datetime.today().strftime("%Y-%m-%d")
    else:
        date_text = reference_date.strip()
        if not date_text:
            raise ValueError("reference_date cannot be blank")

    ref = datetime.strptime(date_text[:10], "%Y-%m-%d")
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
        if self.unit not in multipliers:
            raise ValueError(f"Unsupported money unit: {self.unit}")

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
        return deepcopy(asdict(self))


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
        data = deepcopy(asdict(self))
        if isinstance(self.value, MoneyAmount):
            data["value"] = self.value.to_dict()
        return data


@dataclass(frozen=True)
class TurtleReportFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _copy_dict(self.fields))
        object.__setattr__(self, "metadata", _copy_dict(self.metadata))
        object.__setattr__(self, "caveats", _copy_list(self.caveats))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "metadata": _copy_dict(self.metadata),
            "caveats": _copy_list(self.caveats),
        }


@dataclass(frozen=True)
class TurtleMarketFacts:
    fields: dict[str, TurtleFactValue] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _copy_dict(self.fields))
        object.__setattr__(self, "caveats", _copy_list(self.caveats))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {key: value.to_dict() for key, value in self.fields.items()},
            "caveats": _copy_list(self.caveats),
        }


@dataclass(frozen=True)
class TurtleFacts:
    context: TurtleRunContext
    report: TurtleReportFacts
    market: TurtleMarketFacts
    status: TurtleStatus
    caveats: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "caveats", _copy_list(self.caveats))

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": deepcopy(asdict(self.context)),
            "report": self.report.to_dict(),
            "market": self.market.to_dict(),
            "status": self.status,
            "caveats": _copy_list(self.caveats),
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", _copy_list(self.sources))
        object.__setattr__(self, "missing_inputs", _copy_list(self.missing_inputs))

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))


@dataclass(frozen=True)
class TurtleComputedSignals:
    status: TurtleStatus
    results: dict[str, FormulaResult] = field(default_factory=dict)
    veto_reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", _copy_dict(self.results))
        object.__setattr__(self, "veto_reasons", _copy_list(self.veto_reasons))
        object.__setattr__(self, "caveats", _copy_list(self.caveats))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "results": {key: value.to_dict() for key, value in self.results.items()},
            "veto_reasons": _copy_list(self.veto_reasons),
            "caveats": _copy_list(self.caveats),
        }
