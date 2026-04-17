"""
Auth middleware 测试矩阵：
 ON + 无 header       → 401 AUTH_REQUIRED
 ON + Bearer 正确     → 200
 ON + Bearer 错误     → 401
 ON + Proxy secret 对 → 200
 ON + Proxy secret 错 → 401
 OFF + 无 header      → 200 + X-Auth-Mode: disabled-test
 /health 在任何状态下都 200（无 X-Auth-Mode）
"""
from __future__ import annotations

SAMPLE = {
    "source_code": "pragma solidity ^0.8.0; contract T { uint x; }",
    "language": "en",
}


def test_auth_on_no_header_401(client):
    r = client.post("/api/v1/scan/sync", json=SAMPLE)
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "AUTH_REQUIRED"


def test_auth_on_bearer_ok(client):
    r = client.post(
        "/api/v1/scan/sync",
        json=SAMPLE,
        headers={"Authorization": "Bearer testkey"},
    )
    assert r.status_code == 200, r.text


def test_auth_on_bearer_wrong_401(client):
    r = client.post(
        "/api/v1/scan/sync",
        json=SAMPLE,
        headers={"Authorization": "Bearer nope"},
    )
    assert r.status_code == 401


def test_auth_on_proxy_secret_ok(client):
    r = client.post(
        "/api/v1/scan/sync",
        json=SAMPLE,
        headers={"X-RapidAPI-Proxy-Secret": "testproxy"},
    )
    assert r.status_code == 200, r.text


def test_auth_on_proxy_secret_wrong_401(client):
    r = client.post(
        "/api/v1/scan/sync",
        json=SAMPLE,
        headers={"X-RapidAPI-Proxy-Secret": "nope"},
    )
    assert r.status_code == 401


def test_auth_on_api_market_secret_ok(client):
    r = client.post(
        "/api/v1/scan/sync",
        json=SAMPLE,
        headers={
            "X-Abg-Proxy-Secret": "testabg",
            "X-Abg-Tier": "pro",
            "x-api-market-key": "cmo2laewe0001jj04uiwy3dk7",
        },
    )
    assert r.status_code == 200, r.text


def test_auth_on_api_market_secret_wrong_401(client):
    r = client.post(
        "/api/v1/scan/sync",
        json=SAMPLE,
        headers={"X-Abg-Proxy-Secret": "nope"},
    )
    assert r.status_code == 401


def test_auth_on_api_market_missing_buyer_key_anonymous_still_200(client):
    """API.market 买家 cuid 缺失时 adapter 填 external_user_id='anonymous'，
    不应因此拒绝请求——仅使 rate-limit 退化成"该通道共享桶"。"""
    r = client.post(
        "/api/v1/scan/sync",
        json=SAMPLE,
        headers={"X-Abg-Proxy-Secret": "testabg", "X-Abg-Tier": "free"},
    )
    assert r.status_code == 200, r.text


def test_auth_off_no_header_200_with_mode_header(client_open):
    r = client_open.post("/api/v1/scan/sync", json=SAMPLE)
    assert r.status_code == 200, r.text
    assert r.headers.get("x-auth-mode") == "disabled-test"


def test_health_live_always_open_auth_on(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "x-auth-mode" not in {k.lower() for k in r.headers.keys()}


def test_health_live_always_open_auth_off(client_open):
    r = client_open.get("/health/live")
    assert r.status_code == 200


def test_auth_on_get_scan_status_also_protected(client):
    r = client.get("/api/v1/scan/some-uuid")
    assert r.status_code == 401
