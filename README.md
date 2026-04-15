# Smart Contract Security Scan API

一个基于 Slither + LLM 的智能合约安全扫描 REST API。提交 Solidity 源代码，返回结构化的安全审计报告。

> **Landing / marketing assets** live in the private freelancer parent monorepo (`freelancer/scanner-landing/`). This repo only contains application code + developer docs.

## 技术栈

- **FastAPI** — 高性能 Python Web 框架
- **Slither** — Trail of Bits 出品的 Solidity 静态分析工具
- **OpenAI SDK** — 调用 LLM 进行深度解读（支持任意 OpenAI 兼容代理，如 Claude）
- **Railway** — 一键部署平台

## 本地运行

### 前置条件

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Slither 和 solc
pip install slither-analyzer solc-select
solc-select install 0.8.20
solc-select use 0.8.20
```

### 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 LLM_BASE_URL 和 LLM_API_KEY
```

### 启动服务

```bash
uvicorn app.main:app --reload --port 8000
```

服务启动后访问：
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

## 部署到 Railway

1. Fork 或 push 代码到 GitHub
2. 在 Railway 创建新项目，连接 GitHub 仓库
3. 在 Railway 项目设置中添加以下环境变量：
   - `LLM_BASE_URL` — LLM 代理地址
   - `LLM_API_KEY` — API 密钥
   - `LLM_MODEL` — 模型名（默认 `claude-sonnet-4-6`）
   - `API_KEY` — （可选）保护你的 API 接口

Railway 会自动检测 `railway.toml` 并用 Dockerfile 构建部署。

## API 使用

### 健康检查

```bash
curl https://your-app.railway.app/health
```

### 扫描合约

```bash
curl -X POST https://your-app.railway.app/api/v1/scan \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "source_code": "pragma solidity ^0.8.0;\n\ncontract Vulnerable {\n    mapping(address => uint256) public balances;\n\n    function deposit() public payable {\n        balances[msg.sender] += msg.value;\n    }\n\n    function withdraw(uint256 amount) public {\n        require(balances[msg.sender] >= amount);\n        (bool success, ) = msg.sender.call{value: amount}(\"\");\n        require(success);\n        balances[msg.sender] -= amount;\n    }\n}",
    "contract_name": "Vulnerable",
    "language": "zh"
  }'
```

### 响应示例

```json
{
  "scan_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "summary": "该合约存在严重的重入攻击漏洞，攻击者可通过递归调用 withdraw 函数耗尽合约余额。",
  "risk_score": 85,
  "vulnerabilities": [
    {
      "id": "VULN-001",
      "severity": "critical",
      "title": "重入攻击漏洞（Reentrancy）",
      "description": "withdraw 函数在更新余额之前先发送 ETH，攻击者可部署恶意合约在 receive() 中递归调用 withdraw，反复提取资金。",
      "location": "withdraw() 函数",
      "recommendation": "遵循 Checks-Effects-Interactions 模式：先更新状态（balances[msg.sender] -= amount），再执行外部调用。或使用 ReentrancyGuard。"
    }
  ],
  "gas_optimizations": [
    "balances 映射读取可缓存到局部变量，避免重复 SLOAD",
    "考虑使用 transfer() 替代 call() 以限制 gas（注意：这会限制接收方执行复杂逻辑）"
  ],
  "scan_time_ms": 4231
}
```

## 限流

每个 IP 每分钟最多 10 次请求。超过限制返回 HTTP 429。

## 计费 / 订阅

计费由 RapidAPI Hub 托管（Free 100/月、Hobby $9 2K/月、Pro $29 10K/月）。自托管版无内置计费，由 `API_KEY` 做访问控制即可。

## 注意事项

- 合约大小限制：默认 100KB（可通过 `MAX_CONTRACT_SIZE` 环境变量调整）
- Slither 需要合约能够被 solc 编译，编译失败时将仅依赖 LLM 进行分析
- 本服务为 MVP 版本，无持久化存储，重启后限流计数器会重置
