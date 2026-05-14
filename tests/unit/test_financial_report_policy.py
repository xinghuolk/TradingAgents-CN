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
    confidence: object = "verified"
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


def test_not_present_field_with_value_uses_unavailable_source_label():
    policy = FinancialReportPolicy(allow_llm_models=("codex",))
    field = FakeField(
        field_id="revenue",
        value=Decimal("1"),
        source="akshare",
        raw_bucket="unresolved_conflict",
        is_reliable=False,
        is_present=False,
    )

    decision = policy.decide(field=field, result=FakeResult(None, None))

    assert decision.can_compute is False
    assert decision.can_display is False
    assert decision.source_label == "financial-report-client:unavailable"
    assert decision.caveat == "revenue unavailable: unresolved_conflict"


def test_reliable_llm_field_still_requires_allowed_model_for_compute():
    policy = FinancialReportPolicy(allow_llm_models=("codex",))
    field = FakeField(
        field_id="revenue",
        value=Decimal("1"),
        source="llm",
        raw_bucket="llm_supplement_present",
        is_reliable=True,
        is_present=True,
    )

    decision = policy.decide(field=field, result=FakeResult("deepseek", "deepseek-v3"))

    assert decision.can_compute is False
    assert decision.can_display is True
    assert decision.source_label == "financial-report-client:llm:deepseek-v3"
    assert decision.caveat == "LLM supplement from deepseek-v3 is display-only by policy"


def test_unavailable_llm_field_uses_unavailable_source_label():
    policy = FinancialReportPolicy(allow_llm_models=("codex",))
    field = FakeField(
        field_id="gross_profit",
        value=None,
        source="llm",
        raw_bucket="source_unavailable",
        is_reliable=False,
        is_present=False,
    )

    decision = policy.decide(field=field, result=FakeResult("deepseek", "deepseek-v3"))

    assert decision.can_compute is False
    assert decision.can_display is False
    assert decision.source_label == "financial-report-client:unavailable"
    assert decision.caveat == "gross_profit unavailable: source_unavailable"


def test_field_source_label_uses_model_metadata_for_llm_source():
    label = field_source_label(
        field=FakeField(field_id="revenue", value=Decimal("1"), source="llm"),
        result=FakeResult("openai", "gpt-5.5"),
    )

    assert label == "financial-report-client:llm:gpt-5.5"
