# API Billing Module — 独立计费接入模块设计

_作者：freelancer (for Lion)  |  日期：2026-04-15  |  状态：Draft v1_
_前置输入：`docs/research/API_BILLING_PLATFORMS.md`（15 家平台生态调研）_

---

## 为什么要做这个模块

scanner 现在把 `X-RapidAPI-Proxy-Secret` 校验、`X-RapidAPI-Subscription` → tier 映射、tier → LLM 路由这一整套逻辑写死在 `app/auth.py` + `app/config.py` + `app/routers/scan.py`。

调研结论是我们下一步要同时上 **Zyla + Postman + 后续 APYHub + Stripe 自建**，每多接一家，如果都要往 scanner 代码里加 if/else，就是在不断叠屎山。

**抽成独立模块后**：
- 新增 marketplace = 加一个 adapter 配置（不改 scanner 业务代码）
- 下一个产品（Contract Explainer、Honeypot Risk 等 Copy 机会）可以零成本直接复用整套计费层
- 测试边界清晰（模块有自己的测试矩阵，scanner 只测业务）

---

## 一、模块名称与形态

**名称建议**：`api-billing-gateway`（简称 `abg`）

候选：
- ✅ `api-billing-gateway` — 描述准确，包含"网关"定位（介入 request/response 流）
- ⏳ `@freelancer/api-gateway` — 太泛，kong/express-gateway 这些大名字挤占
- ⏳ `api-billing-middleware` — 限定在 middleware 形态，未来要出 CLI / SDK 时会别扭

**对外形态**：**Python package（可 pip install 的独立 repo）**，主要导出一个 FastAPI middleware + 一套 Adapter 类。

为什么不是 SDK / HTTP 代理 / sidecar：
- SDK → 调用者要到处手动调用 `billing.check()`，容易漏
- HTTP 代理（如 Kong 插件） → 对我们这种 2 人团队 infra 太重
- Sidecar（Envoy filter） → 同上，overkill
- **FastAPI middleware** → 最小侵入，scanner 只加 `app.add_middleware(BillingMiddleware, config=...)` 一行

未来如果其他语言的服务要用（TS / Go），再做一个 HTTP 模式的参考实现（内部服务调 `POST /billing/auth` 换 AuthContext），但 v1 不做。

---

## 二、核心抽象

### 2.1 数据结构

```python
@dataclass(frozen=True)
class AuthContext:
    """单次请求的认证/计费上下文。中间件写入 request.state.billing。"""
    source: str            # "rapidapi" / "zyla" / "stripe" / "disabled-test"
    external_user_id: str  # 平台侧的调用者 ID（RapidAPI user、Stripe customer_id...）
    tier: PlanTier         # 内部归一化后的档位
    raw_tier: str          # 平台原始字符串（BASIC / PRO / Atom1 / ...），日志用
    mode: str              # "authed" / "disabled-test"

class PlanTier(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    BUSINESS = "business"
```

`PlanTier` 4 档够用。每个平台 adapter 负责把自家 tier 名翻译成这 4 档。

### 2.2 BillingAdapter 接口

```python
class BillingAdapter(Protocol):
    source: str  # e.g. "rapidapi"

    def matches(self, req: Request) -> bool:
        """该 adapter 是否应处理此请求（看 header 特征）。"""

    def authenticate(self, req: Request) -> AuthContext | None:
        """校验；通过返回 AuthContext，失败返回 None。"""

    def report_usage(self, ctx: AuthContext, units: int = 1) -> None:
        """上报用量。proxy-secret 平台 no-op（平台自己算），native-key 需实现。"""
```

### 2.3 三类 adapter 实现

**ProxySecretAdapter**（参数化 — 这是复用的关键）：
```python
class ProxySecretAdapter:
    source: str
    secret_env: str              # "RAPIDAPI_PROXY_SECRET"
    secret_header: str           # "X-RapidAPI-Proxy-Secret"
    tier_header: str             # "X-RapidAPI-Subscription"
    user_header: str             # "X-RapidAPI-User"
    tier_map: dict[str, PlanTier] # {"BASIC": FREE, "PRO": STARTER, ...}
```

用它实例化 RapidAPI / Zyla / APYHub / APILayer 等 — **零代码新增一家**。

**NativeKeyAdapter**：Stripe 自建渠道，校验 `Authorization: Bearer <our_key>`，查订阅表取 tier，response hook 上 Stripe meter。

**TokenExchangeAdapter**：骨架先留着，AWS/GCP/Azure 走这条 — 第一阶段不实现（等企业客户要求再补）。

### 2.4 Middleware

```python
class BillingMiddleware:
    def __init__(self, adapters: list[BillingAdapter], exempt: list[str]):
        self.adapters = adapters
        self.exempt = exempt  # ["/health", "/docs", ...]

    async def __call__(self, request, call_next):
        if request.url.path in self.exempt: return await call_next(request)
        if settings.auth_enabled is False: return await self._disabled_passthrough(request, call_next)

        for a in self.adapters:
            if a.matches(request):
                ctx = a.authenticate(request)
                if ctx:
                    request.state.billing = ctx
                    resp = await call_next(request)
                    a.report_usage(ctx)
                    return resp
                return _401()  # 匹配但校验失败 → 直接 401，不 fallback
        return _401()  # 没 adapter 匹配
```

关键决策：**adapter 的选中由 `matches()` 决定（看 header 是否存在），不是 fallback try-each**。避免一个通道失败后"漏"到另一个通道的模糊行为。

---

## 三、tier → 能力（feature/model）映射

当前 scanner 里有 `Settings.resolve_llm(tier) -> (base_url, api_key, model)`。这块应该**留在 scanner 业务层**，不进模块。

模块只负责：**"是谁、什么档"**（identity + tier）。
业务决定：**"这个档能用什么 model / 多大 quota / 什么 feature"**。

为什么这么切：不同产品对 tier 的使用方式完全不一样 — scanner 靠 tier 路由 LLM；Contract Explainer 可能靠 tier 限制请求频率；Honeypot Risk 可能靠 tier 开关"深度分析"开关。**计费模块提供原子事实，业务层组装策略**。

---

## 四、从现有 scanner 代码抽取

| scanner 现有代码 | 提取去向 | 备注 |
|---|---|---|
| `app/auth.py::RAPIDAPI_TIER_MAP` | 模块 / `RapidApiAdapter.tier_map` | 直接迁 |
| `app/auth.py::resolve_tier()` | 模块 / `ProxySecretAdapter._resolve_tier()` | 重构为可参数化 |
| `app/auth.py::auth_middleware` | 模块 / `BillingMiddleware` | 重写成 class-based，支持多 adapter |
| `app/auth.py::AUTH_EXEMPT_PATHS` | 模块 / 构造函数参数 | 调用方传入 |
| `app/config.py::api_key / rapidapi_proxy_secret` | scanner 继续持有 env var，但作为模块初始化参数传入 | 模块不读 env |
| `app/config.py::resolve_llm / LLM_MODEL_<TIER>` | **留在 scanner** | 业务策略，不进模块 |

**关键原则**：模块不读 env，所有配置由调用方注入。这样 scanner 的 `.env` 继续由 `pydantic-settings` 管，模块保持 infra-agnostic、好测试。

---

## 五、仓库布局

**选项 A（推荐）**：monorepo workspace + 独立可发布 package
```
freelancer-services/                 <- 新建根 monorepo（或复用现有）
├── packages/
│   └── api-billing-gateway/         <- 独立 Python package
│       ├── src/abg/
│       │   ├── __init__.py
│       │   ├── middleware.py
│       │   ├── context.py
│       │   ├── adapters/
│       │   │   ├── proxy_secret.py
│       │   │   ├── native_key.py
│       │   │   └── token_exchange.py (stub)
│       │   └── tier.py
│       ├── tests/
│       └── pyproject.toml
└── services/
    └── smart-contract-scan-api/     <- 现有 repo 迁入
        └── pyproject.toml  (depends on ../../packages/api-billing-gateway)
```

**选项 B**：独立 repo + 发 PyPI
- 门槛更高（要维护版本 + CHANGELOG）
- 2 人团队阶段不必要

**选择 A。** 用 `uv workspace` 或 `pip install -e ../../packages/api-billing-gateway` 本地链接。

---

## 六、发布 / 复用路径

**阶段 1（scanner 自用）**：
- 抽模块到 `packages/api-billing-gateway/`，scanner 改用新模块
- 所有现有测试（9 auth + 20 routing = 29）继续全绿
- 不发 PyPI，local editable install

**阶段 2（第二个产品复用）**：
- 下一个产品（假设是 Contract Explainer）直接 `add_middleware(BillingMiddleware, ...)`
- 如果第二个产品跑通 → 证明抽象合格

**阶段 3（外部可见）**：
- 考虑发 PyPI 或开源
- 条件：3+ 个内部服务在用，且 API 稳定 60 天无 breaking change
- 这个阶段要补：CHANGELOG、semver、文档站

---

## 七、实施计划（工时估算）

| 步骤 | 工时 | 输出 |
|---|---|---|
| 1. 建 monorepo 骨架 + `abg` package skeleton | 2h | 目录、pyproject、CI |
| 2. 抽 `BillingMiddleware` + `AuthContext` + `PlanTier` | 2h | core |
| 3. 实现 `ProxySecretAdapter`（参数化） | 3h | RapidAPI 跑通 |
| 4. scanner 迁移到新模块，29 测试全绿 | 3h | 无回归 |
| 5. 实现 `NativeKeyAdapter` + Stripe meter | 4h | 自建渠道打通 |
| 6. 加 Zyla adapter 配置 + 测试 | 1h | 新增平台 |
| 7. 模块自身测试矩阵（adapter 单测 + middleware 集成） | 3h | ≥ 20 cases |
| **合计** | **~18h / 2-3 工作日** | v0.1 可用 |

不在 v0.1：TokenExchangeAdapter、quota 限流、billing event bus、admin dashboard。

---

## 八、开放问题（需 Lion 拍板）

1. **monorepo 根用现有 `freelancer` 目录还是新开？** 推荐：新建 `freelancer-services/` 作为 monorepo 根，现有 scanner repo 作为 `services/smart-contract-scan-api/` 迁入。
2. **是否 Stripe 先行？** 如果 Zyla 第一优先（调研推荐），NativeKeyAdapter 可以后做；v0.1 只出 ProxySecretAdapter + scanner 迁移，工时砍半到 ~10h。
3. **TokenExchangeAdapter 要不要在 v0.1 留空骨架？** 我倾向放一个 `raise NotImplementedError` 占位 + 文档，结构上表态但不投入工时。
4. **命名最终确认**：`api-billing-gateway` vs. 其他。

---

## 附：调研未确认项（落地前必须解决）

来自 `API_BILLING_PLATFORMS.md` 局限章节：
- Zyla / APYHub / APILayer 的 **provider 侧 proxy-secret header 名** 未能从公开文档确认 —— 上架前必须工单索取集成文档
- Zyla 的 **tier 名称约定**（是否也是 BASIC/PRO/ULTRA/MEGA）未确认
- 没拿到就先写 `ZylaAdapter = ProxySecretAdapter(secret_header="<TBD>", ...)`，上线前替换

**这也是参数化的好处**：header 名变了只改配置，不改代码。
