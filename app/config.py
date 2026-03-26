"""
配置管理：从环境变量读取所有配置项
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # LLM 代理配置
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = "your-api-key"
    llm_model: str = "claude-sonnet-4-6"

    # API 保护（可选）：设置后要求 Bearer Token 鉴权
    api_key: str = ""

    # 合约大小限制（字节），默认 100KB
    max_contract_size: int = 100 * 1024

    # Web3 Pay billing
    web3pay_url: str = "http://localhost:3000"
    web3pay_enabled: bool = False  # 开关：False时不计费，方便本地开发
    scan_cost_cents: int = 10      # 每次扫描收费10 cents ($0.10)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """返回单例配置对象（带缓存）"""
    return Settings()
