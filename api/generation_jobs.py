"""Account-scoped background generation with replayable progress."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
from threading import get_ident

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from agents.llm_streaming import GENERATION_EVENTS
from api.account_api import CurrentAccount
from api.generation_stream import HEARTBEAT_SECONDS, encode_event
from core.app_logging import get_logger
from db import generation_jobs as job_db


logger = get_logger("generation_jobs")
router = APIRouter(prefix="/generation-jobs", tags=["后台生成任务"])
TERMINAL = {"completed", "failed"}
MAX_TEXT = 250_000
# 与前端任务分类保持一致：把细分任务类型归到页面上的分类。
CATEGORY_KINDS = {"worldbuilding", "outline", "characters", "draft", "plan", "review", "consistency", "topics", "trends"}
KIND_CATEGORY = {"character_progress": "characters", "batch_draft": "draft", "consistency_repair": "consistency",
                 "trends": "topics"}
STATUSES = {"all", "running", "completed", "failed"}


def _database_error_summary(exc: Exception) -> str:
    """Return a short driver error code/message without logging SQL parameters."""
    original = getattr(exc, "orig", None)
    args = getattr(original, "args", ())
    if args:
        code = args[0]
        detail = str(args[1] if len(args) > 1 else args[0]).replace("\n", " ")
        return f"{code}: {detail[:240]}"
    return type(exc).__name__


def task_category(kind: str) -> str:
    category = KIND_CATEGORY.get(kind, kind)
    return category if category in CATEGORY_KINDS else "other"


def project_key(project_id) -> str:
    return "unassigned" if project_id is None else str(project_id)


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
        # 任务写入数据库时不在事件循环里做阻塞 IO：统一交给这个队列线程处理。
        self.engine_factory = None
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None

    def bind(self, engine_factory) -> bool:
        """启动时恢复历史任务，并把上次未完成的任务标记为中断。"""
        self.engine_factory = engine_factory
        engine = self._engine()
        if engine is None:
            logger.warning("任务持久化不可用：数据库未配置，任务只在本次运行内保留")
            return False
        try:
            interrupted = job_db.fail_running(engine)
            rows = job_db.load_rows(engine)
        except Exception as exc:
            logger.warning("任务持久化不可用：读取失败，数据库错误=%s", _database_error_summary(exc))
            return False
        for row in rows:
            job = self._restore(row)
            self.jobs[job.id] = job
        if interrupted:
            logger.info("已把 %s 个重启前未完成的任务标记为中断", interrupted)
        logger.info("已恢复 %s 个历史任务", len(rows))
        return True

    @staticmethod
    def _restore(row: dict) -> GenerationJob:
        resource_id = row["resource_id"]
        if isinstance(resource_id, str) and resource_id.isdigit():
            # 与运行中的任务保持一致：章节/分卷等数字 ID 用整数，会话 ID 保持字符串。
            resource_id = int(resource_id)
        return GenerationJob(
            id=row["id"], account_id=row["account_id"], key=row["key"], kind=row["kind"],
            title=row["title"], project_id=row["project_id"], resource_id=resource_id,
            status=row["status"], message=row["message"], created_at=row["created_at"],
            finished_at=row["finished_at"], text=row["text"], thinking=row["thinking"],
            truncated=row["truncated"], result=row["result"], error=row["error"], sequence=row["sequence"],
        )

    def _engine(self):
        if self.engine_factory is None:
            return None
        try:
            return self.engine_factory()
        except Exception:
            return None

    def _persist(self, action) -> None:
        engine = self._engine()
        if engine is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 线程池里的同步路由（例如移入回收站）没有事件循环，直接写入即可。
            try:
                action(engine)
            except Exception as exc:
                logger.warning("任务持久化写入失败：数据库错误=%s", _database_error_summary(exc))
            return
        if self._queue is None:
            self._queue = asyncio.Queue()
            self._worker = loop.create_task(self._drain())
        self._queue.put_nowait((engine, action))

    async def _drain(self) -> None:
        while True:
            engine, action = await self._queue.get()
            try:
                await asyncio.to_thread(action, engine)
            except Exception as exc:
                logger.warning("任务持久化写入失败：数据库错误=%s", _database_error_summary(exc))
            finally:
                self._queue.task_done()

    async def flush(self, timeout: float = 5) -> None:
        if self._queue is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except asyncio.TimeoutError:
            logger.warning("任务持久化仍有未完成的写入")

    def delete(self, job_ids, account_id) -> list[str]:
        """删除已完成/未完成的任务记录；生成中的任务需先完成。"""
        removed = []
        for job_id in job_ids:
            job = self.jobs.get(job_id)
            if job is None or job.account_id != account_id or job.status not in TERMINAL:
                continue
            del self.jobs[job_id]
            self._tasks.pop(job_id, None)
            removed.append(job_id)
        if removed:
            self._persist(lambda engine: job_db.delete_rows(engine, removed))
        return removed

    def page(self, account_id, *, category="all", project_id="all", status="all", limit=50, offset=0):
        """按分类/项目/状态筛选并分页，返回当前页与各层筛选计数。"""
        jobs = [job for job in self.jobs.values() if job.account_id == account_id]

        def in_category(job):
            return category == "all" or task_category(job.kind) == category

        def in_project(job, wanted=None):
            wanted = project_id if wanted is None else wanted
            return wanted == "all" or project_key(job.project_id) == str(wanted)

        category_counts = {"all": len(jobs)}
        category_running = {"all": 0}
        for job in jobs:
            key = task_category(job.kind)
            category_counts[key] = category_counts.get(key, 0) + 1
            if job.status == "running":
                category_running["all"] += 1
                category_running[key] = category_running.get(key, 0) + 1

        project_counts = {"all": 0}
        for job in jobs:
            if not in_category(job):
                continue
            project_counts["all"] += 1
            key = project_key(job.project_id)
            project_counts[key] = project_counts.get(key, 0) + 1

        status_counts = {"all": 0, "running": 0, "completed": 0, "failed": 0}
        for job in jobs:
            if not in_category(job) or not in_project(job):
                continue
            status_counts["all"] += 1
            status_counts[job.status] = status_counts.get(job.status, 0) + 1

        filtered = [job for job in jobs if in_category(job) and in_project(job)
                    and (status == "all" or job.status == status)]
        # 与页面一致：生成中的任务优先，其余按发起时间倒序。
        filtered.sort(key=lambda job: (0 if job.status == "running" else 1, -job.created_at.timestamp(), job.id))
        total = len(filtered)
        window = filtered[offset:offset + limit]
        projects = []
        for key, count in project_counts.items():
            if key == "all":
                continue
            projects.append({"project_id": int(key) if key.isdigit() else None, "count": count})
        return {
            "items": [job.summary() for job in window],
            "limit": limit, "offset": offset, "total": total, "has_more": offset + limit < total,
            "running": category_running["all"],
            "completed": sum(1 for job in jobs if job.status == "completed"),
            "counts": {"category": category_counts, "category_running": category_running,
                       "project": project_counts, "status": status_counts},
            "projects": projects,
        }

    def owned(self, job_id, account_id):
        job = self.jobs.get(job_id)
        if job is None or job.account_id != account_id:
            raise HTTPException(404, "任务不存在或已过期")
        return job

    def remove_project(self, project_id):
        """Drop all jobs belonging to a project and cancel active work.

        The jobs are also removed from the persisted history when a novel is
        moved to the recycle bin; cancelling the asyncio task prevents a late
        completion from writing new records into a deleted project.
        """
        removed_ids = []
        for job_id, job in list(self.jobs.items()):
            if job.project_id != project_id:
                continue
            task = self._tasks.get(job_id)
            if task is not None and not task.done():
                task.cancel()
            del self.jobs[job_id]
            removed_ids.append(job_id)
        if removed_ids:
            self._persist(lambda engine: job_db.delete_rows(engine, removed_ids))
        return len(removed_ids)

    def start(self, *, account_id: int, key: str, kind: str, title: str,
              operation: Callable[[], Awaitable], project_id=None, resource_id=None):
        active = [job for job in self.jobs.values() if job.account_id == account_id and job.status not in TERMINAL]
        for job in active:
            if job.key == key:
                return job
        if len(active) >= 3:
            raise HTTPException(429, "已有3个任务正在生成，请等待其中一个完成后再发起")
        job = GenerationJob(id=uuid4().hex, account_id=account_id, key=key, kind=kind,
                            title=title, project_id=project_id, resource_id=resource_id)
        self.jobs[job.id] = job
        initial = job_db.snapshot(job)
        self._persist(lambda engine: job_db.save_values(engine, initial))
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
                final = job_db.snapshot(job)
                self._persist(lambda engine: job_db.save_values(engine, final))
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
async def list_jobs(user: CurrentAccount, category: str = "all", project_id: str = "all", status: str = "all",
                    limit: int = Query(default=50, ge=1, le=100),
                    offset: int = Query(default=0, ge=0, le=2**31 - 1)):
    if category != "all" and category != "other" and category not in CATEGORY_KINDS:
        raise HTTPException(422, "未知的任务分类")
    if status not in STATUSES:
        raise HTTPException(422, "未知的任务状态")
    if project_id not in {"all", "unassigned"} and not project_id.isdigit():
        raise HTTPException(422, "项目筛选参数无效")
    return store.page(user.id, category=category, project_id=project_id, status=status,
                      limit=limit, offset=offset)


class DeleteJobsRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: str, user: CurrentAccount):
    job = store.jobs.get(job_id)
    if job is None or job.account_id != user.id:
        raise HTTPException(404, "任务不存在或已删除")
    if job.status not in TERMINAL:
        raise HTTPException(409, "任务仍在生成中，完成后才能删除")
    store.delete([job_id], user.id)
    return Response(status_code=204)


@router.post("/delete")
def delete_jobs(payload: DeleteJobsRequest, user: CurrentAccount) -> dict:
    removed = store.delete(payload.ids, user.id)
    return {"deleted": len(removed), "job_ids": removed}


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
