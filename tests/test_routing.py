"""
Tier-based 模型路由测试：
- X-RapidAPI-Subscription header → tier 映射
- tier → LLM (base_url / api_key / model) 解析
- analyze_with_llm 收到正确的 tier 参数
- 响应里带上 tier / model_used
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import RAPIDAPI_TIER_MAP, resolve_tier
from app.config import Settings

SAMPLE = {
    "source_code": "pragma solidity ^0.8.0; contract T { uint x; }",
    "language": "en",
}


# ---------- resolve_tier（纯函数） ----------

@pytest.mark.parametrize("header,expected", [
    ("BASIC", "free"),
    ("PRO", "starter"),
    ("ULTRA", "pro"),
    ("MEGA", "business"),
    ("basic", "free"),          # 大小写不敏感
    ("  PRO  ", "starter"),     # 去空白
    ("UNKNOWN", "free"),        # 非法值 fallback
    ("", "free"),               # 空字符串 fallback
    (None, "free"),             # 缺失 header fallback
])
def test_resolve_tier_mapping(header, expected):
    class FakeReq:
        def __init__(self, h):
            self.headers = h
    headers = {"x-rapidapi-subscription": header} if header is not None else {}
    assert resolve_tier(FakeReq(headers)) == expected


def test_rapidapi_tier_map_contents():
    assert RAPIDAPI_TIER_MAP == {
        "BASIC": "free",
        "PRO": "starter",
        "ULTRA": "pro",
        "MEGA": "business",
    }


# ---------- Settings.resolve_llm ----------

def test_resolve_llm_fallback_to_global(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://global/v1")
    monkeypatch.setenv("LLM_API_KEY", "globalkey")
    monkeypatch.setenv("LLM_MODEL", "global-model")
    for t in ("FREE", "STARTER", "PRO", "BUSINESS"):
        monkeypatch.delenv(f"LLM_MODEL_{t}", raising=False)
        monkeypatch.delenv(f"LLM_BASE_URL_{t}", raising=False)
        monkeypatch.delenv(f"LLM_API_KEY_{t}", raising=False)
    s = Settings()
    for tier in ("free", "starter", "pro", "business"):
        assert s.resolve_llm(tier) == ("https://global/v1", "globalkey", "global-model")


def test_resolve_llm_per_tier_override(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://global/v1")
    monkeypatch.setenv("LLM_API_KEY", "globalkey")
    monkeypatch.setenv("LLM_MODEL", "global-model")
    monkeypatch.setenv("LLM_MODEL_PRO", "claude-sonnet-4-6")
    monkeypatch.setenv("LLM_BASE_URL_PRO", "https://anthropic/v1")
    monkeypatch.setenv("LLM_API_KEY_PRO", "anthropic-key")
    # 只配 MODEL_FREE，另两项 fallback
    monkeypatch.setenv("LLM_MODEL_FREE", "deepseek-v3p1")
    s = Settings()
    assert s.resolve_llm("pro") == ("https://anthropic/v1", "anthropic-key", "claude-sonnet-4-6")
    assert s.resolve_llm("free") == ("https://global/v1", "globalkey", "deepseek-v3p1")


def test_resolve_llm_invalid_tier_falls_back_to_free(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "global-model")
    monkeypatch.setenv("LLM_MODEL_FREE", "free-model")
    s = Settings()
    _, _, model = s.resolve_llm("bogus")
    assert model == "free-model"


# ---------- end-to-end: header → analyze_with_llm 收到正确 tier ----------

@pytest.fixture
def capture_client(set_env):
    """复刻 client fixture，但把 analyze_with_llm 替成 spy 以捕获 tier 参数。"""
    set_env(
        AUTH_ENABLED="true",
        API_KEY="testkey",
        RAPIDAPI_PROXY_SECRET="testproxy",
        LLM_MODEL="global-model",
        LLM_MODEL_FREE="free-model",
        LLM_MODEL_STARTER="starter-model",
        LLM_MODEL_PRO="pro-model",
        LLM_MODEL_BUSINESS="business-model",
    )
    from app.models.schemas import ScanResponse

    captured: dict = {}

    def fake_analyze(**kwargs):
        captured["tier"] = kwargs.get("tier")
        return ScanResponse(
            scan_id=kwargs["scan_id"],
            status="completed",
            summary="mocked",
            risk_score=0,
            vulnerabilities=[],
            gas_optimizations=[],
            scan_time_ms=1,
            tier=kwargs.get("tier"),
            model_used=f"{kwargs.get('tier')}-model",
        )

    with patch("app.routers.scan.run_slither", return_value={"success": True, "findings": [], "raw": ""}), \
         patch("app.routers.scan.analyze_with_llm", side_effect=fake_analyze):
        from app.main import app
        with TestClient(app) as c:
            yield c, captured


@pytest.mark.parametrize("sub_header,expected_tier", [
    ("BASIC", "free"),
    ("PRO", "starter"),
    ("ULTRA", "pro"),
    ("MEGA", "business"),
    ("UNKNOWN", "free"),
    (None, "free"),
])
def test_subscription_header_routes_tier(capture_client, sub_header, expected_tier):
    client, captured = capture_client
    headers = {"Authorization": "Bearer testkey"}
    if sub_header is not None:
        headers["X-RapidAPI-Subscription"] = sub_header
    r = client.post("/api/v1/scan/sync", json=SAMPLE, headers=headers)
    assert r.status_code == 200, r.text
    assert captured["tier"] == expected_tier
    body = r.json()
    assert body["tier"] == expected_tier
    assert body["model_used"] == f"{expected_tier}-model"


def test_rapidapi_channel_also_routes_tier(capture_client):
    """走 RapidAPI proxy secret 通道时，tier 也要正确解析。"""
    client, captured = capture_client
    r = client.post(
        "/api/v1/scan/sync",
        json=SAMPLE,
        headers={
            "X-RapidAPI-Proxy-Secret": "testproxy",
            "X-RapidAPI-Subscription": "MEGA",
        },
    )
    assert r.status_code == 200, r.text
    assert captured["tier"] == "business"
