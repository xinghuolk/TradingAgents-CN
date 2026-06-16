import pytest
from tradingagents.dataflows.value_investment.unit_normalizer import (
    tag_currency, assert_same_currency, assert_consistent_currency, AMOUNT_FIELDS,
)

def test_tag_currency_prefers_explicit():
    out = tag_currency({'operating_cash_flow': 62567000000.0}, source_currency='HKD', market='HK')
    assert out['_currency'] == 'HKD'
    assert out['operating_cash_flow'] == 62567000000.0  # 不缩放

def test_tag_currency_falls_back_to_market():
    out = tag_currency({'cash_and_equivalents': 1.0}, source_currency=None, market='A')
    assert out['_currency'] == 'CNY'

def test_tag_currency_skips_underscore_keys():
    out = tag_currency({'_data_source': {'x': 1}}, source_currency='CNY', market='A')
    assert out['_data_source'] == {'x': 1}

def test_assert_same_currency_rejects_mismatch():
    with pytest.raises(ValueError):
        assert_same_currency({'_currency': 'CNY'}, {'_currency': 'HKD'})

def test_assert_same_currency_empty_passes():
    assert_same_currency({}, {'_currency': 'HKD'})  # 空币种放行（merge 次闸向后兼容）

def test_assert_consistent_currency_main_gate_rejects():
    fin = {'_currency': 'HKD', 'operating_cash_flow': 1.0}
    mkt = {'_currency': 'CNY', 'market_cap': 2.0}
    with pytest.raises(ValueError):
        assert_consistent_currency(fin, mkt)

def test_main_gate_missing_currency_on_amount_dict_rejects():
    with pytest.raises(ValueError):
        assert_consistent_currency({'market_cap': 2.0})  # 含金额但无 _currency → 拒绝

def test_main_gate_empty_dict_skipped():
    assert_consistent_currency({}, {'_currency': 'HKD', 'market_cap': 1.0})  # 全空跳过，不抛
