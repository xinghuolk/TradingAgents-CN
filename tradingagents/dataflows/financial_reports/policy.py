"""Reliability policy for FinancialReportClient fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldUseDecision:
    """Decision describing whether a field can feed computation or display."""

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
    confidence = getattr(field, "confidence", "")
    confidence_value = _text(getattr(confidence, "value", confidence)).lower()
    return source == "llm" or confidence_value == "llm_supplement"


def _is_model_allowed(model: str, allow_llm_models: tuple[str, ...]) -> bool:
    normalized = model.lower()
    return any(token.lower() in normalized for token in allow_llm_models)


def field_source_label(field: Any, result: Any) -> str:
    if getattr(field, "value", None) is None or not bool(getattr(field, "is_present", True)):
        return "financial-report-client:unavailable"

    if _is_llm_field(field):
        return f"financial-report-client:llm:{_model_name(result)}"

    source = _text(getattr(field, "source", "")).lower()
    if source in {"akshare", "yahoo", "derived"}:
        return "financial-report-client"

    return "financial-report-client"


@dataclass(frozen=True)
class FinancialReportPolicy:
    """Translate extractor confidence into TradingAgents usage decisions."""

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

        if is_reliable:
            return FieldUseDecision(
                can_compute=True,
                can_display=True,
                source_label=source_label,
            )

        raw_bucket = _text(getattr(field, "raw_bucket", "")) or "non-reliable"
        return FieldUseDecision(
            can_compute=False,
            can_display=True,
            source_label=source_label,
            caveat=f"{field_id} is display-only: {raw_bucket}",
        )
