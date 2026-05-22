from tradingagents.dataflows.value_investment.turtle.report_adapter import (
    _derive_historical_period_ends,
)


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
