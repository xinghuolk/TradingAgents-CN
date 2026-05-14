# -*- coding: utf-8 -*-
"""
价值投资模块配置

包含行业保守系数、健康度阈值等配置
"""

# 行业保守系数配置
# 用于计算保守净利润时的折扣系数
INDUSTRY_CONSERVATIVE_FACTORS = {
    # 稳定行业 - 系数 0.8（现金流稳定）
    '公用事业': 0.8,
    '银行': 0.8,
    '食品饮料': 0.8,
    '医药生物': 0.8,
    '消费': 0.8,
    '医药': 0.8,

    # 周期行业 - 系数 0.65（景气波动大）
    '房地产': 0.65,
    '建筑材料': 0.65,
    '机械设备': 0.65,
    '周期': 0.65,

    # 强周期行业 - 系数 0.6
    '钢铁': 0.6,
    '煤炭': 0.6,
    '有色金属': 0.6,
    '有色': 0.6,

    # 默认值
    'default': 0.7
}

# 行业健康度阈值配置
# 用于判断现金健康度（稳健/健康/警示/不健康）
INDUSTRY_HEALTH_THRESHOLDS = {
    # 稳定行业 - 阈值相对宽松
    '公用事业': {'cov_div_min': 1.0, 'buffer_months_min': 12},
    '银行': {'cov_div_min': 1.0, 'buffer_months_min': 12},

    # 消费行业 - 中等阈值
    '食品饮料': {'cov_div_min': 1.2, 'buffer_months_min': 18},
    '消费': {'cov_div_min': 1.2, 'buffer_months_min': 18},
    '医药生物': {'cov_div_min': 1.2, 'buffer_months_min': 18},
    '医药': {'cov_div_min': 1.2, 'buffer_months_min': 18},

    # 周期行业 - 更高阈值
    '周期': {'cov_div_min': 1.5, 'buffer_months_min': 24},
    '钢铁': {'cov_div_min': 1.5, 'buffer_months_min': 24},
    '煤炭': {'cov_div_min': 1.5, 'buffer_months_min': 24},
    '有色金属': {'cov_div_min': 1.5, 'buffer_months_min': 24},
    '有色': {'cov_div_min': 1.5, 'buffer_months_min': 24},

    # 默认值
    'default': {'cov_div_min': 1.0, 'buffer_months_min': 12}
}


def get_conservative_factor(industry: str) -> float:
    """获取行业保守系数"""
    return INDUSTRY_CONSERVATIVE_FACTORS.get(industry, INDUSTRY_CONSERVATIVE_FACTORS['default'])


def get_health_thresholds(industry: str) -> dict:
    """获取行业健康度阈值"""
    return INDUSTRY_HEALTH_THRESHOLDS.get(industry, INDUSTRY_HEALTH_THRESHOLDS['default'])
