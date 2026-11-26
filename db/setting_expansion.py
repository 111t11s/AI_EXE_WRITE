"""基于用户已有设定的补全报告持久化。"""

import json
from datetime import timezone

from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, Text, insert, select)
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT

from db.accounts import account_id_type, metadata, utcnow


id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
LONG_TYPE = Text().with_variant(LONGTEXT(), "mysql")
setting_expansion = Table(
    "app_setting_expansion", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("file_names", Text, nullable=False),
    Column("input_chars", Integer, nullable=False, default=0),
    Column("instruction", String(2000), nullable=False, default=""),
    Column("report", LONG_TYPE, nullable=False),
    Column("created_at", DateTime, nullable=False, default=utcnow),
)


def _load(value):
    try:
        return json.loads(value) if value else None
    except (TypeError, ValueError):
        return None


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _summary(row) -> dict:
    report = _load(row["report"]) or {}
    return {
        "id": row["id"], "file_names": _load(row["file_names"]) or [],
        "input_chars": row["input_chars"], "instruction": row["instruction"],
        "title": report.get("title", ""), "world_overview": report.get("world_overview", ""),
        "created_at": _iso(row["created_at"]),
    }


def save_report(engine, account_id: int, file_names: list[str], input_chars: int,
                instruction: str, report: dict) -> int:
    with engine.begin() as connection:
        result = connection.execute(insert(setting_expansion).values(
            account_id=account_id,
            file_names=json.dumps(file_names, ensure_ascii=False),
            input_chars=max(0, int(input_chars)),
            instruction=(instruction or "")[:2000],
            report=json.dumps(report, ensure_ascii=False),
            created_at=utcnow()))
        return int(result.inserted_primary_key[0])


def list_reports(engine, account_id: int, limit: int = 20, offset: int = 0) -> dict:
    with engine.begin() as connection:
        rows = connection.execute(select(setting_expansion).where(
            setting_expansion.c.account_id == account_id
        ).order_by(setting_expansion.c.created_at.desc(), setting_expansion.c.id.desc())
        .limit(limit).offset(offset)).mappings().all()
    return {"items": [_summary(row) for row in rows], "limit": limit, "offset": offset}


def get_report(engine, account_id: int, report_id: int) -> dict | None:
    with engine.begin() as connection:
        row = connection.execute(select(setting_expansion).where(
            setting_expansion.c.id == report_id, setting_expansion.c.account_id == account_id
        )).mappings().first()
    if row is None:
        return None
    report = _load(row["report"])
    if report is None:
        return None
    return {**_summary(row), "report": report}
