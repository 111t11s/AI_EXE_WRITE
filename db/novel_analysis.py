"""小说章节拆解报告持久化。"""

import json
from datetime import timezone

from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, Text, insert, select)
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT

from db.accounts import account_id_type, metadata, utcnow


id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
LONG_TYPE = Text().with_variant(LONGTEXT(), "mysql")
novel_analysis = Table(
    "app_novel_analysis", metadata,
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
    techniques = [item for dimension in report.get("dimensions") or []
                  for item in dimension.get("techniques") or []]
    available = [item for item in report.get("dimensions") or [] if item.get("available")]
    return {
        "id": row["id"], "file_names": _load(row["file_names"]) or [],
        "input_chars": row["input_chars"], "instruction": row["instruction"],
        "overview": report.get("overview", ""), "technique_count": len(techniques),
        "dimension_count": len(available), "created_at": _iso(row["created_at"]),
    }


def save_report(engine, account_id: int, file_names: list[str], input_chars: int,
                instruction: str, report: dict) -> int:
    with engine.begin() as connection:
        result = connection.execute(insert(novel_analysis).values(
            account_id=account_id,
            file_names=json.dumps(file_names, ensure_ascii=False),
            input_chars=max(0, int(input_chars)),
            instruction=(instruction or "")[:2000],
            report=json.dumps(report, ensure_ascii=False),
            created_at=utcnow()))
        return int(result.inserted_primary_key[0])


def list_reports(engine, account_id: int, limit: int = 20, offset: int = 0) -> dict:
    with engine.begin() as connection:
        rows = connection.execute(select(novel_analysis).where(
            novel_analysis.c.account_id == account_id
        ).order_by(novel_analysis.c.created_at.desc(), novel_analysis.c.id.desc())
        .limit(limit).offset(offset)).mappings().all()
    return {"items": [_summary(row) for row in rows], "limit": limit, "offset": offset}


def get_report(engine, account_id: int, report_id: int) -> dict | None:
    with engine.begin() as connection:
        row = connection.execute(select(novel_analysis).where(
            novel_analysis.c.id == report_id, novel_analysis.c.account_id == account_id
        )).mappings().first()
    if row is None:
        return None
    report = _load(row["report"])
    if report is None:
        return None
    return {**_summary(row), "report": report}


def list_techniques(engine, account_id: int, limit: int = 300) -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(novel_analysis).where(
            novel_analysis.c.account_id == account_id
        ).order_by(novel_analysis.c.created_at.desc(), novel_analysis.c.id.desc())).mappings().all()
    techniques = []
    for row in rows:
        report = _load(row["report"]) or {}
        names = _load(row["file_names"]) or []
        for dimension in report.get("dimensions") or []:
            for item in dimension.get("techniques") or []:
                techniques.append({
                    "analysis_id": row["id"], "file_names": names,
                    "dimension": item.get("dimension") or dimension.get("dimension"),
                    "name": item.get("name", ""), "summary": item.get("summary", ""),
                    "mechanism": item.get("mechanism", ""), "reuse": item.get("reuse", ""),
                    "confidence": item.get("confidence", "medium"),
                    "applicable_genres": item.get("applicable_genres") or [],
                    "evidence": item.get("evidence") or [],
                })
                if len(techniques) >= limit:
                    return techniques
    return techniques
