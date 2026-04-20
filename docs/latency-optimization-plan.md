# Latency Optimization Plan — SmartScan Scanner

**目标**：scan latency **32–34 s → &lt; 15 s**（buyer-facing 体验可接受门槛）
**瓶颈定位**：LLM 推理占整体 >70%（Slither 4–6 s / 反代 1 s / LLM 20–28 s）
**当前 LLM 栈**：OpenAI-compatible SDK → Fireworks DeepSeek V3.1（free/starter 档），Claude Sonnet 4.6（pro/business 档）

> 本文档是**方案对比 + 推荐**，不是实施计划。选定方案后另开 PR + ADR。

---

## 现状基线（2026-04-20）

| 阶段 | 典型耗时 | 备注 |
|---|---|---|
| Slither 静态分析 | 4–6 s | `app/services/slither_service.py`；120 s hard timeout |
| Findings 压缩 / payload 组装 | &lt;100 ms | 上限 30 findings、每条 ≤400 字 |
| **LLM 推理（非 stream）** | **20–28 s** | `app/services/llm_service.py:analyze_with_llm` |
| JSON 解析 + schema 校验 | &lt;100 ms | `response_format={"type":"json_object"}` 优先，失败 fallback |
| 反代 + 网络 | 1–2 s | RapidAPI → Railway |
| **Total p50** | **32–34 s** | 逼近 RapidAPI 30 s timeout 墙 |

### LLM 调用参数（当前）

```
model         = tier-resolved (DeepSeek V3.1 / Claude Sonnet 4.6)
temperature   = 0.1
max_tokens    = 4096            ← ⚠️ 偏大
stream        = false           ← ⚠️ 首 token 用不上
timeout       = SDK default     ← ⚠️ 约 30 s，和 RapidAPI 墙同步
system prompt = ~1500 chars     ← 可精简
user prompt   = 源码 + findings JSON（3–5 KB） ≈ 3–5 K input token
```

### 已就绪的基础设施

- **Multi-model routing**：`app/config.py` 已有 `LLM_MODEL_{FREE,STARTER,PRO,BUSINESS}` + `LLM_BASE_URL_*` + `LLM_API_KEY_*` 覆盖，`Settings.resolve_llm(tier)` 秒级切换
- **测试 mock**：`tests/conftest.py` 全局 patch `run_slither` / `analyze_with_llm`，安全做 POC
- **tier-aware**：`_run_scan(... tier)` 已贯穿到 LLM 调用，做分级模型无侵入

---

## 方案 A — 换快 model（单档全换）

### 思路

全档位替换 LLM 为 latency-first 模型，牺牲部分推理深度换速度。

### 候选对比（按 public 公开 benchmark + Fireworks / Groq / OpenRouter 文档估）

| 候选 | 推理速度 (TPS) | 4K-token 响应估 latency | Quality（code audit）| 月成本 / 1K scan |
|---|---|---|---|---|
| **DeepSeek V3.1 (baseline)** | ~60 TPS | 25–30 s | ★★★★☆ | ~$0.80 |
| **Llama 3.1 70B Fast (Fireworks)** | ~200 TPS | 8–12 s | ★★★☆☆ | ~$0.60 |
| **Llama 3.3 70B (Groq)** | ~500 TPS | 3–5 s | ★★★☆☆ | ~$0.75 |
| **Gemini 2.0 Flash** | ~250 TPS | 6–10 s | ★★★★☆ | ~$0.15 |
| **Claude Haiku 4.5** | ~150 TPS | 10–14 s | ★★★★☆ | ~$1.00 |
| **gpt-4o-mini** | ~120 TPS | 10–15 s | ★★★☆☆ | ~$0.30 |

### Trade-off

- ✅ 实现极简：只改 env `LLM_MODEL_*` + `LLM_BASE_URL_*` + `LLM_API_KEY_*`，零代码改动
- ✅ Gemini Flash / Groq Llama 3.3：latency 能拉到 **5–12 s**，达标
- ⚠️ 质量风险：code audit 场景下 Llama 3.1 70B 对复杂 reentrancy / cross-function 漏洞判断稍弱——需跑 **quality harness**（见方案 C 复用基线）验证 findings recall
- ⚠️ 厂商分散：新增 Groq / Google 账号 = 新 API key、新 billing、新 SLA 风险
- ⚠️ **输出质量下降不可逆**：一旦 buyer 习惯了 DeepSeek 精度，降档差评难收回

### 工作量：**S**（2–4 h，纯配置 + 质量测试）

---

## 方案 B — 分级 model（Free 档降档 + Paid 档保留）

### 思路

利用**已就绪的 tier-based routing**：

- **Free / Starter** 档 → 换 Gemini Flash 或 Llama 3.3 Groq（latency 优先，"试用档本就允许精度差一点"）
- **Pro / Business** 档 → 保留 DeepSeek / Claude Sonnet（付费用户花钱买 quality）

### Trade-off

- ✅ **零 quality 丧失**：付费用户一字不改
- ✅ **buyer 感知分级天然**：Free 档 latency 8–10 s + "upgrade to Pro for deep reasoning" 定位一致
- ✅ **已有框架直接用**：`config.py` `resolve_llm(tier)` 已支持，只改 env
- ⚠️ Free 档 quality 下滑风险——需在 RapidAPI listing 文案里**明示**"Free tier uses our fast lightweight model; Pro/Business use advanced reasoning"（实际 `rapidapi-listing.md` 已这么写 ✅）
- ⚠️ Pro/Business 的 30 s latency **未解决**——需叠加方案 C/D

### 工作量：**S**（2–3 h，只改 env + 更新文档）

---

## 方案 C — Timeout cap + 优雅降级

### 思路

LLM 硬卡 **12–15 s timeout**，超时返 **Slither findings + LLM partial**（如有）+ 降级 summary。

### 实现

```python
# llm_service.py
try:
    async with asyncio.timeout(LLM_SOFT_TIMEOUT):  # default 12s
        response = await client.chat.completions.create(...)
except (asyncio.TimeoutError, httpx.TimeoutException):
    return _slither_only_fallback(slither_result, scan_id, scan_time_ms)
```

**Fallback 策略**：

```python
def _slither_only_fallback(slither_result, ...) -> ScanResponse:
    return ScanResponse(
        scan_id=...,
        status="completed",
        vulnerabilities=[_slither_to_finding(f) for f in slither_result.findings],
        risk_score=_heuristic_score(slither_result),   # count × severity-weight
        summary="LLM triage temporarily unavailable — returning Slither-only findings. Results may contain noise.",
        gas_optimizations=[],
        scan_time_ms=...,
        degraded=True,       # 新增字段，文档里标记
    )
```

### Trade-off

- ✅ **latency 下限可控**：最坏也是 12–15 s
- ✅ buyer 永远拿到**某种**有效响应（不是 500）
- ⚠️ `degraded=True` 响应会**让一部分 buyer 困惑**——需文档说明 & 可选的重试逻辑
- ⚠️ heuristic risk_score 精度比 LLM 低——可能和 Paid 档 LLM 版 score 偏差 ±15–30
- ⚠️ **治标不治本**：只是保底，`healthy` 场景仍然是 25–30 s

### 工作量：**M**（4–6 h，含 fallback 逻辑 + 测试 + schema 扩字段）

---

## 方案 D — Prompt / input 优化 + Streaming

### 思路

降低 LLM 实际工作量 + 缩短 user-perceived latency。

### 4 个子动作

1. **System prompt 精简**：~1500 chars → ~600 chars（砍掉冗余 task 枚举 + 示例）
2. **max_tokens 下调**：4096 → 2048（典型 reentrancy 响应 &lt;1500 token，4096 是无谓预留）
3. **Findings 二次压缩**：30 → 15 条，description ≤200 字（目前 400）。按 severity 排序取 top-K
4. **Streaming**：`stream=True` + SSE 转发（如对 sync 直接流回 client）或内部流式 parse（拿到 `vulnerabilities` array 首对象即早返回 "status=partial"）

### Trade-off

- ✅ **子动作 1+2+3 零质量风险**（信息冗余砍掉而非降档模型）
- ✅ Streaming 不降 wall-clock latency，但首 token **TTFB 缩到 1–3 s**，buyer 感知巨大改善
- ⚠️ Streaming 和 RapidAPI 反代兼容性需验证（SSE over RapidAPI proxy 是否 passthrough）
- ⚠️ JSON response_format + streaming 能否共存需测（有些 provider 不兼容）

### 预期收益

- 子动作 1+2+3：LLM 推理 **-20 ~ -30%**（25 s → 17–20 s）
- 子动作 4（streaming）：wall-clock 不变，但 **sync buyer 感知 latency 降到 3–5 s**

### 工作量：**M**（6–8 h，prompt 重写 + streaming 改造 + quality harness 验证）

---

## 方案对比表

| 维度 | A. 换快 model | B. 分级 model | C. Timeout + 降级 | D. Prompt / Stream |
|---|---|---|---|---|
| **Latency 预期** | 6–12 s (Free) / ~15 s (Paid) | Free 8–12 s, Paid 25–30 s | 最坏 12–15 s, 正常 25–30 s | 17–20 s, 感知 3–5 s |
| **Quality trade-off** | 全档 -5%~-15% | Free -10%, Paid 0 | Paid 不变，fallback 场景 -30% | 几乎 0（除非 prompt 砍过头）|
| **实现难度** | S (2–4 h) | S (2–3 h) | M (4–6 h) | M (6–8 h) |
| **成本变化** | 可能 -20% ~ +25% | Free 降本 30%，Paid 不变 | 无（只是兜底） | 省 max_tokens 费用 ~10% |
| **Buyer 感知** | 全档变快 | Free 变快，Paid 不变 | 偶发 degraded 标记 | 感知巨快（TTFB） |
| **可组合性** | ⭕ 和 B/D 可叠 | ⭕ 和 C/D 可叠 | ⭕ 叠任何 | ⭕ 叠任何 |
| **可回滚性** | env 改回即恢复 | env 改回即恢复 | code rollback | code rollback |

---

## 推荐方案：**B + D 组合（分级 model + prompt/stream 优化）**

### 推荐理由

1. **达标概率最高**
   - Free/Starter 档：Gemini 2.0 Flash（方案 B）→ 8–12 s；叠加 prompt 精简（方案 D 子 1-3）→ 6–10 s ✅
   - Pro/Business 档：DeepSeek 保留 + prompt 精简（方案 D 子 1-3）→ 17–20 s；叠加 streaming（方案 D 子 4）→ 感知 3–5 s ✅

2. **质量风险可控**
   - 方案 A 的 "全档降档" 不可逆，会丢失已建立的 "advanced reasoning" 定位
   - 方案 B 天然匹配 tier 差异化定价（Free 快但一般，Pro 慢但精），和 RapidAPI listing 文案对齐

3. **成本结构改善**
   - Gemini Flash 比 DeepSeek V3.1 还便宜（Free 档月成本 ↓30%）
   - max_tokens 下调 + prompt 精简：所有档 token 费 ↓10–15%

4. **回滚粒度细**
   - 每一步都是 `env + prompt + code` 三层独立变量，出问题单独 rollback 不影响其他

### 为什么不选方案 C？

Timeout cap + 降级只是**安全网**，不是优化。买了 Pro/Business 的 buyer **不应该**偶发拿到 "LLM temporarily unavailable" 响应——那是 prod 稳定性问题不是 latency 优化。C **建议作为 B+D 的保底**而非主方案：LLM 超 25 s 仍超时兜底，但正常路径由 B+D 提供 <15 s。

### 推荐实施顺序（阶段性交付，每阶段独立验证）

| 阶段 | 方案 | 预期 latency | 验证点 |
|---|---|---|---|
| **Phase 1** | D 子 1-3（prompt / max_tokens / findings 压缩） | 25 s → 17–20 s | quality harness 对比 10 个标准合约 findings recall |
| **Phase 2** | B（Free 档换 Gemini Flash）| Free 17 s → 8–12 s | 同 harness，比较 DeepSeek 和 Flash 在 Free 档输出一致率 |
| **Phase 3** | D 子 4（Streaming，仅 sync 路径）| TTFB 3–5 s | Playwright 录屏验证 RapidAPI Playground 感知 |
| **Phase 4**（可选）| C（12–15 s hard cap + degraded flag）| 兜底 &lt;15 s | chaos test：人为 slow LLM，验降级路径 |

每阶段独立 PR，Lion 分别验证，**完全符合 main-protected / develop-review 流程**。

---

## POC 实现框架（指引，不写全代码）

### Phase 1：Prompt / max_tokens / findings 优化

**File：`app/services/llm_service.py`**

```python
# L21-111 build_prompt(): 砍 ~60% system prompt 内容
# - 删除 "You are a smart contract security expert..." 冗长自我介绍
# - 删除 examples block（现在 prompt 里 inline 的 1–2 个示例）
# - 保留：task、severity 枚举、output schema、2 个关键 constraint

# L133 analyze_with_llm(): max_tokens=4096 → 2048
# L37-45 _compact_slither_findings(): 30 → 15, description ≤200 chars
```

**File：`tests/test_llm_service.py`**（新增）

```python
# Quality harness: 10 个已知漏洞合约 (fixtures/contracts/*.sol)
# 断言新版 prompt 对每个合约仍能检出其标签的漏洞 type
# 用 TDD 先写 harness，再改 prompt
```

### Phase 2：Free 档换 Gemini Flash

**File：`Railway env`**（零代码）

```
LLM_BASE_URL_FREE=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_API_KEY_FREE=<gemini key>
LLM_MODEL_FREE=gemini-2.0-flash-exp
# Starter 同上（或保留现状，按 Lion 决策）
```

**File：`app/services/llm_service.py`** — 确认 OpenAI SDK 兼容 Gemini compat endpoint（有小坑：`response_format` 支持不全，fallback 路径已就绪）

**File：`tests/test_llm_service.py`** — 扩 harness 跑 Gemini provider，比对输出结构一致性

### Phase 3：Streaming（sync 路径）

**File：`app/routers/scan.py`**

```python
# L155-171 _run_scan 增加 stream kwarg
@router.post("/scan/sync")
async def scan_sync(req: ScanRequest, stream: bool = Query(False)):
    if stream:
        return StreamingResponse(_run_scan_streaming(...), media_type="text/event-stream")
    return await _run_scan(...)
```

**File：`app/services/llm_service.py`**

```python
# 新增 analyze_with_llm_stream() — 基于 client.chat.completions.create(stream=True)
# 流式 yield chunks，外层 router 组装 SSE 帧
# 注意：JSON streaming parse 选 ijson 或 partial-json-parser
```

**File：`app/services/rapidapi_sse_passthrough.md`**（新增研究笔记）

```
- 测 RapidAPI 反代对 SSE 是否 passthrough（curl + Fiddler）
- 如不支持，sync streaming 仅直连 Railway 生效，sync RapidAPI 路径保持非 stream
```

### Phase 4（可选）：Timeout + degraded fallback

**File：`app/models/schemas.py`**

```python
# ScanResponse 增加 degraded: bool = False 字段
```

**File：`app/services/llm_service.py`**

```python
# analyze_with_llm 外层 wrap asyncio.timeout(LLM_HARD_TIMEOUT)
# except → _slither_only_fallback()
# 新增 _slither_only_fallback() + _heuristic_score()
```

**File：`app/config.py`**

```python
# 新增 LLM_HARD_TIMEOUT = int(os.getenv("LLM_HARD_TIMEOUT", "25"))
```

---

## Quality Harness（所有 phase 共用，先建）

**File：`tests/quality/fixtures/`** — 10 个带已知漏洞标签的 .sol 合约（reentrancy, integer overflow, access control, tx.origin, unchecked return, delegatecall, selfdestruct, timestamp manipulation, floating pragma, gas optimizations）

**File：`tests/quality/test_recall.py`** —

```python
@pytest.mark.parametrize("fixture", load_fixtures())
def test_vuln_recall(fixture, llm_model):
    result = scan(fixture.source_code, tier="business")
    assert fixture.expected_vuln_type in {v.title.lower() for v in result.vulnerabilities}
```

跑 `pytest tests/quality/ --llm-model=deepseek-v3p1`（baseline）/`--llm-model=gemini-2.0-flash`（候选）对比 recall。

---

## Open Questions（需 Lion 决策）

1. **Gemini Flash vs Groq Llama 3.3** 作为 Free 档首选？
   - Gemini Flash：质量更稳，Google infra 更可靠，成本 ↓
   - Groq：速度更快（3–5 s），但 rate limit 严苛 + 单点风险
   - 建议：先 Gemini Flash，Groq 作为 Phase 5+ 冲刺 &lt;5 s 时再考虑
2. **max_tokens 2048 是否够**？需跑 baseline 统计 p99 响应长度（预计 &lt;1200 token）
3. **Streaming 在 RapidAPI 侧是否 passthrough**？需 Phase 3 前先做 1 h 网络测，不行则 streaming 只生效于直连
4. **degraded 字段是否暴露给 buyer**？影响 schema，需同步更新 RapidAPI Documentation block

---

## Next Step

此文档 merge 到 develop 后，Lion 决策推荐方案 → 开 ADR `docs/adr/latency-optimization-strategy.md` → 按 Phase 1→4 各开独立 PR。
