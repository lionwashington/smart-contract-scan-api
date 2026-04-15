"""
配置管理：从环境变量读取所有配置项
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # LLM 代理配置
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = "your-api-key"
    llm_model: str = "claude-sonnet-4-6"

    # Auth 开关：默认 true（fail-safe，防止生产裸跑）
    # 本地 dev 请在 .env 设 AUTH_ENABLED=false
    auth_enabled: bool = True
    # 直连 Bearer token（运维/监控用）
    api_key: str = ""
    # RapidAPI 网关注入的 proxy secret
    rapidapi_proxy_secret: str = ""

    max_contract_size: int = 100 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=(),
    )


@lru_cache()
def get_settings() -> Settings:
    """返回单例配置对象（带缓存）"""
    return Settings()
