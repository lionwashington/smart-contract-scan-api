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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """返回单例配置对象（带缓存）"""
    return Settings()
