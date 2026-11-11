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

    # MySQL 配置；未配置时仅数据库接口不可用。
    DB_HOST: str = ""
    DB_PORT: int = Field(default=3306, ge=1, le=65535)
    DB_USER: str = ""
    DB_PASSWORD: SecretStr | None = None
    DB_NAME: str = ""
    DB_TLS_REQUIRED: bool = False
    DB_TLS_CA: str = ""

    # OpenAI API key
    API_KEY: str = ""
    PROXY_URL: str = "https://api.meai.cloud/v1"

    # 模型名称
    DEEPSEEK_MODEL_NAME: str = "deepseek-v4-pro"
    GLM_MODEL_NAME: str = "glm-5.1"
    QWEN_MODEL_NAME: str = "qwen3.6-plus"
    KIMI_MODEL_NAME: str = "kimi-k2.6"

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
