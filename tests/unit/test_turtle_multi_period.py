from dataclasses import dataclass
from decimal import Decimal

from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    _derive_historical_period_ends,
    _fetch_periods_concurrently,
)
from tradingagents.dataflows.value_investment.turtle.facts import TurtleReportFacts


# ---------------------------------------------------------------------------
# Fake domain objects
# ---------------------------------------------------------------------------

@dataclass
class FakeField:
    """A field that passes policy and produces reliability="reliable" → status="complete"."""
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


@dataclass
class _FakeExtraction:
    fields: dict
    staleness: object = None
    company: object = None
    market: object = None
    period_end: str = ""
    catalog_version: object = None


@dataclass
class _FakeResult:
    extraction: object
    warnings: list
    errors: list


def _usable_extraction(period_end: str) -> _FakeExtraction:
    """Return an extraction with one verified field that passes policy → complete facts."""
    return _FakeExtraction(
        fields={"net_profit": FakeField("net_profit", Decimal("10000000000"))},
        period_end=period_end,
    )


class _FakeAdapter:
    """
    Returns a result per period, with three possible outcomes per period:
    - period in fail_set            -> raises RuntimeError (exception, dropped by _fetch_periods_concurrently)
    - period in graceful_fail_set   -> returns _FakeResult(extraction=None) (adapter graceful failure)
    - otherwise                     -> returns _FakeResult(extraction=_usable_extraction(...)) (complete facts)
    """
    def __init__(self, fail_set=None, graceful_fail_set=None):
        self.fail_set = fail_set or set()
        self.graceful_fail_set = graceful_fail_set or set()

    def get_annual_report_data(self, *, ticker, market, period_end, reference_date):
        if period_end in self.fail_set:
            raise RuntimeError(f"simulated fetch failure for {period_end}")
        if period_end in self.graceful_fail_set:
            return _FakeResult(
                extraction=None,
                warnings=[],
                errors=[f"simulated graceful failure for {period_end}"],
            )
        return _FakeResult(
            extraction=_usable_extraction(period_end),
            warnings=[], errors=[],
        )


class TestFetchPeriodsConcurrently:
    def test_all_periods_succeed(self):
        adapter = _FakeAdapter()
        results = _fetch_periods_concurrently(
            active_adapter=adapter, ticker="600519", market="CN",
            period_ends=["2024-12-31", "2023-12-31", "2022-12-31"],
            reference_date="2025-05-19", allow_llm_models=(),
        )
        assert set(results.keys()) == {"2024-12-31", "2023-12-31", "2022-12-31"}
        assert all(isinstance(v, TurtleReportFacts) for v in results.values())

    def test_one_period_fails_is_dropped(self):
        adapter = _FakeAdapter(fail_set={"2022-12-31"})
        results = _fetch_periods_concurrently(
            active_adapter=adapter, ticker="600519", market="CN",
            period_ends=["2024-12-31", "2023-12-31", "2022-12-31"],
            reference_date="2025-05-19", allow_llm_models=(),
        )
        assert set(results.keys()) == {"2024-12-31", "2023-12-31"}

    def test_all_periods_fail_returns_empty(self):
        adapter = _FakeAdapter(fail_set={"2024-12-31", "2023-12-31"})
        results = _fetch_periods_concurrently(
            active_adapter=adapter, ticker="600519", market="CN",
            period_ends=["2024-12-31", "2023-12-31"],
            reference_date="2025-05-19", allow_llm_models=(),
        )
        assert results == {}


class TestDeriveHistoricalPeriodEnds:
    def test_two_periods(self):
        assert _derive_historical_period_ends("2024-12-31", 2) == ["2023-12-31", "2022-12-31"]

    def test_zero_periods(self):
        assert _derive_historical_period_ends("2024-12-31", 0) == []

    def test_negative_periods(self):
        assert _derive_historical_period_ends("2024-12-31", -1) == []

    def test_three_periods(self):
        assert _derive_historical_period_ends("2025-12-31", 3) == [
            "2024-12-31", "2023-12-31", "2022-12-31",
        ]


from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    get_turtle_report_facts,
)


class TestGetTurtleReportFactsMultiPeriod:
    def test_history_periods_zero_no_historical(self):
        adapter = _FakeAdapter()
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=0,
        )
        assert facts.historical == {}

    def test_history_periods_two_populates_historical(self):
        # Usable extractions (verified field) → complete status → kept in historical
        adapter = _FakeAdapter()
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        # latest = 2024-12-31 (trade_date 2025-05, month>3 -> year-1)
        # historical = 2023-12-31, 2022-12-31
        assert set(facts.historical.keys()) == {"2023-12-31", "2022-12-31"}
        # Verify they are complete (not dropped)
        assert all(v.status == "complete" for v in facts.historical.values())

    def test_one_historical_period_fails_dropped(self):
        # Exception-raising failure for 2022-12-31 → dropped entirely (not in results dict)
        adapter = _FakeAdapter(fail_set={"2022-12-31"})
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        assert set(facts.historical.keys()) == {"2023-12-31"}

    def test_latest_fails_returns_synthetic_non_decisionable(self):
        # Exception in latest → synthetic non_decisionable (Case 1)
        adapter = _FakeAdapter(fail_set={"2024-12-31"})
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        assert facts.status == "non_decisionable"
        assert facts.fields == {}
        assert facts.historical == {}
        assert any("2024-12-31" in c for c in facts.caveats)

    def test_graceful_failed_historical_period_dropped(self):
        # Graceful failure (extraction=None) for 2022-12-31 → non_decisionable → dropped from historical
        adapter = _FakeAdapter(graceful_fail_set={"2022-12-31"})
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        # 2022-12-31 graceful-failed → non_decisionable → must NOT appear in historical
        assert "2022-12-31" not in facts.historical
        # 2023-12-31 succeeded usably → must appear in historical
        assert "2023-12-31" in facts.historical
        # Latest (2024-12-31) is usable
        assert facts.status in ("complete", "degraded")

    def test_latest_graceful_failure_is_non_decisionable(self):
        # Graceful failure (extraction=None) for latest → non_decisionable (Case 2),
        # historical must be dropped even if prior periods succeeded.
        adapter = _FakeAdapter(graceful_fail_set={"2024-12-31"})
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        assert facts.status == "non_decisionable"
        assert facts.fields == {}
        assert facts.historical == {}
