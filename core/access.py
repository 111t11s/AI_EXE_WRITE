"""Shared access key for a small, private writing group."""

from hmac import compare_digest

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from core.config import get_settings


class AccessKeyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http"
                and (scope.get("path", "") == "/api" or scope.get("path", "").startswith("/api/"))
                and scope["method"] != "OPTIONS"):
            settings = get_settings()
            expected = settings.APP_ACCESS_KEY.get_secret_value()
            if settings.ACCESS_KEY_REQUIRED and not expected:
                response = JSONResponse({"detail": "访问口令尚未配置"}, status_code=503)
                return await response(scope, receive, send)
            provided = Headers(scope=scope).get("x-aitxt-access-key", "")
            if expected and not compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
                response = JSONResponse({"detail": "请输入正确的访问口令"}, status_code=401)
                return await response(scope, receive, send)
        return await self.app(scope, receive, send)
