"""
Rate limiter 单元测试：InMemory + Redis(fakeredis) 两套后端。

覆盖：
- 窗口内允许；超限返回 retry_after
- bearer/rapidapi key 相互隔离（不共享桶）
- Redis 断连 fail-open（异常 → None，不阻断请求）
- factory：REDIS_URL 空 → InMemory；有值 → Redis
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.rate_limiter import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    build_rate_limiter,
)


# ---------- InMemory ----------

def test_inmemory_allows_within_window():
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        assert asyncio.run(limiter.check("k", max_requests=3, window_seconds=60)) is None


def test_inmemory_blocks_over_limit_returns_retry_after():
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        asyncio.run(limiter.check("k", 3, 60))
    retry = asyncio.run(limiter.check("k", 3, 60))
    assert retry is not None
    assert retry >= 1


def test_inmemory_keys_are_isolated():
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        asyncio.run(limiter.check("a", 3, 60))
    # a 已满，b 仍应允许
    assert asyncio.run(limiter.check("b", 3, 60)) is None
    assert asyncio.run(limiter.check("a", 3, 60)) is not None


# ---------- Redis (fakeredis) ----------

@pytest.fixture
def fake_redis_client():
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def test_redis_allows_within_window(fake_redis_client):
    limiter = RedisRateLimiter(fake_redis_client)
    for _ in range(3):
        assert asyncio.run(limiter.check("k", 3, 60)) is None


def test_redis_blocks_over_limit_returns_retry_after(fake_redis_client):
    limiter = RedisRateLimiter(fake_redis_client)
    for _ in range(3):
        asyncio.run(limiter.check("k", 3, 60))
    retry = asyncio.run(limiter.check("k", 3, 60))
    assert retry is not None and retry >= 1


def test_redis_keys_are_isolated(fake_redis_client):
    limiter = RedisRateLimiter(fake_redis_client)
    for _ in range(3):
        asyncio.run(limiter.check("rate:rapidapi:alice", 3, 60))
    # bearer:lion 应独立、未受影响
    assert asyncio.run(limiter.check("rate:bearer:lion", 3, 60)) is None


def test_redis_fail_open_on_backend_error():
    """Redis 抛异常 → check 返回 None（fail-open）。"""

    class BrokenRedis:
        async def script_load(self, _):
            raise RuntimeError("redis down")

        async def evalsha(self, *_a, **_kw):
            raise RuntimeError("redis down")

    limiter = RedisRateLimiter(BrokenRedis())
    assert asyncio.run(limiter.check("k", 3, 60)) is None


def test_redis_ping_failure_returns_false():
    class BrokenRedis:
        async def ping(self):
            raise RuntimeError("nope")

    limiter = RedisRateLimiter(BrokenRedis())
    assert asyncio.run(limiter.ping()) is False


# ---------- Factory ----------

def test_build_rate_limiter_empty_url_returns_inmemory():
    limiter = build_rate_limiter("")
    assert isinstance(limiter, InMemoryRateLimiter)


def test_build_rate_limiter_valid_url_returns_redis():
    # 仅验证类型；真实连接不发起（from_url 是 lazy）。
    pytest.importorskip("redis")
    limiter = build_rate_limiter("redis://localhost:6379/0")
    assert isinstance(limiter, RedisRateLimiter)
