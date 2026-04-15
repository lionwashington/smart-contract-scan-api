"""
Auth middleware.

两条合法通道（AUTH_ENABLED=true 时）：
- RapidAPI 网关转发：header X-RapidAPI-Proxy-Secret == RAPIDAPI_PROXY_SECRET
- 直连 bearer：header Authorization: Bearer <API_KEY>

AUTH_ENABLED=false 时允许裸访问，但在响应加 X-Auth-Mode: disabled-test。
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.config import get_settings

logger = logging.getLogger(__name__)

# 跳过 auth 的路径
AUTH_EXEMPT_PATHS = {"/health", "/", "/docs", "/redoc", "/openapi.json"}

# RapidAPI 订阅档位 → 内部 tier 标签
RAPIDAPI_TIER_MAP = {
    "BASIC": "free",
    "PRO": "starter",
    "ULTRA": "pro",
    "MEGA": "business",
}


def resolve_tier(request: Request) -> str:
    """从 X-RapidAPI-Subscription 提取 tier；无/非法 → free。"""
    raw = request.headers.get("x-rapidapi-subscription") or ""
    return RAPIDAPI_TIER_MAP.get(raw.strip().upper(), "free")


def _is_protected(path: str) -> bool:
    if path in AUTH_EXEMPT_PATHS:
        return False
    if path.startswith("/docs") or path.startswith("/redoc"):
        return False
    return path.startswith("/api/")


async def auth_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    path = request.url.path
    # 提前解析 tier（所有请求都附上，未来若有白名单路径也能用）
    request.state.tier = resolve_tier(request)

    if not _is_protected(path):
        return await call_next(request)

    s = get_settings()

    if not s.auth_enabled:
        request.state.auth_mode = "disabled-test"
        ip = request.client.host if request.client else "?"
        logger.warning("Scan served in unauth test mode path=%s ip=%s", path, ip)
        response = await call_next(request)
        response.headers["X-Auth-Mode"] = "disabled-test"
        return response

    # 生产模式：二选一
    proxy_secret = request.headers.get("x-rapidapi-proxy-secret")
    if s.rapidapi_proxy_secret and proxy_secret and proxy_secret == s.rapidapi_proxy_secret:
        request.state.auth_mode = "rapidapi"
        return await call_next(request)

    authz = request.headers.get("authorization") or ""
    if s.api_key and authz:
        scheme, _, token = authz.partition(" ")
        if scheme.lower() == "bearer" and token == s.api_key:
            request.state.auth_mode = "direct"
            return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={
            "code": "AUTH_REQUIRED",
            "message": (
                "Request must come via RapidAPI (X-RapidAPI-Proxy-Secret) "
                "or carry a valid direct API key (Authorization: Bearer ...)."
            ),
        },
    )
