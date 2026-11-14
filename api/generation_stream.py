"""将线程中的模型分片即时转发为浏览器可读取的 SSE。"""

import asyncio
import json
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse

from agents.llm_streaming import GENERATION_EVENTS
from core.logging import get_logger


logger = get_logger("generation_stream")
_running: set[asyncio.Task] = set()
HEARTBEAT_SECONDS = 10


def encode_event(event: str, data: dict) -> str:
    payload = json.dumps(jsonable_encoder(data), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def generation_response(operation: Callable[[], Awaitable], *, message="正在读取资料，准备生成…") -> StreamingResponse:
    async def stream():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        listening = True

        def publish(event, data):
            if listening and not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, (event, data))

        async def run():
            token = GENERATION_EVENTS.set(publish)
            try:
                result = await operation()
                publish("result", jsonable_encoder(result))
                logger.info("流式生成任务已完成")
            except HTTPException as exc:
                publish("error", {"message": exc.detail, "status": exc.status_code})
                logger.warning("流式生成未完成：状态码=%s，原因=%s", exc.status_code, exc.detail)
            except Exception as exc:
                publish("error", {"message": "生成过程异常，请核对保存结果后重试。", "status": 500})
                logger.warning("流式生成异常：异常类型=%s", type(exc).__name__)
            finally:
                GENERATION_EVENTS.reset(token)

        # If the browser disconnects, finish this single operation in the
        # background. Never cancel or retry a possibly committed database write.
        task = asyncio.create_task(run())
        _running.add(task)
        task.add_done_callback(_running.discard)
        try:
            yield encode_event("status", {"message": message})
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": 保持连接\n\n"
                    continue
                yield encode_event(event, data)
                if event in {"result", "error"}:
                    break
        finally:
            listening = False
            if not task.done():
                logger.info("前端连接已断开，后台继续完成当前生成；请核对保存结果")

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })
