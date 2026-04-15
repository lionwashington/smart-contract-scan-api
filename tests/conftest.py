"""
pytest 公共 fixtures：
- 默认把 LLM / Slither 全 mock 掉（测试绝不真实调用外部 API）
- 允许按测试覆写 AUTH_ENABLED / API_KEY / RAPIDAPI_PROXY_SECRET
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# 强制 test 环境不读本地 .env（pydantic-settings 会读 cwd 里的 .env）
os.environ.setdefault("AUTH_ENABLED", "true")


def _reset_settings_cache() -> None:
    from app.config import get_settings
    get_settings.cache_clear()


@pytest.fixture
def set_env(monkeypatch):
    """用法：set_env(AUTH_ENABLED="false", API_KEY="x")"""
    def _apply(**kv):
        for k, v in kv.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, str(v))
        _reset_settings_cache()
    return _apply


@pytest.fixture
def client(set_env):
    """默认：AUTH_ENABLED=true, API_KEY=testkey, RAPIDAPI_PROXY_SECRET=testproxy"""
    set_env(AUTH_ENABLED="true", API_KEY="testkey", RAPIDAPI_PROXY_SECRET="testproxy")
    # mock 扫描流水线，避免真实调用 Slither / LLM
    from app.models.schemas import ScanResponse

    fake = ScanResponse(
        scan_id="fake",
        status="completed",
        summary="mocked",
        risk_score=0,
        vulnerabilities=[],
        gas_optimizations=[],
        scan_time_ms=1,
    )
    with patch("app.routers.scan.run_slither", return_value={"success": True, "findings": [], "raw": ""}), \
         patch("app.routers.scan.analyze_with_llm", return_value=fake):
        from app.main import app
        with TestClient(app) as c:
            yield c


@pytest.fixture
def client_open(set_env):
    """AUTH_ENABLED=false 的客户端。"""
    set_env(AUTH_ENABLED="false", API_KEY="", RAPIDAPI_PROXY_SECRET="")
    from app.models.schemas import ScanResponse

    fake = ScanResponse(
        scan_id="fake",
        status="completed",
        summary="mocked",
        risk_score=0,
        vulnerabilities=[],
        gas_optimizations=[],
        scan_time_ms=1,
    )
    with patch("app.routers.scan.run_slither", return_value={"success": True, "findings": [], "raw": ""}), \
         patch("app.routers.scan.analyze_with_llm", return_value=fake):
        from app.main import app
        with TestClient(app) as c:
            yield c
