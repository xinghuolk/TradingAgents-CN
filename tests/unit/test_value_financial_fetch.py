"""
tests/unit/test_value_financial_fetch.py

Task 5/11: 验证 _fetch_financial_data_structured 的 None 语义修复
- 债务分项缺失 → interest_bearing_debt 为 None（不为 0，不抛异常）
- HK 市场 → 返回骨架并标注 _currency = 'HKD'
- A 股正常数据 → _currency = 'CNY'
"""
import pandas as pd
from unittest.mock import patch
from tradingagents.tools import value_investment_tool as vt


def test_ibd_all_missing_is_none():
    """资产负债表缺全部债务分项 → interest_bearing_debt 应为 None（不抛、不为 0）"""
    bs = pd.DataFrame([{
        '资产总计': 1000.0,
        '负债合计': 600.0,
        '流动资产合计': 200.0,
        '流动负债合计': 100.0,
        # 故意不包含: 短期借款、长期借款、应付债券、一年内到期的非流动负债
    }])
    with patch('akshare.stock_financial_abstract', return_value=pd.DataFrame()), \
         patch('akshare.stock_financial_report_sina',
               side_effect=lambda stock, symbol: bs if symbol == '资产负债表' else pd.DataFrame()), \
         patch('akshare.stock_financial_abstract_ths', return_value=pd.DataFrame()):
        out = vt._fetch_financial_data_structured('600519', 'A')
    assert out['interest_bearing_debt'] is None, (
        f"期望 interest_bearing_debt 为 None，实际为 {out['interest_bearing_debt']!r}"
    )
    assert out['_currency'] == 'CNY', (
        f"期望 _currency='CNY'，实际为 {out.get('_currency')!r}"
    )


def test_hk_skeleton_tagged():
    """HK 市场 → 返回空骨架并标注 _currency = 'HKD'"""
    out = vt._fetch_financial_data_structured('00001', 'HK')
    assert out['_currency'] == 'HKD', (
        f"期望 _currency='HKD'，实际为 {out.get('_currency')!r}"
    )
