# -*- coding: utf-8 -*-
"""
TDD 测试：report_data_mapper growth 小数化 + 币种标注 + merge 守卫
Task 3/11: value 多源币种归一
"""
import pytest
from tradingagents.dataflows.value_investment.report_data_mapper import (
    map_extracted_reports_to_financial_data, merge_financial_data,
)


def _report(year, net_profit, revenue):
    return {'data': {
        'income_statement': {'net_profit': net_profit, 'revenue': revenue},
        'balance_sheet': {'total_assets': 1000.0, 'total_liabilities': 600.0},
        'cash_flow_statement': {'operating_cash_flow': net_profit * 1.5},
        'financial_metrics': {'roe': 18.0},
        '_pdf_info': {'report_year': year},
    }}


def test_growth_is_decimal_not_percent():
    reports = [_report(2025, 120, 1200), _report(2024, 110, 1100), _report(2023, 100, 1000)]
    out = map_extracted_reports_to_financial_data(reports)
    assert out['revenue_growth_3y'] == pytest.approx(0.2, abs=1e-6)
    assert out['profit_growth_3y'] == pytest.approx(0.2, abs=1e-6)


def test_output_tagged_hkd():
    out = map_extracted_reports_to_financial_data([_report(2025, 120, 1200)])
    assert out['_currency'] == 'HKD'


def test_merge_rejects_cross_currency():
    with pytest.raises(ValueError):
        merge_financial_data({'_currency': 'CNY'}, {'_currency': 'HKD'})


def test_merge_same_currency_supplements():
    a = {'operating_cash_flow': None, '_currency': 'HKD'}
    rc = {'operating_cash_flow': 6.25e10, '_currency': 'HKD'}
    merged = merge_financial_data(a, rc)
    assert merged['operating_cash_flow'] == 6.25e10
    assert merged['_data_source']['operating_cash_flow'] == 'report-collector'


def test_a_share_market_tagged_cny():
    out = map_extracted_reports_to_financial_data([_report(2025, 120, 1200)], market='A')
    assert out['_currency'] == 'CNY'
