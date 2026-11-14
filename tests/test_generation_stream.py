import asyncio
import json
import threading

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from agents.llm_streaming import collect_chat_text, emit_generation_event
from api import generation_stream as streams


def event(frame):
    name, data = frame.strip().split("\n", 1)
    return name.removeprefix("event: "), json.loads(data.removeprefix("data: "))


def test_model_thread_forwards_text_before_generation_finishes():
    async def run():
        release = threading.Event()
        finish = threading.Event()

        def generate():
            def chunks():
                yield {"choices": [{"delta": {"reasoning_content": "先核对世界规则"}}]}
                assert release.wait(timeout=3), "Stream was buffered until completion"
                yield {"choices": [{"delta": {"content": "首批正文"}}]}
                assert finish.wait(timeout=3), "Content was buffered until completion"
                yield {"choices": [{"delta": {"content": "后续正文"}}]}
                yield {"choices": [], "usage": {"completion_tokens": 8}}
            return collect_chat_text(chunks())

        async def operation():
            return {"text": await run_in_threadpool(generate)}

        iterator = streams.generation_response(operation).body_iterator
        try:
            assert event(await anext(iterator))[0] == "status"
            first = event(await asyncio.wait_for(anext(iterator), timeout=2))
            assert first == ("thinking", {"text": "先核对世界规则"})
            release.set()
            assert event(await asyncio.wait_for(anext(iterator), timeout=2)) == ("delta", {"text": "首批正文"})
            finish.set()
            remaining = [event(frame) async for frame in iterator]
            assert remaining[-1] == ("result", {"text": "首批正文后续正文"})
        finally:
            release.set()
            finish.set()
            await iterator.aclose()

    asyncio.run(run())


def test_disconnect_finishes_single_write_in_background():
    async def run():
        release = asyncio.Event()
        completed = asyncio.Event()
        writes = []

        async def operation():
            emit_generation_event("delta", text="正文")
            await release.wait()
            writes.append("committed")
            completed.set()
            return {"id": 2}

        iterator = streams.generation_response(operation).body_iterator
        await anext(iterator)
        assert event(await anext(iterator))[0] == "delta"
        await iterator.aclose()
        release.set()
        await asyncio.wait_for(completed.wait(), timeout=1)
        assert writes == ["committed"]

    asyncio.run(run())


def test_stream_has_heartbeats_and_reports_errors_without_success(monkeypatch):
    monkeypatch.setattr(streams, "HEARTBEAT_SECONDS", 0.01)

    async def run():
        release = asyncio.Event()

        async def operation():
            await release.wait()
            raise HTTPException(504, "模型响应超时，已保留设定")

        response = streams.generation_response(operation)
        assert response.headers["x-accel-buffering"] == "no"
        iterator = response.body_iterator
        await anext(iterator)
        assert (await anext(iterator)).startswith(":")
        release.set()
        remaining = [event(frame) async for frame in iterator if not frame.startswith(":")]
        assert remaining == [("error", {"message": "模型响应超时，已保留设定", "status": 504})]

    asyncio.run(run())
