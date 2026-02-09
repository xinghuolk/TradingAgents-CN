# -*- coding: utf-8 -*-
"""
价值投资数据流模块

基于「龟龟投资」穿透回报率模型，提供：
- 分红数据获取
- 回购数据获取
- 穿透回报率计算
- 现金健康度评估
- 5维健康评分系统
"""

from .dividend_fetcher import DividendFetcher, get_dividend_fetcher
from .buyback_fetcher import BuybackFetcher, get_buyback_fetcher
from .penetrating_yield import PenetratingYieldCalculator
from .cash_health import CashHealthCalculator
from .health_score import HealthScoreCalculator, generate_health_flags
from .config import INDUSTRY_CONSERVATIVE_FACTORS, INDUSTRY_HEALTH_THRESHOLDS
from .config import get_conservative_factor, get_health_thresholds
from .report_data_mapper import map_extracted_reports_to_financial_data, merge_financial_data

__all__ = [
    'DividendFetcher',
    'get_dividend_fetcher',
    'BuybackFetcher',
    'get_buyback_fetcher',
    'PenetratingYieldCalculator',
    'CashHealthCalculator',
    'HealthScoreCalculator',
    'generate_health_flags',
    'INDUSTRY_CONSERVATIVE_FACTORS',
    'INDUSTRY_HEALTH_THRESHOLDS',
    'get_conservative_factor',
    'get_health_thresholds',
    'map_extracted_reports_to_financial_data',
    'merge_financial_data',
]
