"""
配置管理模块
支持环境变量配置和Pydantic Settings
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # 应用基础配置
    APP_NAME: str = "AI_WRITE"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # 服务配置
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    API_PREFIX: str = "/v1"
    CORS_ORIGINS: str = "https://localhost,capacitor://localhost"
    ENABLE_API_DOCS: bool = True
    AUTH_REGISTRATION_ENABLED: bool = True
    AUTH_SESSION_HOURS: int = Field(default=720, ge=1, le=8760)

    # MySQL 配置；未配置时仅数据库接口不可用。
    DB_HOST: str = ""
    DB_PORT: int = Field(default=3306, ge=1, le=65535)
    DB_USER: str = ""
    DB_PASSWORD: SecretStr | None = None
    DB_NAME: str = ""
    DB_TLS_REQUIRED: bool = False
    DB_TLS_CA: str = ""

    # 模型名称清单在 core/model_config.py；密钥和服务地址通过环境变量配置。
    GPT_API_KEY: str = ""
    # 兼容已有部署，未设置 GPT_API_KEY 时作为 GPT 密钥使用。
    API_KEY: str = ""
    # 模型服务地址可按部署环境覆盖。
    GPT_BASE_URL: str = ""
    GEMINI_BASE_URL: str = ""
    DEEPSEEK_BASE_URL: str = ""
    # Application code handles MCP reads/writes. Enable model-side tools only
    # after verifying the gateway supports the full CrewAI request format.
    MODEL_NATIVE_TOOLS: bool = False

    # 各服务密钥由环境变量注入；名称和地址不从环境变量读取。
    GEMINI_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""

    # 热点选题：抓取小说网站榜单；可用“网站名|URL”逗号分隔覆盖默认来源
    TREND_CACHE_MINUTES: int = Field(default=30, ge=1, le=1440)
    TREND_TIMEOUT: int = Field(default=8, ge=1, le=60)
    TREND_NOVEL_SOURCE_URLS: str = ""

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_FILE: Optional[str] = "logs/ai_write.log"
    LOG_MAX_BYTES: int = Field(default=10 * 1024 * 1024, ge=1024)
    LOG_BACKUP_COUNT: int = Field(default=5, ge=1)

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL 必须是 DEBUG、INFO、WARNING、ERROR 或 CRITICAL")
        return value

    # 请求限制
    MAX_TOKENS: Optional[int] = Field(default=4096, ge=1)
    REQUEST_TIMEOUT: Optional[int] = Field(default=120, ge=1)

@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
