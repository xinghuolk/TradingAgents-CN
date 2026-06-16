"""
Task 10: 主函数集成 - 币种主闸 + HK 回购组装
TDD: 先写失败测试，再实现，再通过
"""
import pytest
from unittest.mock import patch
from tradingagents.tools import value_investment_tool as vt


def _patches(fin, mkt, repurchase=None):
    fin = dict(fin)
    if repurchase is not None:
        fin['repurchase_of_stock'] = repurchase
    return patch.multiple(vt,
        _fetch_financial_data_structured=lambda t, m: fin,
        _supplement_with_report_collector=lambda fd, t, m: fd,
        apply_financial_report_client_data=lambda **k: k['financial_data'],
        _fetch_market_data_structured=lambda t, m: mkt,
        _fetch_dividend_data_sync=lambda t, m: {'records': [], 'consecutive_years': 0, 'avg_payout_ratio_3y': 0, 'total_dividend_years': 0},
        _fetch_buyback_data_sync=lambda t, m: {'records': [], 'total_cancelled_amount': 0, 'latest_year_amount': 0, 'has_active_buyback': False},
        _get_industry_dynamic=lambda t, m: 'default')


def test_cross_currency_aborts():
    """跨币种（HKD 财务 + CNY 市值）应触发主闸，返回含'币种不一致'的错误，而非其他异常导致的 ❌"""
    fin = {'_currency': 'HKD', 'net_profits': [1e10], 'operating_cash_flow': 1e10}
    mkt = {'_currency': 'CNY', 'market_cap': 9.032e11}  # CNY 折算市值
    with _patches(fin, mkt):
        out = vt.get_value_investment_analysis.invoke({'ticker': '00001', 'market': 'HK'})
    # 主闸必须返回包含"币种不一致"的错误，而不是其他异常消息
    assert '币种不一致' in out, f"期望'币种不一致'主闸错误，实际: {out[:200]}"


def test_hk_repurchase_injected_into_buyback():
    """HK 回购：financial_data['repurchase_of_stock'] 非 None 时应注入 buyback_data['total_cancelled_amount']"""
    fin = {'_currency': 'HKD', 'net_profits': [1e10], 'operating_cash_flow': 1e10, 'free_cash_flow': 8e9}
    mkt = {'_currency': 'HKD', 'market_cap': 1e12}
    captured = {}

    def fake_full(**kw):
        captured.update(kw)
        return None

    # repurchase=5e8：非零非 None，buyback_data 初始 total_cancelled_amount=0
    # 注入后应变为 5e8，不注入则仍为 0
    with _patches(fin, mkt, repurchase=5e8), \
         patch.object(vt.PenetratingYieldCalculator, 'calculate_full_analysis', side_effect=fake_full):
        vt.get_value_investment_analysis.invoke({'ticker': '00001', 'market': 'HK'})
    # 若未实现注入，captured 里 total_cancelled_amount 仍为 0（子包默认值）
    assert captured.get('buyback_data', {}).get('total_cancelled_amount') == 5e8, \
        f"期望 total_cancelled_amount=5e8，实际: {captured.get('buyback_data')}"


def test_hk_repurchase_none_does_not_override():
    """repurchase=None 时不覆盖，buyback_data['total_cancelled_amount'] 保持子包 0"""
    fin = {'_currency': 'HKD', 'net_profits': [1e10], 'operating_cash_flow': 1e10, 'free_cash_flow': 8e9}
    mkt = {'_currency': 'HKD', 'market_cap': 1e12}
    captured = {}

    def fake_full(**kw):
        captured.update(kw)
        return None

    with _patches(fin, mkt, repurchase=None), \
         patch.object(vt.PenetratingYieldCalculator, 'calculate_full_analysis', side_effect=fake_full):
        vt.get_value_investment_analysis.invoke({'ticker': '00001', 'market': 'HK'})
    assert captured['buyback_data']['total_cancelled_amount'] == 0  # None 不覆盖，保持子包 0


def test_hk_repurchase_zero_overrides():
    """repurchase=0.0 时覆盖为 0（明确上报 0 回购，区别于无数据）"""
    fin = {'_currency': 'HKD', 'net_profits': [1e10], 'operating_cash_flow': 1e10, 'free_cash_flow': 8e9}
    mkt = {'_currency': 'HKD', 'market_cap': 1e12}
    captured = {}

    def fake_full(**kw):
        captured.update(kw)
        return None

    with _patches(fin, mkt, repurchase=0.0), \
         patch.object(vt.PenetratingYieldCalculator, 'calculate_full_analysis', side_effect=fake_full):
        vt.get_value_investment_analysis.invoke({'ticker': '00001', 'market': 'HK'})
    assert captured['buyback_data']['total_cancelled_amount'] == 0.0  # 0 覆盖为 0
