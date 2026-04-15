"""
配置管理：从环境变量读取所有配置项
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


VALID_TIERS = ("free", "starter", "pro", "business")


class Settings(BaseSettings):
    # LLM 全局默认（向后兼容；未配置 per-tier 时 fallback 用这套）
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

    # Per-tier 模型（为空 → fallback llm_model）
    llm_model_free: str = ""
    llm_model_starter: str = ""
    llm_model_pro: str = ""
    llm_model_business: str = ""

    # Per-tier base_url / api_key 可选覆盖（为空 → fallback 全局）
    llm_base_url_free: str = ""
    llm_base_url_starter: str = ""
    llm_base_url_pro: str = ""
    llm_base_url_business: str = ""
    llm_api_key_free: str = ""
    llm_api_key_starter: str = ""
    llm_api_key_pro: str = ""
    llm_api_key_business: str = ""

    max_contract_size: int = 100 * 1024

    def resolve_llm(self, tier: str) -> tuple[str, str, str]:
        """根据 tier 返回 (base_url, api_key, model)。未配置字段 fallback 到全局。"""
        if tier not in VALID_TIERS:
            tier = "free"
        base = getattr(self, f"llm_base_url_{tier}", "") or self.llm_base_url
        key = getattr(self, f"llm_api_key_{tier}", "") or self.llm_api_key
        model = getattr(self, f"llm_model_{tier}", "") or self.llm_model
        return base, key, model

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
