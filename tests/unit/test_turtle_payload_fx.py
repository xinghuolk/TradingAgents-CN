from tradingagents.tools import turtle_analysis_tool as tat
from tradingagents.dataflows.value_investment.turtle.facts import (
    MoneyAmount,
    TurtleFactValue,
    TurtleMarketFacts,
    TurtleReportFacts,
)


def _money_fact(name, value, currency, ref):
    return TurtleFactValue(
        name=name,
        value=MoneyAmount(value=value, currency=currency, unit="yuan", source_label="t", source_reference=ref),
        source_label="t",
        source_reference=ref,
    )


def test_collect_currencies_normalizes_and_dedups():
    report = TurtleReportFacts(fields={"net_profit": _money_fact("net_profit", 5e8, "HK$", "r")})
    market = TurtleMarketFacts(fields={"market_cap": _money_fact("market_cap", 1e10, "HKD", "m")})
    assert tat._collect_currencies(report, market) == {"HKD"}
