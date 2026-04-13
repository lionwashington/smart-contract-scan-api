"""
LLM 解读服务
- 使用 openai SDK 调用自定义代理（支持 Claude 等兼容接口）
- 将 Slither 结果 + 原始代码发给 LLM 进行深度分析
- 解析 LLM 的 JSON 响应为 ScanResponse 结构
"""
import json
import logging
import re
import uuid
from typing import Any

from openai import OpenAI

from app.config import get_settings
from app.models.schemas import ScanResponse, Vulnerability

logger = logging.getLogger(__name__)


def build_prompt(
    source_code: str,
    slither_findings: list[dict[str, Any]],
    contract_name: str | None,
    language: str,
) -> str:
    """
    构建发给 LLM 的系统提示词。
    语言参数控制输出为中文（zh）或英文（en）。
    """
    lang_instruction = (
        "请用中文输出所有分析结果，包括标题、描述和建议。"
        if language == "zh"
        else "Please output all analysis results in English."
    )

    # 压缩 Slither 输出：只保留 LLM 推理必要字段，去掉冗长 description/raw
    compressed = []
    for f in slither_findings[:30]:  # 超过 30 条的部分通常重复信息
        desc = (f.get("description") or "").strip()
        if len(desc) > 400:
            desc = desc[:400] + "..."
        compressed.append({
            "check": f.get("check"),
            "impact": f.get("impact"),
            "confidence": f.get("confidence"),
            "location": f.get("location"),
            "description": desc,
        })
    slither_section = (
        json.dumps(compressed, ensure_ascii=False)
        if compressed
        else "（Slither 未报告任何发现）"
    )

    contract_hint = f"合约名称: {contract_name}" if contract_name else ""

    return f"""你是一名资深的智能合约安全审计专家，专精于 Solidity 和 EVM 生态。
{lang_instruction}

{contract_hint}

## Slither 静态分析结果（JSON 格式）:
```json
{slither_section}
```

## 合约源代码:
```solidity
{source_code}
```

## 你的任务:
1. 深入解读 Slither 的每一条发现，用通俗语言解释漏洞原因和潜在影响。
2. 独立审计源代码，识别 Slither 可能遗漏的问题：
   - 业务逻辑漏洞
   - 访问控制缺陷（权限过度集中、缺少 modifier 等）
   - 重入攻击边缘情况
   - 整数溢出/下溢（即使使用了 SafeMath）
   - 前端运行/抢跑攻击（Front-running）
   - 时间戳依赖
   - 随机数可预测
   - 不安全的外部调用
   - 自毁（selfdestruct）风险
   - 委托调用（delegatecall）风险
3. 综合评估整体风险评分（0-100，越高越危险）。
4. 提出 3-5 条 Gas 优化建议。
5. 写一段话的总结。

## 输出格式要求（严格的 JSON，不要包含任何 Markdown 代码块）:
{{
  "summary": "一段话总结",
  "risk_score": 整数 (0-100),
  "vulnerabilities": [
    {{
      "id": "VULN-001",
      "severity": "critical|high|medium|low|info",
      "title": "漏洞标题",
      "description": "详细描述，包括攻击场景",
      "location": "行号或函数名（可选）",
      "recommendation": "具体修复建议"
    }}
  ],
  "gas_optimizations": [
    "优化建议1",
    "优化建议2"
  ]
}}

严格只输出 JSON，不要有任何前缀或后缀文字。不要使用 Markdown 代码块包装。
所有字符串值中的换行请使用 \\n 转义，双引号请使用 \\" 转义。确保输出是可被 json.loads() 直接解析的合法 JSON。"""


def analyze_with_llm(
    source_code: str,
    slither_result: dict[str, Any],
    contract_name: str | None,
    language: str,
    scan_id: str,
    scan_time_ms: int,
) -> ScanResponse:
    """
    调用 LLM 对合约进行深度分析，返回结构化的 ScanResponse。
    """
    settings = get_settings()
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    findings = slither_result.get("findings", [])
    slither_error = slither_result.get("error")

    # 如果 Slither 编译失败，直接告知 LLM（仍然尝试纯代码审计）
    if not slither_result.get("success") and slither_error:
        logger.warning("Slither 分析失败，将仅依赖 LLM 分析。错误: %s", slither_error)
        findings = []  # 传空列表，提示词中会说明

    prompt = build_prompt(source_code, findings, contract_name, language)

    # 先尝试带 response_format（OpenAI原生支持），失败则回退到不带的版本
    # Claude兼容接口不一定支持 response_format 参数
    try:
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning("LLM 调用失败（含 response_format），回退重试: %s", e)
        try:
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=4096,
            )
        except Exception as e2:
            logger.error("LLM 调用彻底失败: %s", e2)
            return _error_response(scan_id, scan_time_ms, str(e2))

    raw_content = response.choices[0].message.content or ""
    logger.debug("LLM 原始响应（前500字）: %s", raw_content[:500])

    return _parse_llm_response(raw_content, scan_id, scan_time_ms)


def _parse_llm_response(raw: str, scan_id: str, scan_time_ms: int) -> ScanResponse:
    """
    解析 LLM 的 JSON 输出为 ScanResponse。
    尽量健壮处理各种格式问题。
    """
    # 去掉可能的 Markdown 代码块包装
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[start:end]).strip()

    # 如果响应中混有非JSON文字，尝试提取最外层JSON对象
    if not cleaned.startswith("{"):
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if match:
            cleaned = match.group(0)

    # 多次尝试解析：原始 → 修复常见问题
    data = None
    for attempt_cleaned in [cleaned, _fix_json(cleaned)]:
        try:
            data = json.loads(attempt_cleaned)
            break
        except json.JSONDecodeError:
            continue

    if data is None:
        logger.error("LLM 响应 JSON 解析失败\n原文前1000字: %s", raw[:1000])
        return _error_response(scan_id, scan_time_ms, "LLM output JSON parse error")

    # 构建漏洞列表
    vulnerabilities = []
    for i, v in enumerate(data.get("vulnerabilities", []), start=1):
        try:
            vuln = Vulnerability(
                id=v.get("id", f"VULN-{i:03d}"),
                severity=_normalize_severity(v.get("severity", "info")),
                title=v.get("title", "Unknown"),
                description=v.get("description", ""),
                location=v.get("location"),
                recommendation=v.get("recommendation", ""),
            )
            vulnerabilities.append(vuln)
        except Exception as e:
            logger.warning("漏洞条目 %d 解析失败: %s", i, e)

    return ScanResponse(
        scan_id=scan_id,
        status="completed",
        summary=data.get("summary", "Analysis completed."),
        risk_score=max(0, min(100, int(data.get("risk_score", 0)))),
        vulnerabilities=vulnerabilities,
        gas_optimizations=data.get("gas_optimizations", []),
        scan_time_ms=scan_time_ms,
    )


def _fix_json(text: str) -> str:
    """
    尝试修复 LLM 输出中常见的 JSON 格式问题：
    - 字符串值中未转义的换行符
    - 字符串值中未转义的双引号
    - 尾部多余逗号
    - max_tokens 截断导致的未闭合字符串 / 括号（防御性恢复）
    """
    # 修复字符串值内的未转义换行符（替换为 \\n）
    fixed = re.sub(r'(?<=": ")(.*?)(?="[,\s\n\r]*[}\]])',
                   lambda m: m.group(0).replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'),
                   text, flags=re.DOTALL)
    # 移除尾部逗号（如 ,] 或 ,}）
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)

    # 截断恢复：若最后字符处于未闭合字符串/数组/对象，扫描并补齐。
    # 遍历字符，跟踪括号栈和字符串状态，忽略转义字符。
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in fixed:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    if in_string:
        fixed += '"'
    # 去掉尾部悬挂的 `,` 或 `"key":`（键存在但没值）
    fixed = fixed.rstrip()
    fixed = re.sub(r',\s*$', '', fixed)
    fixed = re.sub(r'"[^"\\]*"\s*:\s*$', '', fixed)
    fixed = re.sub(r',\s*$', '', fixed)
    while stack:
        fixed += "}" if stack.pop() == "{" else "]"
    return fixed


def _normalize_severity(severity: str) -> str:
    """将 LLM 输出的严重程度标准化为规定的枚举值。"""
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
        "info": "info",
        "informational": "info",
        "optimization": "info",
        "warning": "low",
    }
    return mapping.get(severity.lower(), "info")


def _error_response(scan_id: str, scan_time_ms: int, error_msg: str) -> ScanResponse:
    """构建错误响应（不抛异常，保持 API 稳定）。"""
    return ScanResponse(
        scan_id=scan_id,
        status="error",
        summary=f"Analysis failed: {error_msg}",
        risk_score=0,
        vulnerabilities=[],
        gas_optimizations=[],
        scan_time_ms=scan_time_ms,
    )
