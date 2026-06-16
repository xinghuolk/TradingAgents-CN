"""端到端验收测试：value 多源币种归一改造。

hermetic 测试（默认跑）：
    test_0_9032_pollution_rejected — 验证 HKD 财务 + CNY 折算市值被主闸拒绝。

integration 测试（需 -m integration）：
    test_e2e_a_share — 600519 A 股出完整价值报告。
    test_e2e_hk      — 00001.HK 港股出完整价值报告。
"""
import pytest
from tradingagents.tools.value_investment_tool import get_value_investment_analysis


@pytest.mark.integration
def test_e2e_a_share():
    out = get_value_investment_analysis.invoke({'ticker': '600519', 'market': 'A'})
    assert '无法获取' not in out and '❌' not in out


@pytest.mark.integration
def test_e2e_hk():
    out = get_value_investment_analysis.invoke({'ticker': '00001', 'market': 'HK'})
    assert '无法获取' not in out and '❌' not in out


def test_0_9032_pollution_rejected():
    # hermetic：HKD 财务 + CNY 折算市值 必须被主闸拒绝
    from tradingagents.dataflows.value_investment.unit_normalizer import assert_consistent_currency
    fin = {'_currency': 'HKD', 'operating_cash_flow': 100.0}
    mkt_cny = {'_currency': 'CNY', 'market_cap': 903.2}  # 1000 HKD × 0.9032
    with pytest.raises(ValueError):
        assert_consistent_currency(fin, mkt_cny)
