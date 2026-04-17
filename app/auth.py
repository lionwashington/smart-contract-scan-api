"""
Auth wiring for scanner — delegates to api-billing-gateway (abg) adapters.

Scanner-specific middleware class that re-reads Settings each request (so test
fixtures can toggle env between requests). Internally uses abg's adapters +
AuthContext + PlanTier to do the actual rule work.

Exports preserved for tests:
- `RAPIDAPI_TIER_MAP`, `resolve_tier`: pure helpers.
- `install_auth(app)`: wires middleware onto app.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from api_billing_gateway import AuthContext, PlanTier
from api_billing_gateway.adapters import ProxySecretAdapter, StaticBearerAdapter
from api_billing_gateway.context import AuthError

from app.config import get_settings

logger = logging.getLogger(__name__)

AUTH_EXEMPT_PATHS = {"/health", "/health/live", "/", "/docs", "/redoc", "/openapi.json"}

RAPIDAPI_TIER_MAP = {
    "BASIC": "free",
    "PRO": "starter",
    "ULTRA": "pro",
    "MEGA": "business",
}

_TIER_MAP_ENUM = {
    "BASIC": PlanTier.FREE,
    "PRO": PlanTier.STARTER,
    "ULTRA": PlanTier.PRO,
    "MEGA": PlanTier.BUSINESS,
}

# API.market seller 在 Custom Headers wizard 里填的是 Abg 约定值（free/starter/pro/business）；
# adapter 内部会做 upper()，此处用大写 key。
_API_MARKET_TIER_MAP = {
    "FREE": PlanTier.FREE,
    "STARTER": PlanTier.STARTER,
    "PRO": PlanTier.PRO,
    "BUSINESS": PlanTier.BUSINESS,
}


def resolve_tier(request: Request) -> str:
    raw = request.headers.get("x-rapidapi-subscription") or ""
    return RAPIDAPI_TIER_MAP.get(raw.strip().upper(), "free")


def _is_protected(path: str) -> bool:
    if path in AUTH_EXEMPT_PATHS:
        return False
    if path.startswith("/docs") or path.startswith("/redoc"):
        return False
    return path.startswith("/api/")


def _build_adapters() -> list:
    s = get_settings()
    adapters = []
    if s.rapidapi_proxy_secret:
        adapters.append(ProxySecretAdapter(
            name="rapidapi",
            secret_value=s.rapidapi_proxy_secret,
            secret_header="X-RapidAPI-Proxy-Secret",
            user_header="X-RapidAPI-User",
            tier_header="X-RapidAPI-Subscription",
            tier_map=_TIER_MAP_ENUM,
        ))
    if s.api_market_proxy_secret:
        # API.market Custom Headers wizard 注入 X-Abg-Proxy-Secret + X-Abg-Tier；
        # 平台并不提供统一 user_id 头，这里用买家侧 x-api-market-key（cuid）做 external_user_id，
        # 既能让 rate-limit 按买家隔离，又不要求平台合作。买家没传则 adapter fallback 到 "anonymous"。
        adapters.append(ProxySecretAdapter(
            name="api_market",
            secret_value=s.api_market_proxy_secret,
            secret_header="X-Abg-Proxy-Secret",
            user_header="x-api-market-key",
            tier_header="X-Abg-Tier",
            tier_map=_API_MARKET_TIER_MAP,
        ))
    if s.api_key:
        adapters.append(StaticBearerAdapter(
            name="bearer",
            api_key=s.api_key,
            default_tier=PlanTier.FREE,
            tier_header="X-RapidAPI-Subscription",
            tier_map=_TIER_MAP_ENUM,
        ))
    return adapters


def _mirror_legacy_state(request: Request, ctx: AuthContext) -> None:
    request.state.billing = ctx
    request.state.tier = ctx.tier.value
    request.state.auth_mode = (
        "rapidapi" if ctx.source == "rapidapi"
        else "direct" if ctx.source == "bearer"
        else ctx.mode
    )


async def auth_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    path = request.url.path
    # 所有请求都先解析 tier（旧行为兼容：健康检查/豁免路径也能读）
    request.state.tier = resolve_tier(request)

    if not _is_protected(path):
        return await call_next(request)

    s = get_settings()

    if not s.auth_enabled:
        ctx = AuthContext(
            source="disabled",
            external_user_id="dev",
            tier=PlanTier.safe(request.state.tier, PlanTier.FREE),
            raw_tier=request.state.tier,
            mode="disabled-test",
        )
        _mirror_legacy_state(request, ctx)
        ip = request.client.host if request.client else "?"
        logger.warning("Scan served in unauth test mode path=%s ip=%s", path, ip)
        response = await call_next(request)
        response.headers["X-Auth-Mode"] = "disabled-test"
        return response

    for adapter in _build_adapters():
        if not adapter.matches(request):
            continue
        try:
            ctx = adapter.authenticate(request)
        except AuthError:
            # scanner 对外统一成 401 AUTH_REQUIRED，不暴露 adapter 细分
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
        _mirror_legacy_state(request, ctx)
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


def install_auth(app: FastAPI) -> None:
    app.middleware("http")(auth_middleware)
