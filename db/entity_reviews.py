"""正文新角色/新势力的待审核记录。"""

import json
from datetime import timezone

from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, Text, insert, select, update)
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT

from db.accounts import account_id_type, metadata, utcnow


id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
LONG_TYPE = Text().with_variant(LONGTEXT(), "mysql")
entity_review = Table(
    "app_entity_review", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("account_id", account_id_type, ForeignKey("app_account.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("project_id", id_type, ForeignKey("novel_project.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("chapter_id", id_type, ForeignKey("novel_chapter.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("entity_type", String(16), nullable=False),
    Column("name", String(160), nullable=False),
    Column("details", LONG_TYPE, nullable=False),
    Column("evidence", LONG_TYPE, nullable=False),
    Column("status", String(10), nullable=False, default="pending"),
    Column("existing_id", id_type, nullable=True),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    Column("reviewed_at", DateTime, nullable=True),
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


def save_reviews(engine, account_id: int, project_id: int, items: list[dict]) -> list[int]:
    saved = []
    with engine.begin() as connection:
        existing = set(connection.execute(select(entity_review.c.entity_type, entity_review.c.name).where(
            entity_review.c.project_id == project_id,
            entity_review.c.status.in_(("pending", "approved", "replaced")),
        )).all())
        for item in items:
            entity_type = item["entity_type"]
            name = item["name"][:160]
            if (entity_type, name) in existing:
                continue
            result = connection.execute(insert(entity_review).values(
                account_id=account_id, project_id=project_id, chapter_id=item["chapter_id"],
                entity_type=entity_type, name=name,
                details=json.dumps(item.get("details") or {}, ensure_ascii=False),
                evidence=json.dumps(item.get("evidence") or [], ensure_ascii=False),
                status="pending", created_at=utcnow()))
            saved.append(int(result.inserted_primary_key[0]))
            existing.add((entity_type, name))
    return saved


def list_reviews(engine, account_id: int, project_id: int, status: str = "pending") -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(entity_review).where(
            entity_review.c.account_id == account_id,
            entity_review.c.project_id == project_id,
            entity_review.c.status == status,
        ).order_by(entity_review.c.created_at, entity_review.c.id)).mappings().all()
    return [{
        "id": row["id"], "project_id": row["project_id"], "chapter_id": row["chapter_id"],
        "entity_type": row["entity_type"], "name": row["name"],
        "details": _load(row["details"]) or {}, "evidence": _load(row["evidence"]) or [],
        "status": row["status"], "existing_id": row["existing_id"],
        "created_at": _iso(row["created_at"]),
    } for row in rows]


def get_review(engine, account_id: int, project_id: int, review_id: int):
    with engine.begin() as connection:
        row = connection.execute(select(entity_review).where(
            entity_review.c.id == review_id,
            entity_review.c.account_id == account_id,
            entity_review.c.project_id == project_id,
        )).mappings().first()
    if row is None:
        return None
    return {
        "id": row["id"], "project_id": row["project_id"], "chapter_id": row["chapter_id"],
        "entity_type": row["entity_type"], "name": row["name"],
        "details": _load(row["details"]) or {}, "evidence": _load(row["evidence"]) or [],
        "status": row["status"], "existing_id": row["existing_id"],
    }


def mark_review(engine, account_id: int, project_id: int, review_id: int, status: str,
                existing_id: int | None = None) -> bool:
    with engine.begin() as connection:
        result = connection.execute(update(entity_review).where(
            entity_review.c.id == review_id,
            entity_review.c.account_id == account_id,
            entity_review.c.project_id == project_id,
            entity_review.c.status == "pending",
        ).values(status=status, existing_id=existing_id, reviewed_at=utcnow()))
        return result.rowcount > 0
