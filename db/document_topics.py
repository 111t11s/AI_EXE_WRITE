"""文件选题分析结果持久化。"""

import json
from datetime import timezone

from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, Text, insert, select)
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT

from db.accounts import account_id_type, metadata, utcnow


id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
LONG_TYPE = Text().with_variant(LONGTEXT(), "mysql")
document_topic = Table(
    "app_document_topic", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("source_type", String(20), nullable=False),
    Column("file_names", Text, nullable=False),
    Column("input_chars", Integer, nullable=False, default=0),
    Column("instruction", String(2000), nullable=False, default=""),
    Column("genre_preference", String(40), nullable=False, default=""),
    Column("audience", String(40), nullable=False, default=""),
    Column("report", LONG_TYPE, nullable=False),
    Column("created_at", DateTime, nullable=False, default=utcnow),
)


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load(value):
    try:
        return json.loads(value) if value else None
    except (TypeError, ValueError):
        return None


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def save_report(engine, account_id: int, source_type: str, file_names: list[str], input_chars: int,
                instruction: str, genre_preference: str, audience: str, report: dict) -> int:
    with engine.begin() as connection:
        result = connection.execute(insert(document_topic).values(
            account_id=account_id, source_type=source_type, file_names=_dump(file_names),
            input_chars=max(0, int(input_chars)), instruction=(instruction or "")[:2000],
            genre_preference=(genre_preference or "")[:40], audience=(audience or "")[:40],
            report=_dump(report), created_at=utcnow()))
        return int(result.inserted_primary_key[0])


def _summary(row) -> dict:
    report = _load(row["report"]) or {}
    return {
        "id": row["id"], "source_type": row["source_type"],
        "file_names": _load(row["file_names"]) or [], "input_chars": row["input_chars"],
        "instruction": row["instruction"], "genre_preference": row["genre_preference"],
        "audience": row["audience"], "overview": report.get("overview", ""),
        "idea_count": len(report.get("ideas") or []),
        "created_at": _iso(row["created_at"]),
    }


def list_reports(engine, account_id: int, limit: int = 20, offset: int = 0) -> dict:
    with engine.begin() as connection:
        rows = connection.execute(select(document_topic).where(
            document_topic.c.account_id == account_id
        ).order_by(document_topic.c.created_at.desc(), document_topic.c.id.desc())
        .limit(limit).offset(offset)).mappings().all()
    return {"items": [_summary(row) for row in rows], "limit": limit, "offset": offset}


def get_report(engine, account_id: int, report_id: int) -> dict | None:
    with engine.begin() as connection:
        row = connection.execute(select(document_topic).where(
            document_topic.c.id == report_id, document_topic.c.account_id == account_id
        )).mappings().first()
    if row is None:
        return None
    report = _load(row["report"])
    if report is None:
        return None
    return {**_summary(row), "report": report}
