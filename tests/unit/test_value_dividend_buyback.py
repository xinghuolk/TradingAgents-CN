# -*- coding: utf-8 -*-
"""
Task 8: 分红/回购子包集成测试
验证 _fetch_dividend_data_sync / _fetch_buyback_data_sync 通过 DividendFetcher/BuybackFetcher 获取数据
"""

from unittest.mock import patch, AsyncMock
from tradingagents.tools import value_investment_tool as vt


def test_hk_dividend_via_subpackage():
    fake = {'records': [{'year': 2025}], 'consecutive_years': 5,
            'avg_payout_ratio_3y': 0.4, 'total_dividend_years': 5}
    with patch('tradingagents.dataflows.value_investment.DividendFetcher') as F:
        F.return_value.fetch_dividend_data = AsyncMock(return_value=fake)
        out = vt._fetch_dividend_data_sync('00001', 'HK')
    assert out['consecutive_years'] == 5


def test_hk_buyback_empty_skeleton():
    with patch('tradingagents.dataflows.value_investment.BuybackFetcher') as F:
        F.return_value.fetch_buyback_data = AsyncMock(return_value=None)
        out = vt._fetch_buyback_data_sync('00001', 'HK')
    assert out['total_cancelled_amount'] == 0


def test_a_share_dividend_still_works():
    fake = {'records': [{'year': 2024}], 'consecutive_years': 3,
            'avg_payout_ratio_3y': 0.5, 'total_dividend_years': 3}
    with patch('tradingagents.dataflows.value_investment.DividendFetcher') as F:
        F.return_value.fetch_dividend_data = AsyncMock(return_value=fake)
        out = vt._fetch_dividend_data_sync('600519', 'A')
    assert out['consecutive_years'] == 3
