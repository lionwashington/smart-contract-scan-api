"""
FastAPI 应用入口
- CORS 全开（MVP 阶段）
- Auth middleware（AUTH_ENABLED toggle + RapidAPI proxy secret + Bearer）
- 健康检查端点
- 注册扫描路由
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import auth_middleware
from app.config import get_settings
from app.routers import scan

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

# Auth / tier middleware（必须在 CORS 之后注册）
app.middleware("http")(auth_middleware)


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


@app.get("/health", tags=["health"], summary="健康检查")
async def health_check():
    """Railway 健康检查端点，返回服务状态。"""
    return {"status": "ok", "service": "smart-contract-scan-api"}
