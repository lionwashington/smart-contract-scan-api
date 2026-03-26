"""
FastAPI 应用入口
- CORS 全开（MVP 阶段）
- 健康检查端点
- 注册扫描路由
"""
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import scan

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

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

# 注册路由
app.include_router(scan.router, prefix="/api/v1", tags=["scan"])


@app.get("/health", tags=["health"], summary="健康检查")
async def health_check():
    """Railway 健康检查端点，返回服务状态。"""
    return {"status": "ok", "service": "smart-contract-scan-api"}
