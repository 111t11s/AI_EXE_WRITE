"""Account-scoped background generation with replayable progress."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from threading import get_ident

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse

from agents.llm_streaming import GENERATION_EVENTS
from api.account_api import CurrentAccount
from api.generation_stream import HEARTBEAT_SECONDS, encode_event
from core.logging import get_logger


logger = get_logger("generation_jobs")
router = APIRouter(prefix="/generation-jobs", tags=["后台生成任务"])
TERMINAL = {"completed", "failed"}
MAX_TEXT = 250_000


@dataclass
class GenerationJob:
    id: str
    account_id: int
    key: str
    kind: str
    title: str
    project_id: int | None = None
    resource_id: str | int | None = None
    status: str = "running"
    message: str = "任务已发起，正在准备生成…"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    text: str = ""
    thinking: str = ""
    truncated: bool = False
    result: dict | None = None
    error: dict | None = None
    sequence: int = 0
    events: deque = field(default_factory=lambda: deque(maxlen=1000), repr=False)
    listeners: set = field(default_factory=set, repr=False)

    def summary(self):
        return {key: getattr(self, key) for key in (
            "id", "kind", "title", "project_id", "resource_id", "status", "message", "created_at", "finished_at"
        )}

    def detail(self):
        return {**self.summary(), "text": self.text, "thinking": self.thinking,
                "truncated": self.truncated, "result": self.result, "error": self.error}

    def publish(self, event, data):
        if event in {"delta", "thinking"}:
            name = "text" if event == "delta" else "thinking"
            value = getattr(self, name) + data.get("text", "")
            self.truncated |= len(value) > MAX_TEXT
            setattr(self, name, value[-MAX_TEXT:])
        elif event == "status":
            self.message = str(data.get("message", self.message))
        self.sequence += 1
        self.events.append((self.sequence, event, data))
        for listener in self.listeners:
            listener.set()


class JobStore:
    def __init__(self):
        self.jobs: dict[str, GenerationJob] = {}
        self.running: set[asyncio.Task] = set()
        # Keep the asyncio task separately from the public job payload so a
        # project deletion can stop its in-flight generation as well as remove
        # the retained progress record.
        self._tasks: dict[str, asyncio.Task] = {}

    def prune(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for job_id, job in list(self.jobs.items()):
            if job.finished_at and job.finished_at < cutoff and not job.listeners:
                del self.jobs[job_id]

    def owned(self, job_id, account_id):
        job = self.jobs.get(job_id)
        if job is None or job.account_id != account_id:
            raise HTTPException(404, "任务不存在或已过期")
        return job

    def remove_project(self, project_id):
        """Drop all jobs belonging to a project and cancel active work.

        Generation jobs are process-local, so there is no database cascade to
        remove them when a novel is moved to the recycle bin.  Cancelling the
        associated asyncio tasks also prevents a late completion from writing
        newly generated records back into a deleted project.
        """
        removed = 0
        for job_id, job in list(self.jobs.items()):
            if job.project_id != project_id:
                continue
            removed += 1
            task = self._tasks.get(job_id)
            if task is not None and not task.done():
                task.cancel()
            del self.jobs[job_id]
        return removed

    def start(self, *, account_id: int, key: str, kind: str, title: str,
              operation: Callable[[], Awaitable], project_id=None, resource_id=None):
        self.prune()
        active = [job for job in self.jobs.values() if job.account_id == account_id and job.status not in TERMINAL]
        for job in active:
            if job.key == key:
                return job
        if len(active) >= 3:
            raise HTTPException(429, "已有3个任务正在生成，请等待其中一个完成后再发起")
        # Bound retained transcripts as well as active work.
        completed = sorted((job for job in self.jobs.values()
                            if job.account_id == account_id and job.status in TERMINAL and not job.listeners),
                           key=lambda job: job.created_at)
        for old in completed[:-49]:
            del self.jobs[old.id]
        job = GenerationJob(id=uuid4().hex, account_id=account_id, key=key, kind=kind,
                            title=title, project_id=project_id, resource_id=resource_id)
        self.jobs[job.id] = job
        loop = asyncio.get_running_loop()
        owner_thread = get_ident()

        def publish(event, data):
            if get_ident() == owner_thread:
                job.publish(event, data)
            elif not loop.is_closed():
                loop.call_soon_threadsafe(job.publish, event, data)

        async def run():
            token = GENERATION_EVENTS.set(publish)
            try:
                job.result = jsonable_encoder(await operation())
                if kind == "worldbuilding":
                    job.project_id = (job.result.get("persistence") or {}).get("project_id")
                job.status = "completed"
                job.message = job.result.get("warning") or "生成已完成"
                job.publish("result", job.result)
                logger.info("后台生成已完成：任务=%s，类型=%s", job.id, kind)
            except asyncio.CancelledError:
                job.status = "failed"
                job.error = {"message": "服务已停止，请核对保存结果后再试。", "status": 503}
                job.message = job.error["message"]
                job.publish("error", job.error)
                raise
            except Exception as exc:
                job.status = "failed"
                job.error = {"message": exc.detail if isinstance(exc, HTTPException) else "生成过程异常，请核对保存结果后重试。",
                             "status": exc.status_code if isinstance(exc, HTTPException) else 500}
                job.message = job.error["message"]
                job.publish("error", job.error)
                logger.warning("后台生成未完成：任务=%s，异常类型=%s", job.id, type(exc).__name__)
            finally:
                job.finished_at = datetime.now(timezone.utc)
                GENERATION_EVENTS.reset(token)

        # create_task copies the request's account/MCP context before it closes.
        task = asyncio.create_task(run())
        self._tasks[job.id] = task
        self.running.add(task)
        def finished(done):
            self.running.discard(done)
            self._tasks.pop(job.id, None)
        task.add_done_callback(finished)
        return job


store = JobStore()


@router.get("")
async def list_jobs(user: CurrentAccount):
    store.prune()
    jobs = sorted((job for job in store.jobs.values() if job.account_id == user.id),
                  key=lambda job: job.created_at, reverse=True)
    return {"items": [job.summary() for job in jobs]}


@router.get("/{job_id}")
async def get_job(job_id: str, user: CurrentAccount):
    return store.owned(job_id, user.id).detail()


@router.get("/{job_id}/events")
async def watch_job(job_id: str, user: CurrentAccount):
    job = store.owned(job_id, user.id)

    async def stream():
        listener = asyncio.Event()
        job.listeners.add(listener)
        cursor = job.sequence
        try:
            snapshot = job.detail()
            yield encode_event("snapshot", snapshot)
            if snapshot["status"] in TERMINAL:
                yield encode_event("error" if snapshot["error"] else "result", snapshot["error"] or snapshot["result"])
                return
            while True:
                pending = [entry for entry in job.events if entry[0] > cursor]
                if pending:
                    if pending[0][0] > cursor + 1:
                        cursor = job.sequence
                        snapshot = job.detail()
                        yield encode_event("snapshot", snapshot)
                        if snapshot["status"] in TERMINAL:
                            yield encode_event("error" if snapshot["error"] else "result", snapshot["error"] or snapshot["result"])
                            return
                        continue
                    for cursor, event, data in pending:
                        yield encode_event(event, data)
                        if event in {"result", "error"}:
                            return
                    continue
                listener.clear()
                try:
                    await asyncio.wait_for(listener.wait(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": 保持连接\n\n"
        finally:
            job.listeners.discard(listener)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })
