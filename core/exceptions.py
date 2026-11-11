"""
自定义异常和错误处理
"""

from typing import Any, Optional


class AIAPIError(Exception):
    """AI API基础异常"""
    
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: Optional[str] = None,
        details: Optional[dict] = None
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or "INTERNAL_ERROR"
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(AIAPIError):
    """认证失败"""
    
    def __init__(self, message: str = "认证失败，请检查API密钥", details: Optional[dict] = None):
        super().__init__(
            message=message,
            status_code=401,
            error_code="AUTHENTICATION_ERROR",
            details=details
        )


class RateLimitError(AIAPIError):
    """请求频率超限"""
    
    def __init__(self, message: str = "请求频率超限，请稍后重试", details: Optional[dict] = None):
        super().__init__(
            message=message,
            status_code=429,
            error_code="RATE_LIMIT_ERROR",
            details=details
        )


class InvalidRequestError(AIAPIError):
    """无效的请求"""
    
    def __init__(self, message: str = "无效的请求参数", details: Optional[dict] = None):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_REQUEST",
            details=details
        )


class ModelNotFoundError(AIAPIError):
    """模型不存在"""
    
    def __init__(self, model: str, details: Optional[dict] = None):
        super().__init__(
            message=f"模型 '{model}' 不存在或不可用",
            status_code=404,
            error_code="MODEL_NOT_FOUND",
            details=details
        )


class ProviderNotAvailableError(AIAPIError):
    """提供商不可用"""
    
    def __init__(self, provider: str, details: Optional[dict] = None):
        super().__init__(
            message=f"LLM提供商 '{provider}' 不可用或未配置",
            status_code=503,
            error_code="PROVIDER_NOT_AVAILABLE",
            details=details
        )


class NetworkError(AIAPIError):
    """网络错误"""
    
    def __init__(self, message: str = "网络请求失败", details: Optional[dict] = None):
        super().__init__(
            message=message,
            status_code=503,
            error_code="NETWORK_ERROR",
            details=details
        )


class ContextLengthExceededError(AIAPIError):
    """上下文长度超限"""
    
    def __init__(self, message: str = "输入内容超出模型上下文长度限制", details: Optional[dict] = None):
        super().__init__(
            message=message,
            status_code=400,
            error_code="CONTEXT_LENGTH_EXCEEDED",
            details=details
        )
