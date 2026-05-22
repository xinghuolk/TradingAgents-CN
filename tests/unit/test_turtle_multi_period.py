from dataclasses import dataclass

from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    _derive_historical_period_ends,
    _fetch_periods_concurrently,
)
from tradingagents.dataflows.value_investment.turtle.facts import TurtleReportFacts


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


class _FakeAdapter:
    """Returns a result per period; raises for periods in fail_set."""
    def __init__(self, fail_set=None):
        self.fail_set = fail_set or set()

    def get_annual_report_data(self, *, ticker, market, period_end, reference_date):
        if period_end in self.fail_set:
            raise RuntimeError(f"simulated fetch failure for {period_end}")
        return _FakeResult(
            extraction=_FakeExtraction(fields={}, period_end=period_end),
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
        adapter = _FakeAdapter()
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        # latest = 2024-12-31 (trade_date 2025-05, month>3 -> year-1)
        # historical = 2023-12-31, 2022-12-31
        assert set(facts.historical.keys()) == {"2023-12-31", "2022-12-31"}

    def test_one_historical_period_fails_dropped(self):
        adapter = _FakeAdapter(fail_set={"2022-12-31"})
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        assert set(facts.historical.keys()) == {"2023-12-31"}

    def test_latest_fails_returns_synthetic_non_decisionable(self):
        adapter = _FakeAdapter(fail_set={"2024-12-31"})
        facts = get_turtle_report_facts(
            ticker="600519", market="A", trade_date="2025-05-19",
            adapter=adapter, allow_llm_models=(), history_periods=2,
        )
        assert facts.status == "non_decisionable"
        assert facts.fields == {}
        assert facts.historical == {}
        assert any("2024-12-31" in c for c in facts.caveats)
