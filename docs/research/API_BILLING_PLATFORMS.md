# API 计费平台生态调研

_调研时间：2026-04-15 / 调研人：freelancer (for Lion) / 范围：smart-contract-scan-api 多平台上架策略_

---

## TL;DR

- **RapidAPI 确实在衰退**：Nokia 于 2024-11 收购 Rapid，随后战略重心转向电信/5G 网络 API。活跃用户"从 400 万降到 thousands 级"，上架 API "降到 hundreds 级"（TechCrunch 原话）。作为基线保留但不应是唯一入口。
- **Zyla / APYHub 是跟 RapidAPI 同类的"真 marketplace"**：开发者在平台订阅、平台统一结算、抽成 20%/未披露。直接竞争者。
- **APILayer 是混合型**：自营 API 为主 + 少量第三方，入驻门槛比 Zyla 高但流量质量更精。
- **AWS / GCP / Azure Marketplace 是"企业采购渠道"**：不是 API 流量入口，而是帮助企业客户用已有云预算买我们的 SaaS；抽成 3%。不会带来散客流量，但客单价高。
- **Postman API Network 是"纯发现平台"**：不抽成、不结算，仅导流到我们自己的 landing。免费铺。
- **认证机制归为 3 种 adapter**：proxy-secret (RapidAPI/Zyla/APYHub/APILayer) + token-exchange (AWS/GCP/Azure) + self-hosted (Stripe/Postman 导流)。

---

## 一、主流平台对照表

| # | 平台 | URL | 商业模式 / 抽成 | 流量规模 | 接入难度 | 上架成本 | 认证机制 | 是否类 proxy-secret | 接入价值 (1-5) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | RapidAPI Hub (Nokia) | rapidapi.com | 平台订阅，抽约 20% | 衰退中，活跃用户 thousands 级，API hundreds 级（2024-11 起）；品牌 SEO 惯性仍强 | 低（已接入） | 免费 | `X-RapidAPI-Proxy-Secret` + `X-RapidAPI-Subscription` + `X-RapidAPI-User` | ✅ 是（基线参考） | 3 — 保留但不指望增长 |
| 2 | Zyla API Hub | zylalabs.com | 订阅转售，**80/20 分账**（SLA < 95% 可降至 0%） | 自报 10,000+ APIs、30+ 分类；实际开发者量级估算数万至数十万 | 低（向导式 UI，人工审核） | 免费 | 未公开文档；响应头 `X-Zyla-RateLimit-*` 说明走网关代理，请求转发到 provider 时有等价的 proxy-secret/header（需开工单确认） | ✅ 是（推定） | 5 — 首批必上 |
| 3 | APYHub | apyhub.com | "Atoms" 积分制 + 收入分成（比例未披露） | 中等，定位 AI/file/data APIs，AI agent 流量偏多 | 低（onboarding 团队协助） | 免费 | 网关代理 + provider 侧校验，具体 header 需联系 hello@apyhub.com 确认 | ✅ 是（推定） | 4 — 第二批 |
| 4 | APILayer (Idera) | apilayer.com | 订阅转售，抽成未公开；provider 自定价 | 小而精，以自营 75+ APIs 为主，第三方入选 curation 严 | 中（需申请 + 人工 curation） | 免费 | 网关代理；具体 proxy header 需通过 provider 合同获取 | ✅ 是（推定） | 3 — 第三批，待通过 curation |
| 5 | Postman API Network | postman.com/explore | **不抽成，不结算**，纯发现/导流 | 开发者流量极大（Postman 日活嵌入工作流），但转化靠自家 landing | 低（发布 Workspace + Collection） | 免费 | N/A — 用户在我们自己的 landing 订阅，Postman 不参与 auth | ❌ 否（导流） | 4 — 白送流量，必上 |
| 6 | AWS Marketplace (SaaS) | aws.amazon.com/marketplace | **3% 公开 listing 费**（私有合约 3.5%） | B2B 大客户采购池，不来散客；适合 $10k+ ACV | 高（需实现 `ResolveCustomer`/`GetEntitlement` API + SNS 订阅 webhook） | 免费 listing；需 AWS 账号 | Token exchange：Marketplace POST 临时 token（4h 有效）到我方 landing → 换 `CustomerIdentifier` / `LicenseArn` → 自行管理 session | ❌ 否（token-exchange） | 4 — 企业客户渠道，6 个月后做 |
| 7 | Google Cloud Marketplace | cloud.google.com/marketplace | **3% 抽成**；21 号月结 | 同 AWS，B2B 采购为主 | 高（需 Partner Advantage Build Partner 资格 + 实现 Partner Procurement API + Service Control 上报用量） | 免费 listing；需 Partner 资格 | Token exchange：Procurement API 通知 + 账户映射 | ❌ 否（token-exchange） | 2 — 流量远不如 AWS，可缓 |
| 8 | Azure Marketplace | azuremarketplace.microsoft.com | **3% agency fee** | 同 AWS；Microsoft Entra 客户生态 | 高（需多租户 + Entra ID + SaaS Fulfillment API v2） | 免费 listing | Token exchange：landing 流 + activation 流；强制 Entra ID SSO | ❌ 否（token-exchange + OIDC） | 3 — AWS 之后再做 |
| 9 | Stripe + 自建 landing | stripe.com | Stripe 收 2.9%+$0.30；**我方 100% 自主控制** | 取决于自己 SEO/营销 | 中（需自建 landing + key 管理 + metered billing 集成） | Stripe 标准费率 | 自发 API key（我们自己设计，不依赖第三方） | ❌ 否（自主） | 5 — 长期主战场，必做 |
| 10 | API.market (Magicloops) | api.market | PAYG/转售，定位 AI/MCP APIs；300+ 已上 | 中小，新兴，AI agent 流量 | 低 | 免费 | 网关代理 | ✅ 是（推定） | 3 — 补位，观察 |
| 11 | APIRobots | apirobots.pro | 自营 agency + 转售，定位 corporate intel/scraping | 小，2024-09 上线 | 中 | 未披露 | — | 部分 | 1 — 与我们品类重合度低，pass |
| 12 | Blobr | blobr.io | **API portal 工具（PaaS）**，帮你建自己的门户，不是 marketplace | 不带流量 | 低 | 有 SaaS 订阅费 | 我方自发 key | ❌ 否（SaaS 工具） | 2 — 可替代自建 landing，但不带流量 |
| 13 | DigitalAPI Marketplace | digitalapi.ai | 企业级 marketplace 搭建 + 部分公共市场 | 企业客户为主 | 中 | 有 license 费 | 网关代理 | ✅ 是 | 2 — 企业方向，暂不需要 |
| 14 | Mashape | — | **已于 2017 并入 RapidAPI** | — | — | — | — | — | 0 — 不存在了 |
| 15 | ProgrammableWeb | — | **2022 年关停** | — | — | — | — | — | 0 — 不存在了 |

> 注：eigenApi 未找到可信文档（疑为小型/未上线），本轮跳过。

---

## 二、类型归类

### A. 真 marketplace（开发者在平台订阅、平台代收 + 分账）
**RapidAPI、Zyla、APYHub、APILayer、API.market、DigitalAPI**

- 特征：平台接管 auth/billing/rate-limit，我方 API 只需校验 proxy header，转发到后端。
- 我方收益 = 开发者订阅费 × (1 − 抽成 20%~未披露)。
- 对"独立计费模块"的影响：需实现一个 marketplace-proxy adapter，校验 header 后将 tier/subscription-id 映射到我方的内部配额层。

### B. 发现/导流平台（不经手钱）
**Postman API Network、Blobr（portal 工具）**

- 特征：列出我们的 API、提供文档/Collection/try it，但点击订阅会跳到**我方自己的 landing**。
- 我方仍需自建订阅系统（Stripe）。
- 价值：零成本 SEO & 曝光；所有发现平台都应该上。

### C. 企业采购渠道（B2B / 云预算通道）
**AWS Marketplace、GCP Marketplace、Azure Marketplace**

- 特征：买家是企业、用云厂商的已有预算额度采购；客单价高、周期长；抽成 3%。
- 不带散客流量。
- 接入成本高（需实现各家的 SaaS Fulfillment API + 身份映射）。
- 当我们有第一个 $10k+ ACV 客户要求"走 AWS 采购"时再做，不需要主动投入。

### D. 自建（基线）
**Stripe + 自家 landing**

- 我方全权控制定价、分层、key 颁发、分析。
- 长期毛利最高，短期流量靠自营。

---

## 三、认证机制差异矩阵（重要 — 为独立计费模块输入）

| 平台 | 平台 → 我方 API 的认证方式 | 我方需校验的 header/字段 | tier/subscription 信息承载在哪 | 用户 ID 承载在哪 |
|---|---|---|---|---|
| RapidAPI | HTTP header（共享密钥） | `X-RapidAPI-Proxy-Secret`（校验来源） | `X-RapidAPI-Subscription`（BASIC/PRO/ULTRA/MEGA） | `X-RapidAPI-User` |
| Zyla | HTTP header（共享密钥，推定） | 需跟 Zyla 工单索取 `X-Zyla-Proxy-Secret` 或同类 | 推定为 header（需文档） | 推定为 header |
| APYHub | HTTP header（共享密钥，推定） | 需跟 hello@apyhub.com 索取 | 推定 | 推定 |
| APILayer | HTTP header（共享密钥，推定） | 需 provider 合同；平台接管 Auth | 推定 | 推定 |
| API.market | HTTP header（共享密钥，推定） | 需文档 | 推定 | 推定 |
| AWS Marketplace | **Token exchange + IAM**：POST 临时 token 到 landing → 调 `ResolveCustomer` / `GetEntitlement` API 换持久 `CustomerIdentifier` (2026-06 起用 `LicenseArn`) | 无直接 header；我方维护 customer ↔ entitlement 映射；后续 API 调用用我方自发 key | SNS 订阅事件推送变更 | `CustomerIdentifier` / `LicenseArn` |
| GCP Marketplace | **Token exchange + OAuth**：Partner Procurement API 通知；Service Control 上报用量 | 同 AWS，自发 key + 账户映射 | Procurement API | Google account ID |
| Azure Marketplace | **Token exchange + Entra ID (OIDC)**：landing flow + activation flow | JWT (Entra) + 我方自发 key | SaaS Fulfillment API | Entra tenant + subscription ID |
| Stripe / 自建 | 我方自发 `Authorization: Bearer <key>` | 自定义 | 内部订阅表 | 内部 user ID |
| Postman | N/A（不代理请求） | — | — | — |

### 三类 adapter 抽象

1. **ProxySecretAdapter**（RapidAPI / Zyla / APYHub / APILayer / API.market）
   - 校验共享密钥（env 里存 5 份 secret）
   - 从 header 解析 tier + external_user_id
   - 映射到内部 plan
2. **TokenExchangeAdapter**（AWS / GCP / Azure）
   - 接收临时 token → 调云厂商 API 换持久 customer ID
   - 订阅 SNS / Pub/Sub / Event Grid 变更事件
   - 自发内部 key，客户用我方 key 访问
3. **NativeKeyAdapter**（Stripe + 自建）
   - 我方颁发 & 校验 API key
   - 走 Stripe metered billing 上报用量
   - 直接查内部订阅表

---

## 四、Top 3 接入推荐

### 第一批（本季度）
1. **Zyla API Hub** — 唯一一个抽成与 RapidAPI 同档、上架零成本、认证机制与 RapidAPI 同构（共享 header）的直接替代品。80/20 分账明确，注意 SLA 条款（uptime < 95% 可能掉到 0%）。预计工作量：< 1 day 做 ProxySecretAdapter 参数化。
2. **Postman API Network** — 不带计费，纯导流，但 Postman 是开发者日常工具，零成本曝光。发布一个 public workspace + scanner Collection，链接到我方 landing。预计 2-4 hours。

### 第二批（下季度）
3. **APYHub** — 规模 < Zyla，但 AI/data API 定位精准，MCP / AI agent 流量正在涨。等 Zyla 数据跑 30 天后评估是否值得再上一家。

### 明确不做 / 延后
- **GCP Marketplace**：流量不如 AWS，接入成本一样高，ROI 差。
- **API.market / DigitalAPI**：观察 6 个月，数据不足以投入。
- **APIRobots / Blobr / ProgrammableWeb**：品类不匹配 / 只是 portal 工具 / 已关停。
- **AWS / Azure Marketplace**：有企业客户明确要求再接，不主动做。

---

## 五、对独立计费模块的设计输入

### 5.1 必须支持的 adapter 数量
**3 种**（proxy-secret / token-exchange / native-key），但第一阶段只需 **proxy-secret + native-key 两个**。TokenExchange 延后到有 AWS 企业客户时再加。

### 5.2 建议的抽象层

```
BillingAdapter (interface)
 ├── authenticate(req) -> AuthContext | 401
 ├── resolveTier(authCtx) -> PlanTier
 ├── reportUsage(authCtx, units) -> void           // native-key 需要；proxy-secret 平台自己算
 └── mapExternalUserToInternal(authCtx) -> UserId

Implementations:
 ├── RapidApiAdapter     : ProxySecretAdapter(secretEnv, headerPrefix="X-RapidAPI-")
 ├── ZylaAdapter         : ProxySecretAdapter(secretEnv, headerPrefix="X-Zyla-")
 ├── ApyHubAdapter       : ProxySecretAdapter(secretEnv, headerPrefix="X-ApyHub-")
 ├── ApiLayerAdapter     : ProxySecretAdapter(secretEnv, headerPrefix="X-APILayer-")
 ├── StripeNativeAdapter : NativeKeyAdapter(stripeClient, dbSubscriptions)
 └── (future) AwsMpAdapter : TokenExchangeAdapter(marketplaceClient)
```

### 5.3 关键设计点
- **ProxySecretAdapter 应参数化**：`headerPrefix`, `tierHeaderName`, `userHeaderName`, `secretEnvVar`。新增一家 marketplace 应只配置、不写代码。
- **PlanTier 内部归一化**：各平台的 tier 名字 (BASIC/PRO vs Starter/Pro vs Atoms) 应统一映射到内部 3-4 档，由 adapter 负责翻译。
- **Usage 上报只在 NativeKeyAdapter 做**：marketplace 平台自己计数，我方后端只校验 + 转发。
- **Router 选择 adapter 的策略**：按 request header 特征检测（看哪家的 proxy-secret header 存在），fallback 到 NativeKey。
- **SLA 监控回路**：Zyla 的 80/20 会因 uptime 衰减，必须暴露 uptime 指标给自己看（不只是给 Zyla 看）。

---

## 附录：数据来源 & 调研局限

### 主要来源
- TechCrunch — Nokia acquires Rapid (2024-11-13)
- Nokia newsroom — Rapid 收购公告
- zylalabs.com/monetize-your-api、freshdesk 文档（80/20 SLA 分账）
- apyhub.com/api-provider、apyhub.com/pricing/api-catalog
- marketplace.apilayer.com/docs/article/provider-faq
- learn.microsoft.com — Microsoft Marketplace SaaS Fulfillment APIs
- docs.aws.amazon.com/marketplace — SaaS contract integration、listing fees
- clazar.io、labra.io、tackle.io — 云 marketplace 运营指南（第三方但质量高）
- apify.com/blog、digitalapi.ai/blogs、apidog.com/blog — RapidAPI 替代品综述（观点可参考，数字需打折）
- stripe.com/billing — usage-based billing docs

### 调研局限（诚实披露）
1. **Zyla / APYHub / APILayer / API.market 的 provider → 后端认证 header 未能从公开文档确认**，仅从"响应 header 前缀" + "行业惯例" 推定走共享密钥。落地前必须通过工单/邮件跟各平台索取 provider 集成文档再写代码。
2. **流量规模数字多为平台自报**（如 Zyla "10,000 APIs"、RapidAPI "hundreds"），未能独立验证；实际转化率需上架后观测 30 天。
3. **eigenApi 未找到有效信息**，疑为非正式项目或名字错误。
4. **各 marketplace 的抽成**中 APYHub / APILayer 未公开具体百分比，需 NDA 后商务沟通。报告里标"未披露"而非填数字。
5. **AWS Marketplace 2026-06 API 变更**（CustomerIdentifier → LicenseArn 迁移）会影响接入时序；如果接入时间在 2026-06 后，可直接走新 API。
