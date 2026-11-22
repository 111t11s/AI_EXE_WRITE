"""生成任务持久化：任务中心在服务重启后仍保留记录，由用户手动删除。"""

import json
from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table,
                        Text, delete, insert, select, update)
from sqlalchemy.dialects.mysql import LONGTEXT

from db.accounts import account, account_id_type, metadata, utcnow  # account 为外键目标


LONG_TYPE = Text().with_variant(LONGTEXT(), "mysql")
INTERRUPTED_MESSAGE = "服务已重启，任务已中断，请重新发起"
generation_job = Table(
    "app_generation_job", metadata,
    Column("id", String(32), primary_key=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("job_key", String(200), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("title", String(200), nullable=False),
    Column("project_id", BigInteger, nullable=True),
    Column("resource_id", String(80), nullable=True),
    Column("status", String(20), nullable=False, index=True),
    Column("message", String(500), nullable=False, default=""),
    Column("text", LONG_TYPE, nullable=True),
    Column("thinking", LONG_TYPE, nullable=True),
    Column("truncated", Boolean, nullable=False, default=False),
    Column("result", LONG_TYPE, nullable=True),
    Column("error", LONG_TYPE, nullable=True),
    Column("event_sequence", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    Column("finished_at", DateTime, nullable=True),
)


def _dump(value):
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _load(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _naive(value):
    """MySQL DATETIME 不保存时区；统一存成 UTC 的朴素时间。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def snapshot(job) -> dict:
    """把内存中的任务转成可写入数据库的字段。"""
    return {
        "id": job.id,
        "account_id": job.account_id,
        "job_key": job.key,
        "kind": job.kind,
        "title": job.title,
        "project_id": job.project_id,
        "resource_id": None if job.resource_id is None else str(job.resource_id),
        "status": job.status,
        "message": (job.message or "")[:500],
        "text": job.text or None,
        "thinking": job.thinking or None,
        "truncated": bool(job.truncated),
        "result": _dump(job.result),
        "error": _dump(job.error),
        "event_sequence": job.sequence,
        "created_at": _naive(job.created_at),
        "finished_at": _naive(job.finished_at),
    }


def save_values(engine, values: dict) -> None:
    """按主键写入或更新一条任务快照。"""
    with engine.begin() as connection:
        exists = connection.scalar(select(generation_job.c.id).where(generation_job.c.id == values["id"]))
        if exists:
            connection.execute(update(generation_job).where(generation_job.c.id == values["id"]).values(**values))
        else:
            connection.execute(insert(generation_job).values(**values))


def delete_rows(engine, job_ids) -> int:
    ids = [job_id for job_id in job_ids if job_id]
    if not ids:
        return 0
    with engine.begin() as connection:
        return connection.execute(delete(generation_job).where(generation_job.c.id.in_(ids))).rowcount


def fail_running(engine) -> int:
    """服务重启后，上次未完成的任务统一标记为中断。"""
    with engine.begin() as connection:
        return connection.execute(update(generation_job).where(generation_job.c.status == "running").values(
            status="failed", message=INTERRUPTED_MESSAGE,
            error=_dump({"message": INTERRUPTED_MESSAGE, "status": 503}),
            finished_at=utcnow())).rowcount


def load_rows(engine) -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(generation_job).order_by(
            generation_job.c.created_at.asc(), generation_job.c.id.asc())).mappings().all()
    return [{
        "id": row["id"], "account_id": row["account_id"], "key": row["job_key"], "kind": row["kind"],
        "title": row["title"], "project_id": row["project_id"],
        "resource_id": row["resource_id"], "status": row["status"], "message": row["message"],
        "text": row["text"] or "", "thinking": row["thinking"] or "", "truncated": bool(row["truncated"]),
        "result": _load(row["result"]), "error": _load(row["error"]), "sequence": row["event_sequence"],
        "created_at": _aware(row["created_at"]), "finished_at": _aware(row["finished_at"]),
    } for row in rows]
