"""模型配置档案：把 .env 中的主模型与 DeepSeek 配置统一成可选项。

生成请求只提交档案 ID（``default`` / ``deepseek``），密钥和地址始终留在
服务端，不下发到浏览器，也不写进模型上下文。主模型沿用原有环境配置，
选中它时返回 ``None``，调用方继续使用原逻辑。
"""

import os
from dataclasses import dataclass

from core.config import get_settings


DEFAULT_PROFILE = "default"


class ModelProfileError(LookupError):
    """请求的模型档案不存在或尚未配置完整。"""


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    model: str
    api_key: str
    base_url: str
    timeout: int = 120

    @property
    def available(self) -> bool:
        return bool(self.model.strip() and self.api_key.strip() and self.base_url.strip())

    def connection(self) -> dict:
        """OpenAI 兼容客户端参数，供智能体直接使用。"""
        return {"model": self.model, "api_key": self.api_key,
                "base_url": self.base_url, "timeout": self.timeout}

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "model": self.model}


def model_profiles() -> tuple[ModelProfile, ...]:
    settings = get_settings()
    timeout = settings.REQUEST_TIMEOUT or 120
    # 主模型沿用历史配置：MODEL_NAME 优先，未设置时回退到 DEEPSEEK_MODEL_NAME。
    default_model = os.getenv("MODEL_NAME") or settings.DEEPSEEK_MODEL_NAME
    return (
        ModelProfile(DEFAULT_PROFILE, "主模型", default_model, settings.API_KEY,
                     settings.PROXY_URL, timeout),
        ModelProfile("deepseek", "DeepSeek", settings.DEEPSEEK_MODEL_NAME,
                     settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_PROXY_URL, timeout),
    )


def available_profiles() -> list[ModelProfile]:
    return [profile for profile in model_profiles() if profile.available]


def resolve_profile(profile_id: str | None) -> ModelProfile | None:
    """返回需要覆盖的模型档案；主模型或省略时返回 None（沿用原配置）。"""
    wanted = (profile_id or "").strip()
    if not wanted or wanted == DEFAULT_PROFILE:
        return None
    profile = next((item for item in model_profiles() if item.id == wanted), None)
    if profile is None:
        raise ModelProfileError("所选模型不存在，请重新选择")
    if not profile.available:
        raise ModelProfileError("所选模型尚未配置完整，请检查 .env 或改选其他模型")
    return profile
