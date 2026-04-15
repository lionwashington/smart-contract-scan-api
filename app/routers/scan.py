"""
扫描路由：
- POST /scan           异步提交，立即返回 scan_id
- POST /scan/sync      同步扫描，阻塞直到完成（给"一行 curl"卖点）
- GET  /scan/{scan_id} 查询异步任务状态

Auth 在 app.auth.auth_middleware 中完成。
"""
import asyncio
import time
import uuid
import logging
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks

from app.config import get_settings
from app.models.schemas import (
    ScanRequest,
    ScanResponse,
    ScanQueuedResponse,
    ScanStatusResponse,
)
from app.services.slither_service import run_slither
from app.services.llm_service import analyze_with_llm
from app.services.task_store import get_task_store

logger = logging.getLogger(__name__)

router = APIRouter()

# ---- 限流状态（内存，进程级别，MVP 够用） ----
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    _rate_limit_store[ip] = [ts for ts in _rate_limit_store[ip] if ts > window_start]
    if len(_rate_limit_store[ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_MAX} requests per {RATE_LIMIT_WINDOW}s per IP",
        )
    _rate_limit_store[ip].append(now)


def _validate_body(body: ScanRequest, max_size: int) -> None:
    if not body.source_code.strip():
        raise HTTPException(status_code=400, detail="source_code cannot be empty")
    code_bytes = len(body.source_code.encode("utf-8"))
    if code_bytes > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"Contract too large: {code_bytes} bytes (max {max_size} bytes)",
        )


async def _run_scan(scan_id: str, body: ScanRequest) -> ScanResponse:
    """核心扫描流水线：Slither → LLM。"""
    start_ms = int(time.time() * 1000)

    try:
        slither_result = await asyncio.to_thread(run_slither, body.source_code)
    except Exception as e:
        logger.exception("Slither 执行异常 scan_id=%s: %s", scan_id, e)
        slither_result = {"success": False, "error": str(e), "findings": [], "raw": ""}

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
    response.scan_time_ms = int(time.time() * 1000) - start_ms
    return response


async def _run_scan_background(scan_id: str, body: ScanRequest) -> None:
    """BackgroundTasks 调用入口：结果写 task_store。"""
    store = get_task_store()
    store.mark_running(scan_id)
    try:
        response = await _run_scan(scan_id, body)
        store.set_result(scan_id, response.model_dump())
        logger.info(
            "异步扫描完成 scan_id=%s 耗时=%dms 风险=%d 漏洞=%d",
            scan_id, response.scan_time_ms, response.risk_score, len(response.vulnerabilities),
        )
    except Exception as e:
        logger.exception("异步扫描失败 scan_id=%s: %s", scan_id, e)
        store.set_error(scan_id, str(e))


def _get_client_ip(request: Request) -> str:
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
    return ip.split(",")[0].strip()


@router.post(
    "/scan",
    response_model=ScanQueuedResponse,
    summary="异步提交扫描任务",
    description="立即返回 scan_id，后台运行。用 GET /scan/{scan_id} 轮询结果。",
    status_code=202,
)
async def scan_contract_async(
    request: Request,
    body: ScanRequest,
    background_tasks: BackgroundTasks,
) -> ScanQueuedResponse:
    settings = get_settings()
    _check_rate_limit(_get_client_ip(request))
    _validate_body(body, settings.max_contract_size)

    scan_id = str(uuid.uuid4())
    get_task_store().create(scan_id)
    background_tasks.add_task(_run_scan_background, scan_id, body)

    logger.info("入队 scan_id=%s contract=%s", scan_id, body.contract_name)
    return ScanQueuedResponse(
        scan_id=scan_id,
        status="queued",
        poll_url=f"/api/v1/scan/{scan_id}",
    )


@router.post(
    "/scan/sync",
    response_model=ScanResponse,
    summary="同步扫描（阻塞直到完成）",
    description="一行 curl 即拿结果；典型耗时 8-15s。超过 RapidAPI 默认超时的请用异步端点。",
)
async def scan_contract_sync(
    request: Request,
    body: ScanRequest,
) -> ScanResponse:
    settings = get_settings()
    _check_rate_limit(_get_client_ip(request))
    _validate_body(body, settings.max_contract_size)

    scan_id = str(uuid.uuid4())
    logger.info("同步扫描 scan_id=%s contract=%s", scan_id, body.contract_name)
    response = await _run_scan(scan_id, body)
    logger.info(
        "同步完成 scan_id=%s 耗时=%dms 风险=%d 漏洞=%d",
        scan_id, response.scan_time_ms, response.risk_score, len(response.vulnerabilities),
    )
    return response


@router.get(
    "/scan/{scan_id}",
    response_model=ScanStatusResponse,
    summary="查询异步扫描结果",
)
async def get_scan_status(
    scan_id: str,
) -> ScanStatusResponse:
    task = get_task_store().get(scan_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"scan_id not found: {scan_id}")

    result = None
    if task["status"] == "completed" and task["result"]:
        result = ScanResponse(**task["result"])

    return ScanStatusResponse(
        scan_id=scan_id,
        status=task["status"],
        result=result,
        error=task["error"],
    )
