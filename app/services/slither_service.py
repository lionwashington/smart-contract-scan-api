"""
Slither 静态分析服务
- 将提交的 Solidity 代码写入临时文件
- 通过子进程运行 Slither 并获取 JSON 输出
- 解析结构化结果，清理临时文件
- 优雅处理错误（Slither 未发现问题也是有效结果）
"""
import subprocess
import tempfile
import json
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_slither(source_code: str) -> dict[str, Any]:
    """
    对 Solidity 源码运行 Slither 静态分析。
    返回解析后的结构化结果字典。
    """
    # 写入临时 .sol 文件
    with tempfile.NamedTemporaryFile(
        suffix=".sol",
        mode="w",
        encoding="utf-8",
        delete=False
    ) as tmp_file:
        tmp_file.write(source_code)
        tmp_path = tmp_file.name

    try:
        result = _execute_slither(tmp_path)
        return result
    finally:
        # 无论成功还是失败，都清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _execute_slither(sol_path: str) -> dict[str, Any]:
    """
    执行 Slither 命令并解析输出。
    """
    cmd = [
        "slither",
        sol_path,
        "--json", "-",          # 输出 JSON 到 stdout
        "--no-fail-pedantic",   # 不因 pedantic 问题而失败
        "--disable-color",      # 禁用 ANSI 颜色码
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,   # 最多等待 2 分钟
        )
    except FileNotFoundError:
        logger.error("Slither 未安装或不在 PATH 中")
        return {
            "success": False,
            "error": "Slither not installed",
            "findings": [],
            "raw": "",
        }
    except subprocess.TimeoutExpired:
        logger.error("Slither 分析超时")
        return {
            "success": False,
            "error": "Slither analysis timed out",
            "findings": [],
            "raw": "",
        }

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    # Slither 在发现问题时返回非 0，但仍有 JSON 输出，这是正常的
    # 只有当没有 JSON 输出时才认为是真正的错误
    if not stdout:
        logger.warning("Slither 无 JSON 输出，stderr: %s", stderr[:500])
        # 检查是否是 solc 相关错误
        if "solc" in stderr.lower() or "compilation" in stderr.lower():
            return {
                "success": False,
                "error": f"Compilation error: {stderr[:1000]}",
                "findings": [],
                "raw": stderr,
            }
        return {
            "success": False,
            "error": stderr[:1000] if stderr else "Unknown Slither error",
            "findings": [],
            "raw": stderr,
        }

    # 解析 JSON
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.error("Slither JSON 解析失败: %s", e)
        return {
            "success": False,
            "error": f"Failed to parse Slither output: {e}",
            "findings": [],
            "raw": stdout[:2000],
        }

    findings = _parse_slither_results(data)

    return {
        "success": True,
        "error": None,
        "findings": findings,
        "raw": stdout,
    }


def _parse_slither_results(data: dict) -> list[dict[str, Any]]:
    """
    将 Slither JSON 输出解析为结构化 findings 列表。
    """
    findings = []

    # Slither JSON 格式：{"success": true, "error": null, "results": {"detectors": [...]}}
    results = data.get("results", {})
    detectors = results.get("detectors", [])

    for item in detectors:
        check = item.get("check", "unknown")
        impact = item.get("impact", "Informational")
        confidence = item.get("confidence", "Low")
        description = item.get("description", "")

        # 提取位置信息
        elements = item.get("elements", [])
        locations = []
        for elem in elements:
            src = elem.get("source_mapping", {})
            filename = elem.get("name", "")
            lines = src.get("lines", [])
            if lines:
                line_str = f"Line {lines[0]}" if len(lines) == 1 else f"Lines {lines[0]}-{lines[-1]}"
                if filename:
                    locations.append(f"{filename} ({line_str})")
                else:
                    locations.append(line_str)
            elif filename:
                locations.append(filename)

        findings.append({
            "check": check,
            "impact": impact,          # High / Medium / Low / Informational / Optimization
            "confidence": confidence,  # High / Medium / Low
            "description": description.strip(),
            "location": "; ".join(locations) if locations else None,
        })

    return findings
