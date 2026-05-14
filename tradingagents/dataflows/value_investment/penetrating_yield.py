# -*- coding: utf-8 -*-
"""
穿透回报率计算模块

核心算法：
穿透回报率 = 分红收益率(可持续) + 注销型回购收益率
          = (保守净利润 × 有效支付率) / 总市值 + 回购金额 / 总市值

三层计算：
1. 保守净利润计算器 - 单期/多期中位数 × 行业保守系数
2. 有效支付率计算器 - 官方数据 > 历史中位×0.8 > 0
3. 穿透回报率计算 - 综合分红和回购
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from statistics import median

from .config import get_conservative_factor
from tradingagents.utils.logging_init import get_logger

logger = get_logger("value_investment")


@dataclass
class ConservativeNetProfitResult:
    """保守净利润计算结果"""
    n_cons: float           # 保守净利润
    n_1: Optional[float]    # 单期口径
    n_2: Optional[float]    # 多期口径
    method: str             # 使用的口径
    factor: float           # 使用的保守系数


@dataclass
class PenetratingYieldResult:
    """穿透回报率计算结果"""
    penetrating_yield: float      # 穿透回报率(%)
    dividend_yield: float         # 分红收益率(%)
    buyback_yield: float          # 回购收益率(%)
    payout_ratio: float           # 有效支付率
    conservative_profit: float    # 保守净利润
    payout_source: str            # 支付率来源
    profit_method: str            # 净利润计算方法


class PenetratingYieldCalculator:
    """穿透回报率计算器"""

    def __init__(self):
        pass

    def calculate_conservative_net_profit(
        self,
        net_profits: List[float],
        industry: str = 'default'
    ) -> Optional[ConservativeNetProfitResult]:
        """
        计算保守净利润 N_cons

        算法:
        - 单期口径: N_1 = N_last × conservative_factor
        - 多期口径: N_2 = Median(近5年净利) × conservative_factor
        - 取低: N_cons = min(N_1, N_2)

        Args:
            net_profits: 按时间倒序排列的净利润列表 [最新, 次新, ...]
            industry: 行业分类

        Returns:
            保守净利润计算结果
        """
        if not net_profits:
            return None

        factor = get_conservative_factor(industry)

        # 过滤无效值
        valid_profits = [p for p in net_profits if p is not None and p > 0]
        if not valid_profits:
            return None

        # 单期口径: 最新一期净利润 × 保守系数
        n_last = valid_profits[0]
        n_1 = float(n_last) * factor

        # 多期口径: 近5年中位数 × 保守系数
        n_2 = None
        if len(valid_profits) >= 3:  # 至少3年数据才计算多期口径
            profits_for_median = valid_profits[:5]  # 取最近5年
            median_profit = median(profits_for_median)
            n_2 = float(median_profit) * factor

        # 取低
        if n_1 is not None and n_2 is not None:
            n_cons = min(n_1, n_2)
            method = '单期口径' if n_cons == n_1 else '多期口径'
        elif n_1 is not None:
            n_cons = n_1
            method = '单期口径'
        elif n_2 is not None:
            n_cons = n_2
            method = '多期口径'
        else:
            return None

        return ConservativeNetProfitResult(
            n_cons=n_cons,
            n_1=n_1,
            n_2=n_2,
            method=method,
            factor=factor
        )

    def calculate_penetrating_yield(
        self,
        market_cap: float,
        conservative_profit: float,
        effective_payout_ratio: float,
        payout_source: str,
        buyback_amount: float = 0,
        profit_method: str = '单期口径'
    ) -> Optional[PenetratingYieldResult]:
        """
        计算穿透回报率

        公式:
        穿透回报率 = (保守净利润 × 有效支付率) / 总市值 + 回购金额 / 总市值

        Args:
            market_cap: 总市值
            conservative_profit: 保守净利润
            effective_payout_ratio: 有效支付率
            payout_source: 支付率来源说明
            buyback_amount: 已注销回购金额
            profit_method: 净利润计算方法

        Returns:
            穿透回报率计算结果
        """
        if not market_cap or market_cap <= 0:
            logger.warning("市值无效，无法计算穿透回报率")
            return None

        if not conservative_profit or conservative_profit <= 0:
            logger.warning("保守净利润无效，无法计算穿透回报率")
            return None

        # 分红收益率 = (保守净利润 × 有效支付率) / 总市值
        dividend_income = conservative_profit * effective_payout_ratio
        dividend_yield = (dividend_income / market_cap) * 100

        # 回购收益率 = 回购金额 / 总市值
        buyback_yield = 0
        if buyback_amount and buyback_amount > 0:
            buyback_yield = (buyback_amount / market_cap) * 100

        # 穿透回报率 = 分红收益率 + 回购收益率
        penetrating_yield = dividend_yield + buyback_yield

        logger.info(
            f"穿透回报率计算: {penetrating_yield:.2f}% = "
            f"分红{dividend_yield:.2f}% + 回购{buyback_yield:.2f}%"
        )

        return PenetratingYieldResult(
            penetrating_yield=penetrating_yield,
            dividend_yield=dividend_yield,
            buyback_yield=buyback_yield,
            payout_ratio=effective_payout_ratio,
            conservative_profit=conservative_profit,
            payout_source=payout_source,
            profit_method=profit_method
        )

    def calculate_full_analysis(
        self,
        financial_data: Dict[str, Any],
        dividend_data: Dict[str, Any],
        buyback_data: Dict[str, Any],
        market_data: Dict[str, Any],
        industry: str = 'default'
    ) -> Optional[Dict[str, Any]]:
        """
        完整的穿透回报率分析

        Args:
            financial_data: 财务数据 {
                'net_profits': [最新, 次新, ...],  # 近5年净利润
                'free_cash_flow': float,           # 自由现金流
                'operating_cash_flow': float,      # 经营现金流
                ...
            }
            dividend_data: 分红数据（来自 DividendFetcher）
            buyback_data: 回购数据（来自 BuybackFetcher）
            market_data: 市场数据 {
                'market_cap': float,   # 总市值
                'close_price': float,  # 收盘价
            }
            industry: 行业分类

        Returns:
            完整分析结果
        """
        # 1. 计算保守净利润
        net_profits = financial_data.get('net_profits', [])
        n_cons_result = self.calculate_conservative_net_profit(net_profits, industry)

        if not n_cons_result:
            logger.warning("无法计算保守净利润")
            return None

        # 2. 计算有效支付率
        from .dividend_fetcher import DividendFetcher
        fetcher = DividendFetcher()

        fcf = financial_data.get('free_cash_flow')
        official_ratio = dividend_data.get('avg_payout_ratio_3y')

        payout_result = fetcher.get_effective_payout_ratio(
            dividend_data=dividend_data,
            net_profit=n_cons_result.n_cons,
            fcf=fcf,
            official_ratio=official_ratio if official_ratio else None
        )

        # 3. 计算回购金额
        buyback_amount = buyback_data.get('total_cancelled_amount', 0)

        # 4. 计算穿透回报率
        market_cap = market_data.get('market_cap', 0)

        py_result = self.calculate_penetrating_yield(
            market_cap=market_cap,
            conservative_profit=n_cons_result.n_cons,
            effective_payout_ratio=payout_result['r_eff'],
            payout_source=payout_result['source'],
            buyback_amount=buyback_amount,
            profit_method=n_cons_result.method
        )

        if not py_result:
            return None

        # 5. 汇总结果
        return {
            'penetrating_yield': {
                'total': py_result.penetrating_yield,
                'dividend_yield': py_result.dividend_yield,
                'buyback_yield': py_result.buyback_yield,
                'payout_ratio': py_result.payout_ratio,
                'payout_source': py_result.payout_source,
                'conservative_profit': py_result.conservative_profit,
                'profit_method': py_result.profit_method,
            },
            'conservative_profit_detail': {
                'n_cons': n_cons_result.n_cons,
                'n_1': n_cons_result.n_1,
                'n_2': n_cons_result.n_2,
                'method': n_cons_result.method,
                'factor': n_cons_result.factor,
            },
            'dividend_info': {
                'consecutive_years': dividend_data.get('consecutive_years', 0),
                'total_dividend_years': dividend_data.get('total_dividend_years', 0),
                'avg_payout_ratio_3y': dividend_data.get('avg_payout_ratio_3y', 0),
            },
            'buyback_info': {
                'total_cancelled_amount': buyback_amount,
                'has_active_buyback': buyback_data.get('has_active_buyback', False),
            },
            'market_info': {
                'market_cap': market_cap,
                'close_price': market_data.get('close_price', 0),
            }
        }
