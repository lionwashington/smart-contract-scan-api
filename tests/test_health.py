"""
健康检查测试：
- /health/live 永远 200（进程存活）
- /health 深度检查：Redis + slither 都 OK → 200 ok；任一异常 → 503 degraded
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_health_live_returns_200_no_dependencies(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "smart-contract-scan-api"


def test_health_deep_all_ok_returns_200(client):
    """Redis ping OK + slither 存在 → 200 ok。"""
    with patch("app.main.shutil.which", return_value="/usr/bin/slither"):
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["rate_limiter"]["status"] == "ok"
    assert body["checks"]["slither"]["status"] == "ok"


def test_health_deep_missing_slither_returns_503(client):
    with patch("app.main.shutil.which", return_value=None):
        r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["slither"]["status"] == "missing"


def test_health_deep_redis_down_returns_503(client):
    """limiter.ping() 返 False → 503 degraded；请求路径的 fail-open 不受影响。"""
    from app.services.rate_limiter import InMemoryRateLimiter, set_rate_limiter

    class AlwaysDownLimiter(InMemoryRateLimiter):
        name = "redis"

        async def ping(self):  # type: ignore[override]
            return False

    set_rate_limiter(AlwaysDownLimiter())
    with patch("app.main.shutil.which", return_value="/usr/bin/slither"):
        r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["rate_limiter"]["status"] == "error"


def test_health_deep_redis_raises_returns_503(client):
    """limiter.ping() 抛异常 → 同样 503，不应把异常透传出去。"""
    from app.services.rate_limiter import InMemoryRateLimiter, set_rate_limiter

    class ExplodingLimiter(InMemoryRateLimiter):
        name = "redis"

        async def ping(self):  # type: ignore[override]
            raise RuntimeError("network partition")

    set_rate_limiter(ExplodingLimiter())
    with patch("app.main.shutil.which", return_value="/usr/bin/slither"):
        r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["rate_limiter"]["status"] == "error"
