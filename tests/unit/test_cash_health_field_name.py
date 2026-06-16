# -*- coding: utf-8 -*-
"""
测试 cash_health.py 回退累加读 bond_payable（FRC 字段名，无 s）
Task 9/11: value 多源币种归一 — 统一字段名
"""

from tradingagents.dataflows.value_investment.cash_health import CashHealthCalculator


def test_bond_payable_feeds_ibd_fallback():
    """
    interest_bearing_debt 缺失 → 回退累加，须能读 FRC 字段名 bond_payable（无 s）。

    断言依据：
      cash_reserve = cash_and_equivalents - interest_bearing_debt
      = 50 - (10 + 20 + 30) = 50 - 60 = -10.0
    to_dict 不直接暴露 interest_bearing_debt，用 cash_reserve 验证 bond_payable 已被计入。
    若仍读 bonds_payable（旧错误字段），ibd=30，cash_reserve=20，断言失败。
    """
    fd = {
        'operating_cash_flow': 100.0,
        'free_cash_flow': 80.0,
        'cash_and_equivalents': 50.0,
        'short_term_debt': 10.0,
        'long_term_debt': 20.0,
        'bond_payable': 30.0,          # FRC mapper 写的字段名（无 s）
    }
    c = CashHealthCalculator()
    res = c.calculate_from_financial_data(financial_data=fd, expected_dividend=5.0)
    d = c.to_dict(res)

    # cash_reserve 应为 50 - 60 = -10，证明 bond_payable 已计入有息负债累加
    assert d['cash_reserve'] == -10.0, (
        f"期望 cash_reserve=-10.0（ibd=60），实际={d['cash_reserve']}。"
        "可能仍在读 bonds_payable（带 s）而非 bond_payable（无 s）。"
    )
