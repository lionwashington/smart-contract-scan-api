"""
Pydantic 数据模型：定义 API 请求和响应的数据结构
"""
from pydantic import BaseModel, Field
from typing import Optional


class ScanRequest(BaseModel):
    """扫描请求模型"""
    source_code: str = Field(..., description="Solidity 源代码")
    contract_name: Optional[str] = Field(None, description="合约名称（可选）")
    language: str = Field("zh", description="报告语言：zh（中文）或 en（英文）")


class Vulnerability(BaseModel):
    """漏洞信息模型"""
    id: str = Field(..., description="漏洞编号，如 VULN-001")
    severity: str = Field(..., description="严重程度：critical / high / medium / low / info")
    title: str = Field(..., description="漏洞标题")
    description: str = Field(..., description="漏洞详细描述")
    location: Optional[str] = Field(None, description="漏洞位置：行号或函数名")
    recommendation: str = Field(..., description="修复建议")


class ScanResponse(BaseModel):
    """扫描响应模型（同步 / 完成后）"""
    scan_id: str = Field(..., description="扫描任务 UUID")
    status: str = Field(..., description="状态：completed 或 error")
    summary: str = Field(..., description="一段话总结")
    risk_score: int = Field(..., ge=0, le=100, description="风险评分 0-100，越高越危险")
    vulnerabilities: list[Vulnerability] = Field(default_factory=list, description="漏洞列表")
    gas_optimizations: list[str] = Field(default_factory=list, description="Gas 优化建议列表")
    scan_time_ms: int = Field(..., description="扫描耗时（毫秒）")


class ScanQueuedResponse(BaseModel):
    """异步提交响应：任务已入队"""
    scan_id: str = Field(..., description="扫描任务 UUID，用于轮询结果")
    status: str = Field("queued", description="queued")
    poll_url: str = Field(..., description="轮询结果的 URL 路径")


class ScanStatusResponse(BaseModel):
    """异步任务状态查询响应"""
    scan_id: str
    status: str = Field(..., description="queued / running / completed / failed")
    result: Optional[ScanResponse] = Field(None, description="完成后的扫描结果")
    error: Optional[str] = Field(None, description="失败原因")
