# -*- coding: utf-8 -*-
"""
现金健康度计算模块

算法：
- RCF = CFO - CapEx - 财务费用（真实可支配现金流）
- 现金储备 = 总现金 - 有息负债
- 覆盖率: Cov_div = RCF / 预期分红现金
- 缓冲: Buffer_months = 现金储备 / (分红现金/12)
- 健康度: 稳健/健康/警示/不健康
"""

from dataclasses import dataclass
from typing import Dict, Optional, Any

from .config import get_health_thresholds
from tradingagents.utils.logging_init import get_logger

logger = get_logger("value_investment")


@dataclass
class CashHealthResult:
    """现金健康度计算结果"""
    rcf: Optional[float]                # 真实可支配现金流
    cash_reserve: Optional[float]       # 现金储备
    cov_div: Optional[float]            # 分红覆盖率
    buffer_months: Optional[float]      # 现金缓冲月数
    health_flag: str                    # 健康度标志（稳健/健康/警示/不健康）


class CashHealthCalculator:
    """现金健康度计算器"""

    def __init__(self):
        pass

    def calculate(
        self,
        cfo: Optional[float],
        capex: Optional[float],
        interest_expense: Optional[float],
        cash_and_equivalents: Optional[float],
        interest_bearing_debt: Optional[float],
        expected_dividend: Optional[float],
        industry: str = 'default'
    ) -> CashHealthResult:
        """
        计算现金健康度

        Args:
            cfo: 经营活动现金流
            capex: 资本支出（正数）
            interest_expense: 财务费用（正数为支出，负数为收入）
            cash_and_equivalents: 货币资金
            interest_bearing_debt: 有息负债
            expected_dividend: 预期分红金额
            industry: 行业分类

        Returns:
            现金健康度计算结果
        """
        thresholds = get_health_thresholds(industry)
        cov_div_min = thresholds['cov_div_min']
        buffer_months_min = thresholds['buffer_months_min']

        # 1. 计算 RCF (真实可支配现金流) = CFO - CapEx - 财务费用
        rcf = None
        if cfo is not None:
            rcf = float(cfo)
            if capex:
                rcf -= abs(float(capex))
            if interest_expense:
                # 财务费用：正数为支出，负数为利息收入
                rcf -= float(interest_expense)

        # 2. 计算现金储备 = 总现金 - 有息负债
        cash_reserve = None
        if cash_and_equivalents is not None:
            cash_reserve = float(cash_and_equivalents)
            if interest_bearing_debt:
                cash_reserve -= float(interest_bearing_debt)

        # 3. 计算覆盖率 Cov_div = RCF / 预期分红
        cov_div = None
        if rcf is not None and expected_dividend and expected_dividend > 0:
            cov_div = rcf / expected_dividend

        # 4. 计算缓冲月数 Buffer_months = 现金储备 / (月分红)
        buffer_months = None
        if cash_reserve is not None and expected_dividend and expected_dividend > 0:
            monthly_dividend = expected_dividend / 12
            if monthly_dividend > 0:
                buffer_months = cash_reserve / monthly_dividend

        # 5. 判断健康度
        health_flag = self._determine_health_flag(
            cov_div=cov_div,
            buffer_months=buffer_months,
            cash_reserve=cash_reserve,
            cov_div_min=cov_div_min,
            buffer_months_min=buffer_months_min
        )

        return CashHealthResult(
            rcf=rcf,
            cash_reserve=cash_reserve,
            cov_div=cov_div,
            buffer_months=buffer_months,
            health_flag=health_flag
        )

    def _determine_health_flag(
        self,
        cov_div: Optional[float],
        buffer_months: Optional[float],
        cash_reserve: Optional[float],
        cov_div_min: float,
        buffer_months_min: float
    ) -> str:
        """
        判断健康度标志

        规则：
        - 稳健: 覆盖率 >= min×1.2 且 缓冲月数 >= min×1.5
        - 健康: 覆盖率 >= min 且 缓冲月数 >= min
        - 警示: 覆盖率 >= min×0.8 或 缓冲月数 >= min×0.8
        - 不健康: 其他情况
        """
        health_flag = '不健康'

        if cov_div is not None and buffer_months is not None:
            if cov_div >= cov_div_min * 1.2 and buffer_months >= buffer_months_min * 1.5:
                health_flag = '稳健'
            elif cov_div >= cov_div_min and buffer_months >= buffer_months_min:
                health_flag = '健康'
            elif cov_div >= cov_div_min * 0.8 or buffer_months >= buffer_months_min * 0.8:
                health_flag = '警示'
        elif cov_div is not None:
            # 只有覆盖率数据
            if cov_div >= cov_div_min * 1.2:
                health_flag = '稳健'
            elif cov_div >= cov_div_min:
                health_flag = '健康'
            elif cov_div >= cov_div_min * 0.8:
                health_flag = '警示'
        elif cash_reserve is not None:
            # 只有现金储备数据
            if cash_reserve > 0:
                health_flag = '警示'  # 有现金但无覆盖率数据

        return health_flag

    def calculate_from_financial_data(
        self,
        financial_data: Dict[str, Any],
        expected_dividend: float,
        industry: str = 'default'
    ) -> CashHealthResult:
        """
        从财务数据计算现金健康度

        Args:
            financial_data: 财务数据字典
            expected_dividend: 预期分红金额（保守净利润 × 有效支付率）
            industry: 行业分类

        Returns:
            现金健康度计算结果

        问题1修复: 支持新增的财务数据字段
        """
        # 提取所需字段
        cfo = financial_data.get('operating_cash_flow')
        fcf = financial_data.get('free_cash_flow')

        # 优先使用直接获取的 capex，否则从 CFO - FCF 计算
        capex = financial_data.get('capex')
        if capex is None and cfo is not None and fcf is not None:
            capex = float(cfo) - float(fcf)

        # 财务费用
        interest_expense = financial_data.get('interest_expense')
        cash_and_equivalents = financial_data.get('cash_and_equivalents')

        # 优先使用已计算好的有息负债合计，否则累加各项
        interest_bearing_debt = financial_data.get('interest_bearing_debt')
        if interest_bearing_debt is None:
            interest_bearing_debt = 0
            debt_fields = [
                'short_term_debt',
                'long_term_debt',
                'bonds_payable',
                'current_portion_of_long_term_debt'
            ]
            for field in debt_fields:
                val = financial_data.get(field)
                if val:
                    try:
                        interest_bearing_debt += float(val)
                    except (ValueError, TypeError):
                        pass

        logger.debug(f"📊 [现金健康度] CFO={cfo}, CapEx={capex}, 财务费用={interest_expense}, "
                    f"现金={cash_and_equivalents}, 有息负债={interest_bearing_debt}, 预期分红={expected_dividend}")

        return self.calculate(
            cfo=cfo,
            capex=capex,
            interest_expense=interest_expense,
            cash_and_equivalents=cash_and_equivalents,
            interest_bearing_debt=interest_bearing_debt,
            expected_dividend=expected_dividend,
            industry=industry
        )

    def to_dict(self, result: CashHealthResult) -> Dict[str, Any]:
        """将结果转为字典"""
        return {
            'rcf': result.rcf,
            'cash_reserve': result.cash_reserve,
            'cov_div': result.cov_div,
            'buffer_months': result.buffer_months,
            'health_flag': result.health_flag,
            'status': result.health_flag  # 别名，兼容
        }
