# -*- coding: utf-8 -*-
"""
Task 6/11: 测试 _fetch_market_data_structured 走 yfinance 获取市值/股价
"""
from unittest.mock import patch
from tradingagents.tools import value_investment_tool as vt


def test_a_share_market_cap_yuan_cny():
    with patch.object(vt, '_yfinance_snapshot',
                      return_value={'market_cap': 1.588e12, 'close_price': 1600.0}) as m:
        out = vt._fetch_market_data_structured('600519', 'A')
    assert out['market_cap'] == 1.588e12   # 元直通，无 ×1e4
    assert out['_currency'] == 'CNY'
    assert m.call_args[0][0] == '600519.SS'  # 沪市后缀


def test_hk_market_cap_yuan_hkd():
    with patch.object(vt, '_yfinance_snapshot',
                      return_value={'market_cap': 2.68e11, 'close_price': 67.55}) as m:
        out = vt._fetch_market_data_structured('00001', 'HK')
    assert out['market_cap'] == 2.68e11
    assert out['_currency'] == 'HKD'
    assert m.call_args[0][0] == '0001.HK'    # 港股 4 位补零 + .HK


def test_shenzhen_suffix():
    with patch.object(vt, '_yfinance_snapshot',
                      return_value={'market_cap': 1e11, 'close_price': 10.0}) as m:
        vt._fetch_market_data_structured('000001', 'A')
    assert m.call_args[0][0] == '000001.SZ'  # 深市后缀
