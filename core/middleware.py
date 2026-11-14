"""保留流式响应行为的 ASGI 请求日志中间件。"""

import asyncio
import re
import time
from uuid import uuid4

from core.logging import REQUEST_ID, get_logger


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app
        self.logger = get_logger("http")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))
        incoming = headers.get(b"x-request-id", b"").decode("ascii", errors="replace")
        request_id = incoming if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", incoming) else uuid4().hex
        token = REQUEST_ID.set(request_id)
        started = time.perf_counter()
        status = 500
        outcome = "已完成"
        method = scope["method"]
        # 不读取请求体、查询参数、Cookie 或认证头。
        path = scope.get("path", "").replace("\r", "").replace("\n", "")

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        self.logger.info("请求开始：方法=%s，路径=%s", method, path)
        try:
            await self.app(scope, receive, send_with_id)
        except asyncio.CancelledError:
            outcome = "已中断"
            raise
        except Exception:
            outcome = "失败"
            self.logger.exception("请求异常：方法=%s，路径=%s", method, path)
            raise
        finally:
            level = 40 if outcome == "失败" or status >= 500 else 30 if status >= 400 or outcome == "已中断" else 20
            self.logger.log(
                level, "请求结束：方法=%s，路径=%s，状态码=%s，结果=%s，耗时=%.2f毫秒",
                method, path, status, outcome, (time.perf_counter() - started) * 1000,
            )
            REQUEST_ID.reset(token)
