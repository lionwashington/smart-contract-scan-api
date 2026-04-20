# Competitor LLM Disclosure — Evidence Scan

调研日期 2026-04-15，时间盒 30min。方法：WebFetch 并行抓 landing / pricing / docs，只记落地可引用的公开文本。

## 证据矩阵

| 对象 | 品类 | 暴露 LLM 名？ | 暴露供应商？ | 暴露引擎？ | URL 证据 |
|------|------|---------------|--------------|------------|----------|
| SolidityScan (landing) | Web3 安全扫描 | ❌ | ❌ | ❌ | solidityscan.com — 仅 "AI-Powered Scanning" |
| SolidityScan (pricing) | 同上 | ❌ | ❌ | ❌ | solidityscan.com/pricing — 无任何模型/引擎名 |
| SolidityScan (docs) | 同上 | ❌ | ❌ | ❌ | docs.solidityscan.com — 无披露 |
| OpenZeppelin Defender | Web3 安全平台 | N/A 抓取失败 | N/A | N/A | openzeppelin.com/defender 返回 404/极简 |
| Hacken | Web3 审计 | ❌ | ❌ | ❌ | hacken.io — 仅 "AI-powered threat signals" |
| Cyfrin | Web3 审计工具 | ❌ | ❌ | ✅ 自有 Aderyn | cyfrin.io — "Aderyn - Solidity Static Analyzer"（自家工具，非第三方引擎） |
| Slither (crytic) | 静态分析引擎 | ❌ | ❌ | — | github.com/crytic/slither — 不涉 LLM |
| MythX | Web3 扫描 | N/A 403 | N/A | N/A | 站点疑似下线 |
| Snyk DeepCode AI | 通用 SAST+AI | ❌ | ❌（明确反向定位） | ❌ | snyk.io/platform/deepcode-ai — "purpose-built… contrasts with Single-model AI like GPT-4"，强调自研 |
| DeepSource | 通用 SAST+AI | ❌ | ✅ 部分（BYOK） | ❌ | deepsource.com/pricing — Enterprise BYOK 支持 "Anthropic Claude, OpenAI, or Google Gemini"；两档 AI 不绑定模型 |
| Cursor | AI 编码 IDE | ❌（仅家族名） | ✅ | — | cursor.com/pricing — "3x usage on all OpenAI, Claude, Gemini models" |
| GitHub Copilot | AI 编码 | ✅ 完整 SKU | ✅ | — | github.com/features/copilot/plans — 列 "Anthropic Claude Haiku 4.5 / Sonnet 4.6 / Opus 4.6、OpenAI GPT-5.x、Google Gemini 2.5 Pro / 3 Flash、xAI Grok" |
| Sourcegraph Cody | AI 编码 | ❌ | ❌ | — | sourcegraph.com/cody — "uses all the latest LLMs" |
| Windsurf (前 Codeium) | AI 编码 | ❌ | ❌ | — | windsurf.com/pricing — 仅 "all premium models" + 自研 SWE-1.5 |
| Perplexity | AI 搜索 | ✅（FAQ） | ✅ | — | pro 页 403；公开 FAQ 长期列 GPT-4/Claude/Sonar |
| Phind | AI 搜索/编码 | N/A 403 | N/A | — | phind.com 抓取失败 |
| You.com | AI 搜索 | ❌ | ❌ | — | you.com/plans — 仅通用描述 |

## 结论

1. **Web3 安全垂直品类全员隐藏**：SolidityScan / Hacken / Cyfrin / OpenZeppelin 在 landing+pricing+docs 均**未披露**底层 LLM 或静态引擎。唯一"引擎"披露是 Cyfrin 把 Aderyn 当**自有产品**宣传（不是"我用了 Slither"）。这个品类的行业惯例就是把 AI 和静态分析包成黑盒卖。
2. **通用 SAST+AI 也倾向隐藏**：Snyk 甚至主动反向定位（"不像 GPT-4 那种单模型"）；DeepSource 只在企业 BYOK 这个必须披露的场景里点名 Anthropic/OpenAI/Google。
3. **AI 编码 IDE 是分裂的**：Copilot / Cursor 把模型当**卖点**（用户要选），Cody / Windsurf 只说"all LLMs"。差别来自用户是否有模型选择权。
4. **通用 AI 搜索**也分裂：Perplexity 披露；You.com 不披露。

## 我们应该怎么做

**保留现方案（通用档位，不暴露 Claude / Anthropic / Slither）。** 理由：

- 最接近的参照组（Web3 安全扫描 API）是 100% 黑盒，0 个竞品暴露 LLM 或引擎。我们暴露反而**显得不专业**（像"我们只是套壳 Claude + Slither"）。
- Snyk 的反向定位是可借鉴话术："purpose-built for smart contract security"，暗示是组合系统而非单模型套壳。
- Copilot/Cursor 的暴露模式**不适用**——那是因为用户要在 IDE 里切换模型；API 扫描服务用户不需要也不关心选模型。
- 留一条**退路**：参考 DeepSource，未来做 Enterprise BYOK 时再披露（"bring your own Anthropic/OpenAI key"），那时暴露是有商业理由的。

**landing/pricing 文案建议**（无需两套备选）：
- 保留 "AI-powered" / "advanced static analysis" / 档位用 Fast / Standard / Deep 而非 Haiku / Sonnet / Opus。
- FAQ 若被问"用什么模型"：回答 "We use a combination of proprietary heuristics, industry-standard static analysis, and frontier LLMs. The exact stack evolves as better models ship." —— 这是 SolidityScan + Snyk 的混合话术。
