# Scanner — Design

Smart Contract Scan API 的鉴权、计费、限流、健康检查与多实例部署的完整设计。
**必须先实现并通过 §8 自查清单**再提交 marketplace。

---

## 1. 架构

```
 ┌────────────── 多 marketplace 付费用户 ────────────────┐
 │                                                       │
 │  RapidAPI ──┐                                         │
 │             │                                         │
 │  Zyla API ──┼── adds gateway-specific header ─────┐   │
 │             │   (e.g. X-RapidAPI-Proxy-Secret)    │   │
 │  …（未来）──┘                                     │   │
 │                                                   ▼   │
 └───────────────────────────────────────── Railway (FastAPI) ──┐
                                              scanner            │
 运维/监控/Lion 直连                             │                │
 ──────────────────> Authorization: Bearer <API_KEY> ───────────┘
                                                 │
 Dev 测试期临时 (AUTH_ENABLED=false)             │
 ──────────────────> 任何请求直放行 ─────────────┘
                                                 │
                                           ┌─────▼──────┐
                                           │ Redis      │
                                           │ (sliding-  │
                                           │ window     │
                                           │ rate-limit)│
                                           └────────────┘
```

**关键设计点**
- **单一后端，多网关**：用户感知的 marketplace 不同，scanner 侧只看 `AuthContext.source`，通过 `api-billing-gateway` 的 adapter 注册表分发。
- **Bearer 通道为内部专用**：`source="bearer"` 永远不对公众暴露；给 Lion 运维/监控/冒烟用。
- **多实例 = Redis 必需**：rate-limit 必须跨实例共享桶，否则每实例独自计数，真实阈值 = 配置值 × 实例数。
- **Platform-edge quota for now**：订阅/配额由 marketplace（RapidAPI、Zyla…）自己执行；scanner 只做 **防滥用型 rate-limit**，不自己管"每月 300 次"这种配额。详见 §4。

---

## 2. Auth 与 AuthContext

请求鉴权由 `api-billing-gateway` 库驱动（见 `app/auth.py:install_auth`）。中间件对每次请求：

1. 路径在豁免集合 (`/health`, `/health/live`, `/`, `/docs`, `/redoc`, `/openapi.json`) → 直接放行。
2. `AUTH_ENABLED=false` → 构造 `AuthContext(source="disabled", external_user_id="dev", tier=free)`，响应打 `X-Auth-Mode: disabled-test`。
3. 否则遍历已注册 adapter 列表，匹配到的第一个 adapter 负责认证：
   - `ProxySecretAdapter`（RapidAPI / 未来 Zyla 同款）：验 `X-RapidAPI-Proxy-Secret`，读 `X-RapidAPI-User`（external_user_id）+ `X-RapidAPI-Subscription`（tier）。
   - `StaticBearerAdapter`（bearer）：验 `Authorization: Bearer <API_KEY>`，external_user_id 固定为 "owner"。
4. 无任何 adapter 匹配 → 401 `AUTH_REQUIRED`（不暴露 adapter 细节）。

认证产物是 `AuthContext`，字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | `"rapidapi" \| "bearer" \| "zyla" \| "disabled"` | 网关来源（即 adapter 名） |
| `external_user_id` | `str` | 网关侧的用户 ID；rapidapi 用 `X-RapidAPI-User`，bearer 固定 "owner" |
| `tier` | `PlanTier` enum | `FREE / STARTER / PRO / BUSINESS` |
| `raw_tier` | `str` | 未归一化的原值（日志追溯用） |
| `mode` | `str` | adapter 自填，审计用 |

中间件把 `ctx` 写进 `request.state.billing`，下游（LLM routing、rate-limit）就地取用。

**RapidAPI tier 映射**（`app/auth.py:RAPIDAPI_TIER_MAP`）：

| `X-RapidAPI-Subscription` | `tier` |
|---------------------------|--------|
| BASIC | free |
| PRO | starter |
| ULTRA | pro |
| MEGA | business |
| *其它 / 空* | free（降级保底） |

---

## 3. Tier-based LLM Routing

`Settings.resolve_llm(tier)` 按 tier 取 `(base_url, api_key, model)`；per-tier 字段为空 → fallback 到全局 `LLM_*`。

典型搭法（成本分层）：
- `free / starter` → 便宜快模型（Fireworks DeepSeek 等）
- `pro / business` → Claude Sonnet 或同级

handler 从 `request.state.tier`（middleware 写入，等于 `ctx.tier.value`）取 tier 传给 LLM 服务。`_tier_of(request)` 是唯一入口。

---

## 4. Quota & Rate Limiting

### 4.1 职责分层

| 层 | 负责 | 机制 |
|----|------|------|
| **Marketplace (RapidAPI, Zyla, …)** | 订阅配额（"Pro 档每月 300 次"）、超额计费、账单分成 | 网关自己的计量台，**scanner 不感知** |
| **Scanner self-hosted** | 防滥用型 **rate-limit**（60s 内 N 次），防 DoS、防卡住 LLM 池 | Redis 滑动窗口 |

MVP 阶段 scanner **不自己管月度配额**——靠 marketplace 的 platform-edge quota。未来若要跨 gateway 同步配额才会开新模块（见 §9）。

### 4.2 rate-limit 实现

代码：`app/services/rate_limiter.py`。

```python
RateLimiter (Protocol)
 ├── InMemoryRateLimiter   # dev / 单实例 / fallback
 └── RedisRateLimiter      # prod（多实例必需）
```

- **Key**：`rate:{ctx.source}:{ctx.external_user_id}` — 每个 marketplace 用户一个独立桶，RapidAPI 共享代理 IP 不会造成集体 429。
- **桶容量**：
  - 默认（marketplace 源）：`RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW`（默认 10 / 60s）
  - bearer 源：`RATE_LIMIT_MAX_BEARER`（默认 600 / 60s，放宽是因为这条路只给自用脚本走）
- **算法**：Redis sorted-set 滑动窗口，Lua 脚本原子化 `ZREMRANGEBYSCORE + ZCARD + ZADD + EXPIRE`。EVALSHA 缓存避免每次送 Lua body。
- **Fail-open**：Redis 异常 → log error + 放行。理由：marketplace（尤其 Zyla，SLA <95% 归零分成）对 503 极敏感，短暂越限代价远小于被踢出服务目录。

### 4.3 fallback 行为

| 场景 | 行为 |
|------|------|
| `REDIS_URL` 为空 | InMemoryRateLimiter；启动 WARN；多实例会各算各的（不要跑 prod） |
| redis 包 import 失败 | 同上，启动 ERROR |
| Redis 运行期挂掉 | 每次 check 都 log error 放行（fail-open） |
| `AuthContext` 缺失（豁免路径偶发、disabled 模式） | 退化成按 IP 限流（同 `X-Forwarded-For` 首段） |

---

## 5. 支持的网关（v0）

| Gateway | 状态 | 接入方式 | 配额侧 | 备注 |
|---------|------|----------|--------|------|
| **RapidAPI** | ✅ live | `ProxySecretAdapter` | platform-edge | 首发网关，付费档 Free / Starter / Pro / Business |
| **Bearer（内部）** | ✅ live | `StaticBearerAdapter` | 无（仅 rate-limit） | Lion 运维 / 监控 / 冒烟用，不对外发布 |
| **Zyla** | ⏳ 待 billing-researcher 确认 | 预计复用 `ProxySecretAdapter`（RapidAPI 同款 header 族） | platform-edge | 研究确证 header 族 + quota 上报后接入 |
| APYHub / APILayer / API.market | ⏳ 调研中 | 视 quota 支持决定是否接入 | — | 不支持 platform-edge quota 的网关 v0 不上 |

> v0 整体原则（Lion 2026-04-17）：**只接入自己承担配额管理的网关**。不承担配额的 gateway 留给 v1（那时再评估要不要 scanner 自建配额台）。

---

## 6. 健康检查端点

| 端点 | 用途 | 检查 | 失败行为 |
|------|------|------|----------|
| `/health/live` | Railway 容器存活探针 | 只验进程能响应 | 永远 200 |
| `/health` | marketplace SLA 监控（RapidAPI / Zyla 轮询） | `rate_limiter.ping()` + `slither` 可执行 | 任一异常 → 503 `{"status":"degraded","checks":{…}}` |

**为什么拆**：Redis 抖动不应让 Railway 重启容器（重启只放大事故 + 加 cold-start）。而 marketplace 应在 Redis 挂时**立刻** degrade，主动分流保 SLA。scanner 内部对 Redis 依旧 fail-open（见 §4.2），/health 仅上报状态、不阻塞业务路径。

---

## 7. 环境变量

| env | 必须 | 默认 | 用途 |
|-----|------|------|------|
| `AUTH_ENABLED` | ⚠ 生产务必 `true` | `true` | 总开关。false 下所有请求放行并打 `X-Auth-Mode: disabled-test` |
| `API_KEY` | 选 | `""` | 内部 bearer token（openssl rand -hex 32） |
| `RAPIDAPI_PROXY_SECRET` | RapidAPI 上必须 | `""` | RapidAPI Dashboard → API Settings → "Secret for your API" 同值 |
| `REDIS_URL` | 多实例必须 | `""` | 空 → InMemory fallback（WARN log，只适合单实例） |
| `RATE_LIMIT_WINDOW` | 选 | `60` | 窗口秒数 |
| `RATE_LIMIT_MAX` | 选 | `10` | marketplace 源每窗口最大请求数 |
| `RATE_LIMIT_MAX_BEARER` | 选 | `600` | bearer 源每窗口最大请求数 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 必须 | 见 `config.py` | 全局 LLM 默认值，per-tier 为空时 fallback |
| `LLM_{BASE_URL,API_KEY,MODEL}_{FREE,STARTER,PRO,BUSINESS}` | 选 | `""` | per-tier 覆盖 |
| `MAX_CONTRACT_SIZE` | 选 | `102400` | 提交合约字节上限 |

**fail-safe 默认**：`AUTH_ENABLED` 未设 = true。若同时 `API_KEY` 和 `RAPIDAPI_PROXY_SECRET` 都空，启动时 `logger.critical`，且所有 `/api/*` 返 401——忘配 secret 时显式暴雷，不会静默放行。

---

## 8. 发布前自查清单

- [ ] `AUTH_ENABLED=true` 且 `RAPIDAPI_PROXY_SECRET` / `API_KEY` 已配置，Railway logs 无 critical 告警
- [ ] `REDIS_URL` 已注入，`/health` 返回 `rate_limiter.backend == "redis"` 且 status = ok
- [ ] `/health/live` → 200
- [ ] `/api/v1/scan/sync` 裸请求（无 header）→ 401 AUTH_REQUIRED
- [ ] 带正确 `Authorization: Bearer <API_KEY>` → 200（scan 正常返回）
- [ ] 带正确 `X-RapidAPI-Proxy-Secret` + `X-RapidAPI-User: testuser` → 200
- [ ] 错的 token/secret → 401
- [ ] 连续 11 次同一 bearer 冒烟请求 → 第 11 次 429，Retry-After 头存在
- [ ] Railway logs 无 stack trace、无 "REDIS_URL not set" WARN、无打印 secret/api_key
- [ ] RapidAPI Provider Dashboard "Test Endpoint" 通（说明网关→后端 secret 对齐）

全部 ✅ 才 Submit for review。

---

## 9. 未来扩展（不在本次范围）

- **Scanner-side 跨 gateway 配额**：只有当接入不管配额的 gateway 时才开工；届时 rate-limiter 模块会被扩成 quota 台，加月度周期 key。
- **Request signing (HMAC)**：若 RapidAPI/Zyla 后续支持，替换明文 proxy secret。
- **Structured audit log**：scan_id、external_user_id、tier、latency 推到 Loki/Grafana。
- **Priority queue by tier**：pro/business 走独立 LLM 池，避免 free 流量饱和时影响付费。
- **Per-plan cold-path 缓存**：相同 source_code + compiler 版本 → 缓存命中 → 跳 LLM。
