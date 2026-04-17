"""
Rate limiter 抽象：进程内（dev / 单实例）+ Redis（prod / 多实例）。

Key 由 AuthContext.source + external_user_id 构成，每个 marketplace 用户独立预算；
这样 RapidAPI 代理带来的固定源 IP 不会造成集体 429。

Redis 后端用 Lua 脚本化的 sorted-set 滑动窗口：ZREMRANGEBYSCORE、ZCARD、ZADD、
EXPIRE 在一个原子 roundtrip 里完成。

失败策略：fail-open。Redis 不可达时 log 告警 + 放行请求。对 RapidAPI / Zyla 这
类 marketplace，返 503 会被踢出服务目录，代价远大于短暂越限。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from threading import Lock
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class RateLimiter(Protocol):
    name: str

    async def check(self, key: str, max_requests: int, window_seconds: int) -> Optional[int]:
        """允许 → None；超限 → retry-after 秒数 (>=1)。后端异常按"允许"处理（fail-open）。"""
        ...

    async def ping(self) -> bool:
        """Best-effort 健康检查，给 /health 用。永不抛异常。"""
        ...


class InMemoryRateLimiter:
    """进程本地滑动窗口。dev / 单实例 / Redis 降级时用。"""

    name = "in-memory"

    def __init__(self) -> None:
        self._store: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    async def check(self, key: str, max_requests: int, window_seconds: int) -> Optional[int]:
        now = time.time()
        window_start = now - window_seconds
        with self._lock:
            timestamps = [ts for ts in self._store[key] if ts > window_start]
            if len(timestamps) >= max_requests:
                oldest = min(timestamps)
                retry_after = max(1, int(oldest + window_seconds - now) + 1)
                self._store[key] = timestamps
                return retry_after
            timestamps.append(now)
            self._store[key] = timestamps
        return None

    async def ping(self) -> bool:
        return True


# KEYS[1] = 桶 key; ARGV = now, window, max, member(unique)
# 返回 -1 表示允许；正整数 = retry-after 秒。
_LUA_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_req = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= max_req then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry = 1
    if oldest and #oldest == 2 then
        local oldest_ts = tonumber(oldest[2])
        retry = math.max(1, math.ceil(oldest_ts + window - now))
    end
    return retry
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window + 1)
return -1
"""


class RedisRateLimiter:
    name = "redis"

    def __init__(self, redis_client) -> None:
        self._redis = redis_client
        self._script_sha: Optional[str] = None
        self._script_lock = asyncio.Lock()

    async def _ensure_script(self) -> str:
        if self._script_sha is None:
            async with self._script_lock:
                if self._script_sha is None:
                    self._script_sha = await self._redis.script_load(_LUA_SLIDING_WINDOW)
        return self._script_sha

    async def check(self, key: str, max_requests: int, window_seconds: int) -> Optional[int]:
        try:
            sha = await self._ensure_script()
            now = time.time()
            member = f"{now}:{uuid.uuid4().hex}"
            result = await self._redis.evalsha(
                sha, 1, key, now, window_seconds, max_requests, member,
            )
            result_int = int(result)
            if result_int == -1:
                return None
            return max(1, result_int)
        except Exception as e:
            logger.error("Redis rate-limit check failed key=%s err=%s; fail-open", key, e)
            return None

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception as e:
            logger.warning("Redis ping failed: %s", e)
            return False


def build_rate_limiter(redis_url: str) -> RateLimiter:
    """REDIS_URL 非空 + redis 可导入 → RedisRateLimiter；否则 InMemoryRateLimiter。"""
    if not redis_url:
        logger.warning(
            "REDIS_URL not set — using in-memory rate limiter "
            "(NOT safe for multi-instance deployments)"
        )
        return InMemoryRateLimiter()
    try:
        from redis.asyncio import from_url as redis_from_url
    except ImportError:
        logger.error("redis package not importable; using in-memory rate limiter")
        return InMemoryRateLimiter()
    try:
        client = redis_from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
        logger.info("Rate limiter backend: redis (url host=%s)", _safe_host(redis_url))
        return RedisRateLimiter(client)
    except Exception as e:
        logger.error("Failed to init Redis client (%s); using in-memory limiter", e)
        return InMemoryRateLimiter()


def _safe_host(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or "?"
    except Exception:
        return "?"


_limiter_singleton: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """进程级单例；首次调用按当前 Settings 构建。"""
    global _limiter_singleton
    if _limiter_singleton is None:
        from app.config import get_settings
        _limiter_singleton = build_rate_limiter(get_settings().redis_url)
    return _limiter_singleton


def reset_rate_limiter() -> None:
    """Test helper：清掉单例，下次 get_rate_limiter() 会基于当前 env 重建。"""
    global _limiter_singleton
    _limiter_singleton = None


def set_rate_limiter(limiter: RateLimiter) -> None:
    """Test helper：直接注入 limiter 实例。"""
    global _limiter_singleton
    _limiter_singleton = limiter
