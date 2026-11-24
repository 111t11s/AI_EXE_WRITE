"""模型列表与非敏感连接配置；API 密钥仍从环境变量读取。"""

from typing import Any

from core.config import get_settings


class ModelConfig:
    """集中定义可选模型，并从 Settings 注入各服务的密钥。"""

    def __init__(self):
        self.settings = get_settings()
        self.model_dict = self.get_model()

    def _setting(self, name: str, fallback: str = "") -> str:
        value = getattr(self.settings, name, None)
        if not value and fallback:
            value = getattr(self.settings, fallback, "")
        return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")

    def get_model(self) -> dict[str, dict[str, Any]]:
        """合并各服务的模型清单，``main_model`` 标记唯一默认模型。"""
        models = {
            **self.set_deepsee_model(),
            **self.set_gpt_model(),
            **self.set_gemini_model(),
        }
        if sum(bool(model["main_model"]) for model in models.values()) != 1:
            raise ValueError("模型清单必须且只能有一个 main_model")
        return models

    def set_deepsee_model(self) -> dict[str, dict[str, Any]]:
        api_key = self._setting("DEEPSEEK_API_KEY")
        base_url = self._setting("DEEPSEEK_BASE_URL")
        return {
            "DEEPSEEK_FLASH": {
                "label": "DeepSeek Flash",
                "base_url": base_url,
                "model_name": "deepseek-flash",
                "api_key": api_key,
                "main_model": False,
            },
            "DEEPSEEK_PRO": {
                "label": "DeepSeek Pro",
                "base_url": base_url,
                "model_name": "deepseek-pro",
                "api_key": api_key,
                "main_model": False,
            },
        }

    def set_gpt_model(self) -> dict[str, dict[str, Any]]:
        api_key = self._setting("GPT_API_KEY", "API_KEY")
        base_url = self._setting("GPT_BASE_URL")
        return {
            "GPT_6LUNA": {
                "label": "GPT 6 Luna",
                "base_url": base_url,
                "model_name": "gpt-6-luna",
                "api_key": api_key,
                "main_model": True,
            },
            "GPT_6_SOL": {
                "label": "GPT 6 Sol",
                "base_url": base_url,
                "model_name": "gpt-6-sol",
                "api_key": api_key,
                "main_model": False,
            },
            "GPT_6.1_SOL": {
                "label": "GPT 6.1 Sol",
                "base_url": base_url,
                "model_name": "gpt-6.1-sol",
                "api_key": api_key,
                "main_model": False,
            },
            "GPT_6_ASTRA": {
                "label": "GPT 6 Astra",
                "base_url": base_url,
                "model_name": "gpt-6-astra",
                "api_key": api_key,
                "main_model": False,
            },
        }

    def set_gemini_model(self) -> dict[str, dict[str, Any]]:
        api_key = self._setting("GEMINI_API_KEY")
        base_url = self._setting("GEMINI_BASE_URL")
        return {
            "GEMINI_3.8_FLASH": {
                "label": "Gemini 3.8 Flash",
                "base_url": base_url,
                "model_name": "gemini-3.8-flash",
                "api_key": api_key,
                "main_model": False,
            },
            "GEMINI_4_ARGON": {
                "label": "Gemini 4 Argon",
                "base_url": base_url,
                "model_name": "gemini-4-argon",
                "api_key": api_key,
                "main_model": False,
            },
        }
