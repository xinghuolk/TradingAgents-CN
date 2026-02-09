# -*- coding: utf-8 -*-
"""
5维健康评分系统

评分维度：
1. 盈利能力: ROE(60%) + 利润质量(40%)
2. 偿债能力: 资产负债率(60%) + 流动比率(40%)
3. 成长能力: 营收增长(40%) + 利润增长(60%)
4. 运营能力: 资产周转率(100%)
5. 现金流能力: 穿透收益率(60%) + FCF收益率(40%)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from tradingagents.utils.logging_init import get_logger

logger = get_logger("value_investment")


@dataclass
class HealthScoreResult:
    """健康评分结果"""
    profitability: int      # 盈利能力 (0-100)
    solvency: int           # 偿债能力 (0-100)
    growth: int             # 成长能力 (0-100)
    operation: int          # 运营能力 (0-100)
    cashflow: int           # 现金流能力 (0-100)
    total: int              # 综合得分 (0-100)


class HealthScoreCalculator:
    """健康评分计算器"""

    def __init__(self):
        pass

    def calculate(
        self,
        indicators: Dict[str, Any],
        financials: List[Dict[str, Any]]
    ) -> HealthScoreResult:
        """
        计算5维健康评分

        Args:
            indicators: 已计算的指标 {
                'roe_avg_3y': float,
                'profit_quality': float,
                'penetrating_yield': float,
                'fcf_yield': float,
                'revenue_growth_3y': float,
                'profit_growth_3y': float,
                'asset_turnover': float,
            }
            financials: 财务数据列表（按年份降序）

        Returns:
            健康评分结果
        """
        scores = {
            'profitability': 50,
            'solvency': 50,
            'growth': 50,
            'operation': 50,
            'cashflow': 50,
        }

        # 1. 盈利能力评分
        scores['profitability'] = self._calculate_profitability_score(indicators)

        # 2. 偿债能力评分
        scores['solvency'] = self._calculate_solvency_score(financials)

        # 3. 成长能力评分
        scores['growth'] = self._calculate_growth_score(indicators, financials)

        # 4. 运营能力评分
        scores['operation'] = self._calculate_operation_score(indicators, financials)

        # 5. 现金流能力评分
        scores['cashflow'] = self._calculate_cashflow_score(indicators)

        # 综合得分
        total = sum(scores.values()) // len(scores)

        return HealthScoreResult(
            profitability=scores['profitability'],
            solvency=scores['solvency'],
            growth=scores['growth'],
            operation=scores['operation'],
            cashflow=scores['cashflow'],
            total=total
        )

    def _calculate_profitability_score(self, indicators: Dict) -> int:
        """
        盈利能力评分
        = ROE得分 × 60% + 利润质量得分 × 40%
        """
        roe = indicators.get('roe_avg_3y')
        profit_quality = indicators.get('profit_quality')

        # ROE 得分
        roe_score = 50
        if roe:
            if roe >= 20:
                roe_score = 100
            elif roe >= 15:
                roe_score = 80
            elif roe >= 10:
                roe_score = 60
            elif roe >= 5:
                roe_score = 40
            else:
                roe_score = 20

        # 利润质量得分 (CFO/净利润)
        quality_score = 50
        if profit_quality:
            if profit_quality >= 1.0:
                quality_score = 100
            elif profit_quality >= 0.8:
                quality_score = 80
            elif profit_quality >= 0.6:
                quality_score = 60
            elif profit_quality >= 0.4:
                quality_score = 40
            else:
                quality_score = 20

        return int(roe_score * 0.6 + quality_score * 0.4)

    def _calculate_solvency_score(self, financials: List[Dict]) -> int:
        """
        偿债能力评分
        = 资产负债率得分 × 60% + 流动比率得分 × 40%

        问题2修复: 支持英文字段名和中文字段名
        """
        debt_ratio = None
        current_ratio = None

        if financials and len(financials) > 0:
            latest = financials[0]
            # 支持英文字段名（首选）和中文字段名（备选）
            debt_ratio = (
                latest.get('asset_liability_ratio') or
                latest.get('资产负债率')
            )
            current_ratio = (
                latest.get('current_ratio') or
                latest.get('流动比率')
            )

        # 资产负债率得分（越低越好）
        debt_score = 50
        if debt_ratio is not None:
            # 转为百分比
            debt_ratio_pct = float(debt_ratio) * 100 if debt_ratio < 1 else float(debt_ratio)
            if debt_ratio_pct <= 30:
                debt_score = 100
            elif debt_ratio_pct <= 40:
                debt_score = 80
            elif debt_ratio_pct <= 50:
                debt_score = 60
            elif debt_ratio_pct <= 60:
                debt_score = 40
            else:
                debt_score = 20

        # 流动比率得分（越高越好）
        liquidity_score = 50
        if current_ratio is not None:
            cr = float(current_ratio)
            if cr >= 2.0:
                liquidity_score = 100
            elif cr >= 1.5:
                liquidity_score = 80
            elif cr >= 1.0:
                liquidity_score = 60
            elif cr >= 0.8:
                liquidity_score = 40
            else:
                liquidity_score = 20

        return int(debt_score * 0.6 + liquidity_score * 0.4)

    def _calculate_growth_score(
        self,
        indicators: Dict,
        financials: List[Dict]
    ) -> int:
        """
        成长能力评分
        = 营收增长得分 × 40% + 利润增长得分 × 60%
        """
        revenue_growth = indicators.get('revenue_growth_3y')
        profit_growth = indicators.get('profit_growth_3y')

        # 如果没有预计算的增长率，从财务数据计算
        # 问题2修复: 支持英文和中文字段名
        if revenue_growth is None and financials and len(financials) >= 3:
            revenues = []
            for f in financials[:3]:
                rev = f.get('revenue') or f.get('营业总收入') or f.get('营业收入')
                if rev:
                    try:
                        revenues.append(float(rev))
                    except (ValueError, TypeError):
                        pass
            if len(revenues) >= 2 and revenues[-1] and revenues[-1] > 0:
                revenue_growth = (revenues[0] / revenues[-1]) ** (1 / (len(revenues) - 1)) - 1

        if profit_growth is None and financials and len(financials) >= 3:
            profits = []
            for f in financials[:3]:
                np = f.get('net_profit') or f.get('归属于母公司所有者的净利润') or f.get('净利润')
                if np:
                    try:
                        val = float(np)
                        if val > 0:
                            profits.append(val)
                    except (ValueError, TypeError):
                        pass
            if len(profits) >= 2 and profits[-1] and profits[-1] > 0:
                profit_growth = (profits[0] / profits[-1]) ** (1 / (len(profits) - 1)) - 1

        # 营收增长得分
        rev_growth_score = 50
        if revenue_growth is not None:
            rg = float(revenue_growth) * 100  # 转为百分比
            if rg >= 20:
                rev_growth_score = 100
            elif rg >= 10:
                rev_growth_score = 80
            elif rg >= 5:
                rev_growth_score = 60
            elif rg >= 0:
                rev_growth_score = 40
            else:
                rev_growth_score = 20

        # 利润增长得分
        profit_growth_score = 50
        if profit_growth is not None:
            pg = float(profit_growth) * 100
            if pg >= 20:
                profit_growth_score = 100
            elif pg >= 10:
                profit_growth_score = 80
            elif pg >= 5:
                profit_growth_score = 60
            elif pg >= 0:
                profit_growth_score = 40
            else:
                profit_growth_score = 20

        return int(rev_growth_score * 0.4 + profit_growth_score * 0.6)

    def _calculate_operation_score(
        self,
        indicators: Dict,
        financials: List[Dict]
    ) -> int:
        """
        运营能力评分
        = 资产周转率得分 × 100%
        """
        asset_turnover = indicators.get('asset_turnover')

        # 从财务数据估算资产周转率
        # 问题2修复: 支持英文和中文字段名
        if asset_turnover is None and financials and len(financials) > 0:
            latest = financials[0]
            revenue = latest.get('revenue') or latest.get('营业总收入') or latest.get('营业收入')
            total_assets = latest.get('total_assets') or latest.get('资产总计')
            if revenue and total_assets:
                try:
                    if float(total_assets) > 0:
                        asset_turnover = float(revenue) / float(total_assets)
                except (ValueError, TypeError):
                    pass

        turnover_score = 50
        if asset_turnover is not None:
            at = float(asset_turnover)
            if at >= 1.0:
                turnover_score = 100
            elif at >= 0.7:
                turnover_score = 80
            elif at >= 0.5:
                turnover_score = 60
            elif at >= 0.3:
                turnover_score = 40
            else:
                turnover_score = 30

        return turnover_score

    def _calculate_cashflow_score(self, indicators: Dict) -> int:
        """
        现金流能力评分
        = 穿透收益率得分 × 60% + FCF收益率得分 × 40%
        """
        py = indicators.get('penetrating_yield')
        fcf_yield = indicators.get('fcf_yield')

        # 穿透收益率得分
        py_score = 50
        if py:
            py_pct = float(py)
            if py_pct >= 10:
                py_score = 100
            elif py_pct >= 7:
                py_score = 80
            elif py_pct >= 5:
                py_score = 60
            elif py_pct >= 3:
                py_score = 40
            else:
                py_score = 20

        # FCF收益率得分
        fcf_score = 50
        if fcf_yield is not None:
            fy = float(fcf_yield)
            if fy >= 8:
                fcf_score = 100
            elif fy >= 5:
                fcf_score = 80
            elif fy >= 3:
                fcf_score = 60
            elif fy >= 0:
                fcf_score = 40
            else:
                fcf_score = 20

        return int(py_score * 0.6 + fcf_score * 0.4)

    def to_dict(self, result: HealthScoreResult) -> Dict[str, int]:
        """将结果转为字典"""
        return {
            'health_score_profitability': result.profitability,
            'health_score_solvency': result.solvency,
            'health_score_growth': result.growth,
            'health_score_operation': result.operation,
            'health_score_cashflow': result.cashflow,
            'health_score_total': result.total,
        }


def generate_health_flags(
    indicators: Dict[str, Any],
    cash_health: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    生成健康标志数组

    标志类型：
    - high_roe: ROE >= 15%
    - stable_dividend: 连续分红 >= 5年
    - low_debt: 资产负债率 < 50%
    - positive_fcf: 自由现金流 > 0
    - profit_quality: CFO/净利润 >= 0.8
    - high_penetrating_yield: 穿透率 >= 5%
    - high_dividend_yield: 股息率 >= 3%
    - cash_healthy: 现金健康度为稳健/健康

    Args:
        indicators: 已计算的指标
        cash_health: 现金健康度结果

    Returns:
        健康标志列表
    """
    flags = []

    # high_roe
    roe = indicators.get('roe_avg_3y')
    if roe and roe >= 15:
        flags.append('high_roe')

    # stable_dividend
    consecutive_years = indicators.get('consecutive_dividend_years', 0)
    if consecutive_years and consecutive_years >= 5:
        flags.append('stable_dividend')

    # low_debt
    debt_ratio = indicators.get('debt_ratio')
    if debt_ratio is not None and debt_ratio < 0.5:
        flags.append('low_debt')

    # positive_fcf
    fcf_yield = indicators.get('fcf_yield')
    if fcf_yield is not None and fcf_yield > 0:
        flags.append('positive_fcf')

    # profit_quality
    profit_quality = indicators.get('profit_quality')
    if profit_quality and profit_quality >= 0.8:
        flags.append('profit_quality')

    # high_penetrating_yield
    py = indicators.get('penetrating_yield')
    if py and py >= 5:
        flags.append('high_penetrating_yield')

    # high_dividend_yield
    dividend_yield = indicators.get('dividend_yield')
    if dividend_yield and dividend_yield >= 3:
        flags.append('high_dividend_yield')

    # cash_healthy
    if cash_health:
        health_flag = cash_health.get('health_flag') or cash_health.get('status')
        if health_flag in ['稳健', '健康']:
            flags.append('cash_healthy')

    return flags
