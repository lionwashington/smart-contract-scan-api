"""
FastAPI 应用入口
- CORS 全开（MVP 阶段）
- Auth middleware（AUTH_ENABLED toggle + RapidAPI proxy secret + Bearer）
- 健康检查端点
- 注册扫描路由
"""
import logging
import shutil
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth import install_auth
from app.config import get_settings
from app.routers import scan
from app.services.rate_limiter import get_rate_limiter

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Smart Contract Security Scan API",
    description="提交 Solidity 智能合约，获取 AI 驱动的安全审计报告。",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS：MVP 阶段全开，生产环境应限制 origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth / tier middleware（必须在 CORS 之后注册）—— 走 api-billing-gateway
install_auth(app)


@app.on_event("startup")
async def _log_auth_mode() -> None:
    s = get_settings()
    mode = "production" if s.auth_enabled else "disabled-test"
    has_direct = bool(s.api_key)
    has_proxy = bool(s.rapidapi_proxy_secret)
    logger.info(
        "AUTH_ENABLED=%s mode=%s direct_key=%s proxy_secret=%s",
        s.auth_enabled, mode, has_direct, has_proxy,
    )
    if s.auth_enabled and not has_direct and not has_proxy:
        logger.critical(
            "AUTH_ENABLED=true 但 API_KEY 和 RAPIDAPI_PROXY_SECRET 都未配置——所有 /api/* 将返回 401。"
        )


# 注册路由
app.include_router(scan.router, prefix="/api/v1", tags=["scan"])


@app.get("/health/live", tags=["health"], summary="Liveness probe")
async def health_live():
    """
    轻量存活探针：只要进程能处理请求就 200。

    Railway / k8s 用这个决定是否重启容器——Redis 挂了不该导致容器被杀
    （那样只会放大事故）。
    """
    return {"status": "ok", "service": "smart-contract-scan-api"}


@app.get("/health", tags=["health"], summary="Deep health check")
async def health_check():
    """
    深度健康检查：验证 Redis + slither 依赖可用。

    marketplace（RapidAPI / Zyla）的 uptime 监控轮询这个端点，直接决定 SLA
    分成。任何依赖异常 → 503 degraded，让监控主动切流；但 rate-limit 的
    fail-open 行为不变（请求仍放行）。
    """
    checks = {}
    overall_ok = True

    # Redis: ping 单独计时并容错
    try:
        redis_ok = await get_rate_limiter().ping()
        checks["rate_limiter"] = {
            "backend": get_rate_limiter().name,
            "status": "ok" if redis_ok else "error",
        }
        if not redis_ok:
            overall_ok = False
    except Exception as e:
        checks["rate_limiter"] = {"backend": "?", "status": "error", "error": str(e)}
        overall_ok = False

    # Slither: 仅 which，避免真跑分析
    slither_path = shutil.which("slither")
    checks["slither"] = {"status": "ok" if slither_path else "missing"}
    if not slither_path:
        overall_ok = False

    body = {
        "status": "ok" if overall_ok else "degraded",
        "service": "smart-contract-scan-api",
        "checks": checks,
    }
    return JSONResponse(status_code=200 if overall_ok else 503, content=body)
