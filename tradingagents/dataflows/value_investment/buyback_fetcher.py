# -*- coding: utf-8 -*-
"""
回购数据获取模块

支持：
- AKShare 获取 A 股回购数据
- MongoDB 缓存（7天过期）
- 仅计入已注销的回购（真实回报）
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from tradingagents.utils.logging_init import get_logger

logger = get_logger("value_investment")

# 缓存过期时间（天）
CACHE_EXPIRE_DAYS = 7

# 全局单例
_buyback_fetcher_instance = None


class BuybackFetcher:
    """回购数据获取器"""

    def __init__(self, db=None):
        """
        初始化回购数据获取器

        Args:
            db: MongoDB 数据库实例（可选，用于缓存）
        """
        self.db = db
        self._cache_collection = "buyback_cache"

    async def _get_from_cache(self, stock_code: str) -> Optional[Dict]:
        """从缓存获取回购数据"""
        if not self.db:
            return None

        try:
            collection = self.db[self._cache_collection]
            cache_doc = await collection.find_one({"stock_code": stock_code})

            if cache_doc:
                cached_at = cache_doc.get("cached_at")
                if cached_at:
                    expire_time = cached_at + timedelta(days=CACHE_EXPIRE_DAYS)
                    if datetime.now() < expire_time:
                        logger.debug(f"回购数据缓存命中: {stock_code}")
                        return cache_doc.get("data")

            return None
        except Exception as e:
            logger.warning(f"读取回购缓存失败: {e}")
            return None

    async def _save_to_cache(self, stock_code: str, data: Dict) -> None:
        """保存回购数据到缓存"""
        if not self.db:
            return

        try:
            collection = self.db[self._cache_collection]
            await collection.update_one(
                {"stock_code": stock_code},
                {
                    "$set": {
                        "stock_code": stock_code,
                        "data": data,
                        "cached_at": datetime.now(),
                        "updated_at": datetime.now()
                    }
                },
                upsert=True
            )
            logger.debug(f"回购数据已缓存: {stock_code}")
        except Exception as e:
            logger.warning(f"保存回购缓存失败: {e}")

    async def fetch_buyback_data(self, stock_code: str, market: str = "A") -> Optional[Dict]:
        """
        获取股票回购数据

        Args:
            stock_code: 股票代码
            market: 市场类型（A=A股, HK=港股）

        Returns:
            回购数据字典：
            - records: 回购记录列表
            - total_cancelled_amount: 已注销总金额
            - latest_year_amount: 最近一年回购金额
        """
        # 1. 先尝试从缓存获取
        cached_data = await self._get_from_cache(stock_code)
        if cached_data:
            return cached_data

        # 2. 从数据源获取
        if market == "A":
            data = await self._fetch_a_stock_buyback(stock_code)
        elif market == "HK":
            data = await self._fetch_hk_stock_buyback(stock_code)
        else:
            logger.warning(f"不支持的市场类型: {market}")
            return None

        # 3. 保存到缓存
        if data:
            await self._save_to_cache(stock_code, data)

        return data

    async def _fetch_a_stock_buyback(self, stock_code: str) -> Optional[Dict]:
        """
        获取 A 股回购数据

        使用东方财富回购数据，重点关注已注销的回购
        """
        import akshare as ak

        records = []
        total_cancelled_amount = 0
        latest_year_amount = 0

        pure_code = stock_code.split('.')[0]
        current_year = datetime.now().year

        try:
            # 方案1：获取回购实施情况
            df = await asyncio.to_thread(ak.stock_repurchase_em)

            if df is not None and len(df) > 0:
                # 筛选当前股票的回购记录
                stock_df = df[df['代码'].str.contains(pure_code, na=False)]

                for _, row in stock_df.iterrows():
                    try:
                        # 解析回购金额
                        amount_str = str(row.get('已回购金额', '0'))
                        amount = self._parse_amount(amount_str)

                        # 解析回购进度/状态
                        status = str(row.get('实施进度', ''))
                        is_cancelled = '注销' in status or '已完成' in status

                        # 解析公告日期
                        announce_date = row.get('首次公告日', '')
                        year = None
                        if announce_date:
                            try:
                                if isinstance(announce_date, str):
                                    year = int(announce_date[:4])
                                else:
                                    year = announce_date.year
                            except:
                                year = current_year

                        record = {
                            'year': year,
                            'amount': amount,
                            'status': status,
                            'is_cancelled': is_cancelled,
                            'source': '东方财富'
                        }
                        records.append(record)

                        # 统计已注销金额
                        if is_cancelled and amount:
                            total_cancelled_amount += amount

                        # 统计最近一年金额
                        if year and year >= current_year - 1 and amount:
                            latest_year_amount += amount

                    except Exception as e:
                        logger.debug(f"解析回购记录失败: {e}")
                        continue

                logger.info(f"{stock_code} 获取到 {len(records)} 条回购记录，已注销金额: {total_cancelled_amount/1e8:.2f}亿")

        except Exception as e:
            logger.debug(f"获取A股回购数据失败: {e}")

        # 方案2：尝试从股本变动推算回购（更准确但数据可能不全）
        if not records:
            try:
                # 获取股本变动数据
                df = await asyncio.to_thread(
                    ak.stock_share_change_em,
                    symbol=pure_code
                )

                if df is not None and len(df) > 0:
                    for _, row in df.iterrows():
                        try:
                            change_reason = str(row.get('变动原因', ''))
                            if '回购' in change_reason and '注销' in change_reason:
                                # 计算注销金额 = 注销股数 × 当时股价（估算）
                                shares_changed = abs(float(row.get('变动数量', 0) or 0))
                                # 这里简化处理，实际应该用当时的股价
                                if shares_changed > 0:
                                    records.append({
                                        'year': current_year,
                                        'shares_cancelled': shares_changed,
                                        'is_cancelled': True,
                                        'source': '股本变动'
                                    })
                        except:
                            continue

            except Exception as e:
                logger.debug(f"股本变动数据获取失败: {e}")

        return {
            'records': records,
            'total_cancelled_amount': total_cancelled_amount,
            'latest_year_amount': latest_year_amount,
            'has_active_buyback': any(not r.get('is_cancelled', True) for r in records)
        }

    async def _fetch_hk_stock_buyback(self, stock_code: str) -> Optional[Dict]:
        """获取港股回购数据"""
        # 港股回购数据暂时返回空，后续可以接入其他数据源
        logger.debug(f"港股回购数据暂不支持: {stock_code}")
        return {
            'records': [],
            'total_cancelled_amount': 0,
            'latest_year_amount': 0,
            'has_active_buyback': False
        }

    def _parse_amount(self, amount_str: str) -> float:
        """
        解析金额字符串

        支持格式：
        - "1.5亿" -> 150000000
        - "1500万" -> 15000000
        - "15000000" -> 15000000
        """
        if not amount_str or amount_str == '--':
            return 0

        try:
            amount_str = str(amount_str).strip()

            if '亿' in amount_str:
                return float(amount_str.replace('亿', '').strip()) * 1e8
            elif '万' in amount_str:
                return float(amount_str.replace('万', '').strip()) * 1e4
            else:
                return float(amount_str)
        except:
            return 0

    def calculate_buyback_yield(
        self,
        buyback_data: Dict,
        market_cap: float,
        use_cancelled_only: bool = True
    ) -> float:
        """
        计算回购收益率

        Args:
            buyback_data: 回购数据
            market_cap: 总市值
            use_cancelled_only: 是否只计算已注销的回购（推荐）

        Returns:
            回购收益率（%）
        """
        if not market_cap or market_cap <= 0:
            return 0

        if use_cancelled_only:
            # 只计入已注销的回购（真实股东回报）
            amount = buyback_data.get('total_cancelled_amount', 0)
        else:
            # 计入最近一年所有回购
            amount = buyback_data.get('latest_year_amount', 0)

        if amount <= 0:
            return 0

        return (amount / market_cap) * 100


def get_buyback_fetcher(db=None) -> BuybackFetcher:
    """获取回购数据获取器单例"""
    global _buyback_fetcher_instance
    if _buyback_fetcher_instance is None:
        _buyback_fetcher_instance = BuybackFetcher(db)
    return _buyback_fetcher_instance
