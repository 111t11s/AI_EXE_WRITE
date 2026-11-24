"""将 core.model_config 中的模型清单转换为安全的可选档案。"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.config import get_settings
from core.model_config import ModelConfig


KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def endpoint_host(base_url: str) -> str:
    """只保留主机名，便于日志定位问题且不泄露完整地址参数。"""
    try:
        return urlsplit(base_url).hostname or "-"
    except ValueError:
        return "-"


def safe_error_reason(exc) -> str:
    """提取上游错误说明，隐藏密钥后用于日志与提示。"""
    message = ""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("detail") or "")
        elif isinstance(error, str):
            message = error
        if not message:
            message = str(body.get("message") or "")
    elif isinstance(body, str):
        # Cloudflare 等边缘节点常返回 HTML 错误页，只保留可读文字。
        message = re.sub(r"<[^>]+>", " ", body)
    if not message:
        message = str(getattr(exc, "message", "") or "")
    message = KEY_PATTERN.sub("sk-***", message)
    return " ".join(message.split())[:160]


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
    main_model: bool = False

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
    configured = ModelConfig().get_model()
    return tuple(
        ModelProfile(
            id=key.lower(),
            label=item["label"],
            model=item["model_name"],
            api_key=item["api_key"],
            base_url=item["base_url"],
            timeout=timeout,
            main_model=item["main_model"],
        )
        for key, item in configured.items()
    )


def available_profiles() -> list[ModelProfile]:
    profiles = [profile for profile in model_profiles() if profile.available]
    return sorted(profiles, key=lambda profile: not profile.main_model)


def default_profile_id() -> str:
    """返回已配置模型中的主模型；主模型密钥缺失时选第一个可用模型。"""
    profiles = available_profiles()
    primary = next((profile for profile in profiles if profile.main_model), None)
    return (primary or (profiles[0] if profiles else None)).id if profiles else ""


def resolve_profile(profile_id: str | None) -> ModelProfile:
    """按模型清单 ID 解析连接；未指定时使用 ``main_model`` 标记的模型。"""
    wanted = (profile_id or "").strip()
    if not wanted or wanted == "default":
        wanted = default_profile_id()
        if not wanted:
            raise ModelProfileError("模型服务尚未配置 API 密钥")
    else:
        # 接受旧版模型选择器曾发送的档案 ID。
        wanted = {"deepseek": "deepseek_flash", "gemini-pro": "gemini_4_argon"}.get(wanted, wanted)
    profile = next((item for item in model_profiles() if item.id == wanted), None)
    if profile is None:
        raise ModelProfileError("所选模型不存在，请重新选择")
    if not profile.available:
        raise ModelProfileError("所选模型尚未配置 API 密钥，请检查 .env 或改选其他模型")
    return profile
