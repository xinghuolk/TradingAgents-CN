"""oauth_refresh_service 的 hermetic 单测（mock motor collection + oauth_service.resolve）。"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.oauth_refresh_service import refresh_due_oauth_credentials


class _AsyncCursor:
    """模拟 motor find() 返回的 async 可迭代游标。"""
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()


def _make_collection(docs):
    coll = MagicMock()
    coll.find = MagicMock(return_value=_AsyncCursor(docs))
    coll.update_one = AsyncMock()
    return coll


def _run(docs, resolve_mock, threshold_days=7):
    coll = _make_collection(docs)
    db = {"user_oauth_credentials": coll}
    with patch("app.core.database.get_database", return_value=db), \
         patch("app.services.oauth_service.resolve", new=resolve_mock):
        stats = asyncio.run(refresh_due_oauth_credentials(threshold_days))
    return stats, coll


def test_skips_recently_refreshed():
    now = datetime.now(timezone.utc)
    docs = [{"user_id": "u1", "provider": "codex", "last_refresh_at": now - timedelta(days=2)}]
    resolve = AsyncMock()
    stats, _ = _run(docs, resolve)
    assert stats["total"] == 1
    assert stats["due"] == 0
    resolve.assert_not_called()


def test_refreshes_stale():
    now = datetime.now(timezone.utc)
    docs = [{"user_id": "u1", "provider": "codex", "last_refresh_at": now - timedelta(days=10)}]
    resolve = AsyncMock()
    stats, _ = _run(docs, resolve)
    assert stats["due"] == 1
    assert stats["refreshed"] == 1
    resolve.assert_awaited_once()


def test_never_refreshed_is_due():
    docs = [{"user_id": "u1", "provider": "codex"}]  # 无 last_refresh_at
    resolve = AsyncMock()
    stats, _ = _run(docs, resolve)
    assert stats["due"] == 1
    assert stats["refreshed"] == 1


def test_failure_recorded_not_interrupt():
    now = datetime.now(timezone.utc)
    docs = [
        {"user_id": "u1", "provider": "codex", "last_refresh_at": now - timedelta(days=10)},
        {"user_id": "u2", "provider": "claude_code", "last_refresh_at": now - timedelta(days=10)},
    ]

    async def _resolve_side(coll, uid, prov, force_refresh=False):
        if uid == "u1":
            raise Exception("HTTP Error 401: Unauthorized")

    resolve = AsyncMock(side_effect=_resolve_side)
    stats, coll = _run(docs, resolve)
    assert stats["failed"] == 1
    assert stats["refreshed"] == 1  # u2 仍被处理，未被 u1 失败中断
    assert stats["failures"][0]["user_id"] == "u1"
    # 失败的 u1 写入了 last_refresh_error
    coll.update_one.assert_awaited()
