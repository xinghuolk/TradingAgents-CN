import socket

import pytest

from app.core import redis_client


@pytest.mark.asyncio
async def test_init_redis_uses_platform_tcp_keepalive_constants(monkeypatch):
    captured = {}

    class FakeConnectionPool:
        @staticmethod
        def from_url(*args, **kwargs):
            captured.update(kwargs)
            return object()

    class FakeRedis:
        def __init__(self, connection_pool):
            self.connection_pool = connection_pool

        async def ping(self):
            return True

    monkeypatch.setattr(redis_client.redis, "ConnectionPool", FakeConnectionPool)
    monkeypatch.setattr(redis_client.redis, "Redis", FakeRedis)

    await redis_client.init_redis()

    expected_options = {
        getattr(socket, name)
        for name in ("TCP_KEEPIDLE", "TCP_KEEPINTVL", "TCP_KEEPCNT")
        if hasattr(socket, name)
    }
    assert set(captured["socket_keepalive_options"]) == expected_options

    redis_client.redis_pool = None
    redis_client.redis_client = None
