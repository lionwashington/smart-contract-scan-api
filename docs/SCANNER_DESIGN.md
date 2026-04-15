# Scanner — Auth & Deployment Design

统一扫描服务的鉴权开关、RapidAPI 网关流量校验、直连测试通道的完整设计。**必须先实现并验证完毕**再提交 RapidAPI。

## 1. 架构

```
                       ┌──────────────────────────┐
 付费用户              │                          │
 ─────────> RapidAPI ──┤  adds header:            │
            Gateway    │  X-RapidAPI-Proxy-Secret ├──> Railway (FastAPI)
                       │                          │    smart-contract-scan-api
                       └──────────────────────────┘         │
 运维/Lion 直连                                              │
 ───────────────────> Authorization: Bearer <API_KEY> ──────┘
                                                            │
 测试期临时（AUTH_ENABLED=false）                            │
 ───────────────────> 无 header 也允许 ─────────────────────┘
```

两条合法路径（生产）：
- **RapidAPI 网关**：用户付费给 RapidAPI，RapidAPI 代理请求并自动注入 `X-RapidAPI-Proxy-Secret: <你在后台设的值>`
- **直连运维**：你本人用 `Authorization: Bearer <API_KEY>` 打 Railway URL（监控、调试、batch）

## 2. Auth 流程

```
请求进入 (除 /health 外所有 /api/v1/*)
   │
   ▼
AUTH_ENABLED ?
   │
   ├── false (测试模式)
   │      │
   │      ▼
   │   在 response header 写 X-Auth-Mode: disabled-test
   │   logger.warning 记录 "unauth request from <ip>"
   │   放行
   │
   └── true (生产模式)
          │
          ▼
       header X-RapidAPI-Proxy-Secret == RAPIDAPI_PROXY_SECRET ?
          │
          ├── yes → 放行（tag source=rapidapi）
          │
          └── no → header Authorization: Bearer <token>
                     │
                     ├── token == API_KEY → 放行（tag source=direct）
                     │
                     └── 其它 → 401 {"code":"AUTH_REQUIRED","detail":"..."}
```

## 3. env var 清单

| env var | 用途 | 默认 | 示例 |
|---|---|---|---|
| `AUTH_ENABLED` | 总开关。`true`/`false`（大小写不敏感） | **见下节建议** | `true` |
| `API_KEY` | 直连通道的 Bearer token（你自己用） | `""` | `sk_live_a1b2...` 32 字节 hex |
| `RAPIDAPI_PROXY_SECRET` | RapidAPI 网关注入的 header 值 | `""` | `rapi_xyz...` 32 字节 hex |
| `LLM_BASE_URL` | 已有 | `https://api.openai.com/v1` | `https://api.fireworks.ai/inference/v1` |
| `LLM_MODEL` | 已有 | `claude-sonnet-4-6` | `accounts/fireworks/models/deepseek-v3p1` |

生成 secret：`openssl rand -hex 32`

### 默认值：两个选项，Lion 选

- **选项 A（fail-safe 向"开放"）**：`AUTH_ENABLED` 未设置 → false。
  - 好处：本地 dev、首次部署不会被锁死。
  - 坏处：Railway 如果忘了设，生产也是开的——**这正是我们现在的坑**。
- **选项 B（fail-safe 向"关闭"）** ← **推荐**：`AUTH_ENABLED` 未设置 → true，但如果 `API_KEY` 和 `RAPIDAPI_PROXY_SECRET` 都为空，启动时 `logger.critical` 并仍然 401 所有请求。
  - 好处：生产默认安全；忘了配 secret = 服务直接拒所有请求，一眼能发现。
  - 坏处：本地 dev 必须显式 `AUTH_ENABLED=false`（可写进 `.env.example`）。

推荐 **B**，因为这次教训就是"静默放行"的。Dev 麻烦一次，永远不再裸跑生产。

## 4. 代码改动

| 文件 | 改动 | 行数 |
|---|---|---|
| `app/config.py` | 加 `auth_enabled: bool = True`（或 False，见上），`rapidapi_proxy_secret: str = ""` | +2 |
| `app/routers/scan.py` | 重写 `_verify_api_key` 函数 | ~25 |
| `app/main.py` | 启动时 log 当前 auth 模式（便于 Railway logs 确认） | +5 |
| `.env.example` | 新增（文档） | +10 |
| `tests/test_auth.py` | 4 种组合 unit test（见 §6） | ~60 |

关键函数（`scan.py:49`）重写：

```python
def _verify_api_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_rapidapi_proxy_secret: Optional[str] = Header(None, alias="X-RapidAPI-Proxy-Secret"),
) -> None:
    s = get_settings()
    if not s.auth_enabled:
        logger.warning("unauth request (AUTH_ENABLED=false) path=%s ip=%s",
                       request.url.path, request.client.host if request.client else "?")
        return
    # 生产模式
    if s.rapidapi_proxy_secret and x_rapidapi_proxy_secret == s.rapidapi_proxy_secret:
        return
    if s.api_key and authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token == s.api_key:
            return
    raise HTTPException(
        status_code=401,
        detail={"code": "AUTH_REQUIRED",
                "message": "Request must come via RapidAPI or carry a valid direct API key."},
    )
```

总工作量：**~2h**（含单测 + Railway 部署验证）。

## 5. Railway 操作指南（给 Lion）

1. `openssl rand -hex 32`（跑两次，得两个值）
2. Railway Dashboard → 服务 → Variables：
   - `AUTH_ENABLED=true`
   - `API_KEY=<第一个随机值>`      ← 你自己收好，别提交 git
   - `RAPIDAPI_PROXY_SECRET=<第二个随机值>`  ← 待 RapidAPI 后台同步填
3. 保存 → 自动 redeploy（~90s）
4. Deployments 状态转 active 后跑 §6 测试矩阵
5. 上 RapidAPI Provider Dashboard → API Settings → "Secret for your API" 填**第二个值**（和 Railway `RAPIDAPI_PROXY_SECRET` 一致）

## 6. 测试矩阵（4 种组合）

`BASE=https://smart-contract-scan-api-production.up.railway.app`

| # | AUTH_ENABLED | 请求携带 | 期望 | 命令 |
|---|---|---|---|---|
| 1 | true | 无 header | **401** `AUTH_REQUIRED` | `curl -s -o /dev/null -w "%{http_code}" $BASE/api/v1/scan/sync -d '{...}'` |
| 2 | true | `Authorization: Bearer <API_KEY>` | **200** | `curl -H "Authorization: Bearer $API_KEY" ...` |
| 3 | true | `X-RapidAPI-Proxy-Secret: <SECRET>` | **200** | `curl -H "X-RapidAPI-Proxy-Secret: $SECRET" ...` |
| 4 | false | 无 header | **200** + `X-Auth-Mode: disabled-test` | `curl -i ... \| grep -i x-auth-mode` |
| 5 | true | `Authorization: Bearer wrong` | **401** | — |
| 6 | true | `X-RapidAPI-Proxy-Secret: wrong` | **401** | — |

`/health` 在所有组合下都必须 200（RapidAPI 健康探针 + 监控需要）。

## 7. 发布前检查清单

- [ ] 代码改动合并到 master（auth 逻辑 + 启动日志）
- [ ] Railway 三个 env var 已设 + redeploy active
- [ ] 测试矩阵 6 项全过
- [ ] Railway logs 能看到 `AUTH_ENABLED=true mode=production` 启动行
- [ ] RapidAPI Provider "Secret for your API" 已填（与 Railway `RAPIDAPI_PROXY_SECRET` 一致）
- [ ] RapidAPI 后台 "Test Endpoint" 能跑通（说明网关→后端 secret 对上了）
- [ ] 用一个不带任何 header 的裸 curl 再跑一次 Railway URL → 必须 401
- [ ] Rate limit 仍然生效（12 个并发 POST，第 11/12 应 429）
- [ ] 日志不打印 secret / api_key / Authorization 完整值

全部 ✅ 才 Submit for review。

## 8. 未来扩展（不在本次范围）

- 按来源区分配额：`X-RapidAPI-User` header（RapidAPI 注入）→ per-user 计数
- Request signing（HMAC）代替明文 secret，若 RapidAPI 支持
- Structured audit log → Loki / Grafana
