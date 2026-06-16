"""OAuth 订阅令牌定时刷新保活服务。

背景：codex/claude_code 的 refresh_token 是一次性轮换（rotated on each use），且
access_token 寿命较长（codex 实测约 16 天）。懒刷新（resolve 仅在 access 即将过期时
触发）会让 refresh_token 长时间闲置，一旦其自身寿命到期，等到首次刷新时就已失效（401）。

本服务提供后台定时任务：扫描所有绑定凭证，对「距上次刷新超过阈值」的主动 force_refresh，
让 refresh_token 滚动轮换续命，避免闲置过期。单条失败不影响其余，并在 DB 标记错误。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

logger = logging.getLogger("app.oauth_refresh")


async def refresh_due_oauth_credentials(threshold_days: int = 7) -> Dict[str, Any]:
    """对到期（距上次刷新超过 threshold_days）的 oauth 凭证主动刷新轮换。

    策略：按 last_refresh_at 阈值筛选——只刷新久未轮换的凭证，避免每天对所有凭证
    force_refresh（减少刷新失败暴露面、降低与懒刷新撞车概率）。从未刷新过
    （last_refresh_at 缺失）的凭证一律视为到期。

    单条凭证刷新失败（如 refresh_token 已失效）记 error 日志并在文档写入
    last_refresh_error / last_refresh_error_at，不中断对其余凭证的处理。

    Returns:
        统计字典：total（扫描数）/ due（到期数）/ refreshed / failed / failures。
    """
    # 延迟导入，避免模块级循环依赖（main 启动期注册 job 时才需要）
    from app.core.database import get_database
    from app.services import oauth_service

    collection = get_database()["user_oauth_credentials"]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=threshold_days)

    stats: Dict[str, Any] = {
        "total": 0,
        "due": 0,
        "refreshed": 0,
        "failed": 0,
        "failures": [],
    }

    cursor = collection.find(
        {}, {"user_id": 1, "provider": 1, "last_refresh_at": 1}
    )
    async for doc in cursor:
        stats["total"] += 1
        user_id = doc.get("user_id")
        provider = doc.get("provider")

        last_refresh = doc.get("last_refresh_at")
        if last_refresh is not None:
            if last_refresh.tzinfo is None:
                last_refresh = last_refresh.replace(tzinfo=timezone.utc)
            if last_refresh > cutoff:
                # 未到阈值，跳过
                continue

        stats["due"] += 1
        try:
            await oauth_service.resolve(
                collection, user_id, provider, force_refresh=True
            )
            stats["refreshed"] += 1
            logger.info(f"[oauth-refresh] 已刷新 ({user_id}, {provider})")
        except Exception as exc:  # noqa: BLE001 — 单条失败不应中断整体
            stats["failed"] += 1
            stats["failures"].append(
                {"user_id": user_id, "provider": provider, "error": str(exc)}
            )
            logger.error(f"[oauth-refresh] 刷新失败 ({user_id}, {provider}): {exc}")
            try:
                await collection.update_one(
                    {"user_id": user_id, "provider": provider},
                    {"$set": {"last_refresh_error": str(exc), "last_refresh_error_at": now}},
                )
            except Exception as mark_exc:  # noqa: BLE001
                logger.warning(f"[oauth-refresh] 标记刷新错误失败 ({user_id}, {provider}): {mark_exc}")

    logger.info(
        f"[oauth-refresh] 完成: 扫描 {stats['total']}, 到期 {stats['due']}, "
        f"刷新 {stats['refreshed']}, 失败 {stats['failed']}"
    )
    return stats
