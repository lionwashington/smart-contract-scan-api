# Fireworks Model Selection — SmartScan 4-Tier Matrix

> 延迟优化 Phase 2 交付物。Phase 1 (`latency-optimization-plan.md`) 定了 Strategy B（按 tier 路由模型）为主方向；本文定**具体选型**：每档 primary + fallback、env 配置、成本核算、smoke test 验收。
>
> 约束（2026-04-17 决策）：**Fireworks serverless 独家 provider**，不混用 Anthropic / Gemini / 自建；成本、延迟、运维三视角同时收口。

---

## TL;DR

| Tier | Price | Quota | Primary | Fallback | 典型延迟 (LLM 阶段) |
|---|---|---|---|---|---|
| **BASIC** (Free) | $0 | 1 / mo | Qwen3 8B | DeepSeek V3.1 | ~5-8s |
| **STARTER** | $48.9 | 100 / mo | DeepSeek V3.1 | Llama 3.3 70B | ~12-18s |
| **PRO** | $134.9 | 300 / mo | DeepSeek V3.2 | Llama 3.3 70B | ~14-20s |
| **BUSINESS** | $399 | 1,000 / mo | Llama 3.3 70B | DeepSeek V3.2 | ~10-14s |

**Rationale 一句话**：低档走小/便宜 (Qwen3 8B → V3.1) 换延迟，中高档走 DeepSeek 推理家族 (V3.1 → V3.2) 换质量，顶档用 Llama 3.3 70B 的品牌可信度 + 稳 TTFT 作为 BUSINESS 默认脸，同时互为备胎。

---

## Fireworks Serverless 候选池

**Serverless** = 按 token 计费、无 GPU 月费、冷启动 <1s；**on-demand** = 专属 GPU、按小时计费，单点 scan 成本不划算 — 本文**只用 serverless**。

经 Fireworks 官方 catalog (docs.fireworks.ai/models/serverless) 核验，当前符合条件的候选仅 4 个：

| Model ID | In ($/1M) | Out ($/1M) | Cached In | Context | Notes |
|---|---|---|---|---|---|
| `accounts/fireworks/models/qwen3-8b` | 0.20 | 0.20 | — | 41K | 最便宜 / 最小 / 最快 |
| `accounts/fireworks/models/deepseek-v3p1` | 0.56 | 1.68 | 0.28 | 163.8K | 当前生产基线 |
| `accounts/fireworks/models/deepseek-v3p2` | 0.56 | 1.68 | 0.28 | 163.8K | V3.1 继任，推理/agent 更强 |
| `accounts/fireworks/models/llama-v3p3-70b-instruct` | 0.90 | 0.90 | 0.45 | 131K | TTFT 0.70s，~141.8 t/s (Artificial Analysis) |

**被排除的模型**（on-demand only，无 serverless）：Llama 3.1 8B/70B/405B、Qwen 2.5 7B/32B/72B、Qwen3 235B A22B、Kimi K2、DeepSeek R1（Basic + Fast 两个变体）。要用这些必须包 GPU，不适合 per-scan 定价。

---

## 分档选型详解

### BASIC (Free) — Qwen3 8B

**Primary**: `accounts/fireworks/models/qwen3-8b`
**Fallback**: `accounts/fireworks/models/deepseek-v3p1`

**Why**:
- Free 档 quota 1 req/mo，用户只拿来**试水**。质量满足"能看出有 finding"即可，不要求深推理。
- Qwen3 8B 是池里最快/最便宜，降低 LLM 阶段到 ~5-8s，Free 档感受更好 → 转化。
- Fallback V3.1 保证 Qwen3 出问题也不会直接掉到 Llama（Llama 单价翻 4 倍，Free 不值）。

**风险**：Qwen3 8B 英文能力和 reasoning 深度弱于 DeepSeek 家族，可能漏掉微妙漏洞。Free 档能接受 — 用户要精度会 upgrade。

### STARTER ($48.9) — DeepSeek V3.1

**Primary**: `accounts/fireworks/models/deepseek-v3p1`
**Fallback**: `accounts/fireworks/models/llama-v3p3-70b-instruct`

**Why**:
- V3.1 是当前生产基线，质量已被 smoke test 验证过（reentrancy 能稳定检出 critical/high）。
- 价格 $0.56/$1.68 — STARTER 100/mo 毛利仍 > 50%。
- Fallback 选 Llama 3.3 70B 而非 V3.2：V3.1 / V3.2 同家族，如果是 provider 侧问题很可能一起挂，Llama 跨家族更安全。

### PRO ($134.9) — DeepSeek V3.2

**Primary**: `accounts/fireworks/models/deepseek-v3p2`
**Fallback**: `accounts/fireworks/models/llama-v3p3-70b-instruct`

**Why**:
- V3.2 相对 V3.1 强化 reasoning + agent use，对"根因分析 + fix 建议"这种 triage 任务更合适。
- 同价 ($0.56/$1.68) — PRO 用户多付钱买**更好的脑子**而非更多 token，契合 listing 文案 "higher tiers use stronger models, not just more quota"。
- Fallback 同 STARTER 逻辑跨家族选 Llama。

### BUSINESS ($399) — Llama 3.3 70B

**Primary**: `accounts/fireworks/models/llama-v3p3-70b-instruct`
**Fallback**: `accounts/fireworks/models/deepseek-v3p2`

**Why**:
- BUSINESS 用户付高价，品牌信任度是 decision factor。Llama 70B 是**可在合同/PR 文档里写出来**的名字（Meta 背书），DeepSeek 在保守企业客户眼里还是"听说过"级别。
- Llama 3.3 70B TTFT 0.70s + ~141.8 t/s 的 Artificial Analysis 数据 → **实际 end-to-end 比 70B 直觉要快**（约 10-14s，比 V3.2 还略好），BUSINESS 不牺牲延迟。
- 成本：$0.90 flat 比 V3.2 输出 $1.68 更可预测（BUSINESS 1000/mo 的 budget 预留更准）。
- Fallback V3.2 保留 DeepSeek 的强推理作 backup，跨家族。

---

## 成本核算（每 scan）

**典型 token 量**（依现行 `build_prompt` + `_compact_slither_findings` 实测经验）：
- Input: ~2,500-4,000 tokens（system prompt 1500 chars ≈ 400 tok + 合约源码 500-2000 tok + Slither findings 压缩后 500-1500 tok）
- Output: ~1,200-2,200 tokens (见 Q2 节)

**按 3,200 in / 1,800 out 中位数估算每 scan 成本**：

| Tier | Model | In cost | Out cost | Total/scan | Quota cost (全量用完) | vs 定价 | 毛利 |
|---|---|---|---|---|---|---|---|
| BASIC | Qwen3 8B | $0.00064 | $0.00036 | **$0.001** | $0.001 | $0 | Loss-leader (预期) |
| STARTER | V3.1 | $0.00179 | $0.00302 | **$0.00481** | $0.481 | $48.9 | **99.0%** |
| PRO | V3.2 | $0.00179 | $0.00302 | **$0.00481** | $1.443 | $134.9 | **98.9%** |
| BUSINESS | Llama 70B | $0.00288 | $0.00162 | **$0.00450** | $4.500 | $399 | **98.9%** |

**观察**：
- 全量都超 98% 毛利 — 成本模型极其健康，模型升级空间大。
- Free 档单次 $0.001 在可忽略区间，1 req/mo × 几千 free users 也是百刀级月度成本，可当 acquisition cost 吃掉。
- 如果未来要进一步压成本，第一候选是**开 cached input** ($0.28 / 1M)，system prompt 固定 → 重复调用命中 cache 直接砍 50% input cost。

---

## Env 配置（Railway）

`Settings.resolve_llm(tier)` (app/config.py:53-60) 已完整支持 per-tier。**不需要改代码**，只要在 Railway → Variables 设以下环境变量：

```bash
# 全局 fallback（任何 per-tier 变量缺失时兜底）
LLM_BASE_URL=https://api.fireworks.ai/inference/v1
LLM_API_KEY=<fireworks-api-key>
LLM_MODEL=accounts/fireworks/models/deepseek-v3p1

# Per-tier primary
LLM_MODEL_FREE=accounts/fireworks/models/qwen3-8b
LLM_MODEL_STARTER=accounts/fireworks/models/deepseek-v3p1
LLM_MODEL_PRO=accounts/fireworks/models/deepseek-v3p2
LLM_MODEL_BUSINESS=accounts/fireworks/models/llama-v3p3-70b-instruct

# Per-tier base_url / api_key 留空 → 继承全局（所有 tier 都用同一个 Fireworks key）
```

Fallback 暂不通过 env 独立配置；计划以**代码层 try/except retry with fallback model** 实现（见下节 Rollout P2）。

---

## Q2 答案：max_tokens 2048 够吗？1536 呢？

**当前**：`app/services/llm_service.py:151` / `:161` — `max_tokens=4096`。

**Response schema（ScanResponse）** 的典型分布：
- `summary`: 200-400 tokens（~100-200 英文词）
- `vulnerabilities[]`: 每条 ~150-250 tokens（title + description + location + recommendation），典型合约 3-7 条 findings → 450-1750 tokens
- `gas_optimizations[]`: 2-5 条 × ~60 tokens = 120-300 tokens
- `risk_score` + JSON 结构 overhead: ~100 tokens

**估算 p50 ~1,200 / p95 ~1,800 / p99 ~2,200 tokens**。

**建议**：
- **2048 基本够**，但会偶尔被截断（p99 溢出 ~10%），截断在 JSON 输出场景是硬 fail（parse error）— 不能接受。
- **1536 不够**，会在 p80+ 被截 — 明确 no-go。
- **推荐 2400** — 比 p99 多 10% buffer，既砍了一半 max_tokens（4096→2400）延迟收益立等可取，又避免截断误伤。
- 确认前**必须做 T7 smoke test**：用 reentrancy 合约 + 3 个真实客户合约实测 output token 分布，若 p99 < 1800 则进一步下调到 2048。

**Action**：T7 smoke test 的指标里**必须抓 `response.usage.completion_tokens`**。

---

## Rollout Plan（4 步）

### P1 — Env 配置 + 部署（15 min）
1. Railway → Variables 写入上节 6 个 env
2. Railway 自动 redeploy
3. 过一遍 `/health`（scanner 内部 endpoint，未对外）确认服务起来
4. Git commit：本文件 + 本文件相关的环境变量更新说明（写入 `docs/launch-assets/deployment-runbook.md` 或类似位置，本次不涉及）

### P2 — 代码层 fallback（30 min，**本次暂不做**，留给后续 iteration）
**想法**：`analyze_with_llm` 在 primary 失败（非 parse error 的异常）时自动切 fallback model 重试一次。目前 `llm_service.py:154-165` 已有"带 response_format 失败→不带 response_format 重试"的同模型 retry，需扩展为"同模型降级 retry 失败 → fallback model 再试一次"。

Scope：保守估计 +40 行代码，要加对应单元测试（mock primary 抛异常 → 验证走 fallback）。**独立 PR** 做，不跟本次 env 配置混。

### P3 — max_tokens 调整（5 min，跟 T7 后做）
`app/services/llm_service.py:151` 和 `:161` 两处 `max_tokens=4096` → 根据 T7 smoke test 实测数据改为 `2400` 或 `2048`。

### P4 — Smoke Test（T7，60 min）
见下节。

---

## Smoke Test 验收（T7）

**目标**：4 档 primary model 都能对 reentrancy 合约返回合法 JSON + critical/high finding。

**测试合约**：`smoke-test.md` 里的 `Vulnerable` 合约（reentrancy 漏洞）。

**执行方式**：本地跑 `pytest tests/integration/test_llm_tiers.py`（新建），4 个 case × 1 个合约：

```python
@pytest.mark.parametrize("tier,model", [
    ("free", "accounts/fireworks/models/qwen3-8b"),
    ("starter", "accounts/fireworks/models/deepseek-v3p1"),
    ("pro", "accounts/fireworks/models/deepseek-v3p2"),
    ("business", "accounts/fireworks/models/llama-v3p3-70b-instruct"),
])
def test_tier_reentrancy_detection(tier, model):
    response, usage, latency = run_scan(VULNERABLE_REENTRANCY, tier=tier)
    assert any("reentrancy" in v.title.lower() for v in response.vulnerabilities)
    assert any(v.severity in ("critical", "high") for v in response.vulnerabilities)
    assert response.model_used == model
    record_metrics(tier, model, usage, latency)
```

**采集指标**（写入 `docs/fireworks-smoke-test-results.md`，T7 交付）：
- `scan_time_ms`
- `response.usage.prompt_tokens`
- `response.usage.completion_tokens`
- 是否命中 reentrancy finding
- severity

**PASS 标准**：
- ✅ 4/4 tier 都能检出 reentrancy 且 severity ≥ high
- ✅ 所有 `completion_tokens < 2400`（如果 p99 ≥ 2200 则保持 4096 max_tokens）
- ✅ 所有 `scan_time_ms < 30_000`（RapidAPI sync 硬顶）

---

## 风险 + 回滚

| 风险 | 概率 | 处理 |
|---|---|---|
| Fireworks serverless 某 model 下架 / 改模型 ID | 低 | Env 配置可直接改，无需 redeploy 代码；V3.1/V3.2 一起下架几乎不可能 |
| Qwen3 8B 对复杂合约 false-negative 过多 | 中 | Free 档先忍；若 upgrade 率明显低，切 V3.1 做 Free primary（牺牲成本换转化） |
| Llama 3.3 70B BUSINESS 档延迟不如文宣数据 | 中 | T7 实测；若 > 20s，回切 V3.2 做 BUSINESS primary |
| 全 Fireworks 挂（provider 级故障） | 低 | P2 代码层 fallback 同 provider 内切 model 无用；长期看需准备 Anthropic / Gemini 跨 provider fallback，但**非本次 scope** |

**回滚**：env 改回单模型即可：
```bash
LLM_MODEL_FREE=
LLM_MODEL_STARTER=
LLM_MODEL_PRO=
LLM_MODEL_BUSINESS=
LLM_MODEL=accounts/fireworks/models/deepseek-v3p1
```
全档回到 V3.1 单模型，所有 tier 共用。

---

## 交付物 checklist

- [x] 本文件（`docs/fireworks-model-selection.md`）— develop 分支
- [ ] Railway env 配置（Lion 执行，本次**不做**，等 Lion 确认矩阵后动手）
- [ ] T7 smoke test 代码 + 结果（`tests/integration/test_llm_tiers.py` + `docs/fireworks-smoke-test-results.md`）
- [ ] max_tokens 调整（P3）
- [ ] Code-layer fallback（P2，独立 PR）

**依赖**：Q2 的实测答案要 T7 先跑完；P2 / P3 等 T7 数据。本次 PR 仅交付**选型文档 + env 配置清单**，代码动作另起 PR。
