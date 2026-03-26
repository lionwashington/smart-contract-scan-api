"""
Web3 Pay计费客户端
- 在执行扫描前调用Web3 Pay扣费
- Web3 Pay关闭时直接放行（本地开发）
"""
import httpx
import logging
from app.config import get_settings

logger = logging.getLogger(__name__)

# 复用HTTP客户端
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def check_and_deduct(
    api_key: str,
    endpoint: str,
    client_ip: str = "unknown",
) -> dict:
    """
    调用Web3 Pay扣费接口。

    Returns:
        {
            "allowed": True/False,
            "balance_cents": int,
            "error": str | None,
        }
    """
    settings = get_settings()

    # 计费开关关闭时直接放行
    if not settings.web3pay_enabled:
        return {"allowed": True, "balance_cents": -1, "error": None}

    client = _get_client()
    url = f"{settings.web3pay_url}/api/v1/billing/deduct"

    try:
        response = await client.post(
            url,
            json={
                "cost_cents": settings.scan_cost_cents,
                "endpoint": endpoint,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Forwarded-For": client_ip,
            },
        )

        data = response.json()

        if response.status_code == 200:
            return {
                "allowed": True,
                "balance_cents": data.get("balance_cents", 0),
                "error": None,
            }
        elif response.status_code == 402:
            return {
                "allowed": False,
                "balance_cents": data.get("balance_cents", 0),
                "error": data.get("error", "Insufficient balance"),
            }
        elif response.status_code == 401:
            return {
                "allowed": False,
                "balance_cents": 0,
                "error": "Invalid API key for Web3 Pay",
            }
        elif response.status_code == 429:
            return {
                "allowed": False,
                "balance_cents": 0,
                "error": "Rate limit exceeded",
            }
        else:
            logger.error("Web3 Pay unexpected response: %d %s", response.status_code, data)
            # Fail open: allow the request if billing service has issues
            return {"allowed": True, "balance_cents": -1, "error": None}

    except httpx.TimeoutException:
        logger.error("Web3 Pay billing timeout")
        # Fail open on timeout
        return {"allowed": True, "balance_cents": -1, "error": None}
    except Exception as e:
        logger.error("Web3 Pay billing error: %s", e)
        # Fail open on error
        return {"allowed": True, "balance_cents": -1, "error": None}
