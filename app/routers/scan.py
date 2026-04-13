"""
扫描路由：POST /api/v1/scan
- 可选 Bearer Token 鉴权
- 简单内存限流（每 IP 每分钟最多 10 次）
- 调用 Slither + LLM，返回 ScanResponse
"""
import asyncio
import time
import uuid
import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Header, Depends

from app.config import get_settings
from app.models.schemas import ScanRequest, ScanResponse
from app.services.slither_service import run_slither
from app.services.llm_service import analyze_with_llm

logger = logging.getLogger(__name__)

router = APIRouter()

# ---- 限流状态（内存，进程级别，MVP 够用） ----
# 结构：{ip: [(timestamp, count), ...]}
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 10       # 每窗口最大请求数
RATE_LIMIT_WINDOW = 60    # 窗口大小（秒）


def _check_rate_limit(ip: str) -> None:
    """检查限流，超限则抛出 429。"""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # 过滤掉窗口外的旧记录
    _rate_limit_store[ip] = [ts for ts in _rate_limit_store[ip] if ts > window_start]

    if len(_rate_limit_store[ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_MAX} requests per {RATE_LIMIT_WINDOW}s per IP",
        )

    _rate_limit_store[ip].append(now)


def _verify_api_key(authorization: Optional[str] = Header(None)) -> None:
    """
    如果配置了 API_KEY 环境变量，则验证 Bearer Token。
    未配置则跳过（开放访问）。
    """
    settings = get_settings()
    if not settings.api_key:
        return  # 未配置，开放访问

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


@router.post(
    "/scan",
    response_model=ScanResponse,
    summary="扫描智能合约安全漏洞",
    description="提交 Solidity 源代码，返回详细的安全审计报告。",
)
async def scan_contract(
    request: Request,
    body: ScanRequest,
    _: None = Depends(_verify_api_key),
) -> ScanResponse:
    """
    主扫描端点：
    1. 限流检查
    2. 合约大小检查
    3. 运行 Slither 静态分析
    4. 调用 LLM 深度解读
    5. 返回结构化报告
    """
    settings = get_settings()

    # 获取客户端 IP（兼容代理）
    client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    client_ip = client_ip.split(",")[0].strip()

    # 限流
    _check_rate_limit(client_ip)

    # 合约大小检查
    code_bytes = len(body.source_code.encode("utf-8"))
    if code_bytes > settings.max_contract_size:
        raise HTTPException(
            status_code=413,
            detail=f"Contract too large: {code_bytes} bytes (max {settings.max_contract_size} bytes)",
        )

    if not body.source_code.strip():
        raise HTTPException(status_code=400, detail="source_code cannot be empty")

    scan_id = str(uuid.uuid4())
    start_ms = int(time.time() * 1000)

    logger.info("开始扫描 scan_id=%s, ip=%s, contract=%s", scan_id, client_ip, body.contract_name)

    # Step 1: Slither 静态分析（同步调用，用 to_thread 避免阻塞事件循环）
    try:
        slither_result = await asyncio.to_thread(run_slither, body.source_code)
    except Exception as e:
        logger.exception("Slither 执行异常: %s", e)
        slither_result = {
            "success": False,
            "error": str(e),
            "findings": [],
            "raw": "",
        }

    # Step 2: LLM 深度分析（同步调用，用 to_thread 避免阻塞事件循环）
    scan_time_ms = int(time.time() * 1000) - start_ms

    response = await asyncio.to_thread(
        analyze_with_llm,
        source_code=body.source_code,
        slither_result=slither_result,
        contract_name=body.contract_name,
        language=body.language,
        scan_id=scan_id,
        scan_time_ms=scan_time_ms,
    )

    # 更新最终耗时（含 LLM 调用）
    response.scan_time_ms = int(time.time() * 1000) - start_ms

    logger.info(
        "扫描完成 scan_id=%s, 耗时=%dms, 风险分=%d, 漏洞数=%d",
        scan_id,
        response.scan_time_ms,
        response.risk_score,
        len(response.vulnerabilities),
    )

    return response
