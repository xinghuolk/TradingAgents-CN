from unittest.mock import patch
from tradingagents.tools import value_investment_tool as vt

def test_hk_industry_via_yfinance():
    with patch('tradingagents.dataflows.providers.hk.hk_stock.get_hk_stock_info',
               return_value={'industry': '综合企业'}):
        assert vt._get_industry_dynamic('00001', 'HK') == '综合企业'

def test_hk_industry_default_when_missing():
    with patch('tradingagents.dataflows.providers.hk.hk_stock.get_hk_stock_info', return_value={}):
        assert vt._get_industry_dynamic('00001', 'HK') == 'default'
