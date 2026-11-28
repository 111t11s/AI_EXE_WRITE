"""伏笔档案：预埋、消费与来源章节关联。"""

from datetime import timezone

from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, Text,
                        func, insert, or_, select, update)
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT

from db.accounts import account_id_type, metadata, utcnow


id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
LONG_TYPE = Text().with_variant(LONGTEXT(), "mysql")
CATEGORY_LABELS = {
    "identity": "身份秘密", "treasure": "秘宝奇物", "event": "事件真相",
    "relationship": "关系伏笔", "ability": "能力来历", "world": "世界规则",
    "prophecy": "预言征兆", "debt": "人情仇恨", "location": "地点秘密", "other": "其他",
}


def normalize_category(value: str | None) -> str:
    text = (value or "").strip()
    if text in CATEGORY_LABELS:
        return text
    for key, label in CATEGORY_LABELS.items():
        if text == label:
            return key
    return "other"


foreshadow = Table(
    "novel_foreshadow", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("project_id", id_type, ForeignKey("novel_project.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("title", String(200), nullable=False),
    Column("category", String(32), nullable=False, default="other"),
    Column("status", String(16), nullable=False, default="planned"),
    Column("source", String(16), nullable=False, default="preset"),
    Column("summary", Text, nullable=False, default=""),
    Column("details", LONG_TYPE, nullable=False, default=""),
    Column("planted_outline_id", id_type, ForeignKey("novel_outline.id", ondelete="SET NULL"), nullable=True),
    Column("planted_volume_id", id_type, ForeignKey("novel_outline_volume.id", ondelete="SET NULL"), nullable=True),
    Column("planted_chapter_id", id_type, ForeignKey("novel_chapter.id", ondelete="SET NULL"), nullable=True),
    Column("consumed_outline_id", id_type, ForeignKey("novel_outline.id", ondelete="SET NULL"), nullable=True),
    Column("consumed_volume_id", id_type, ForeignKey("novel_outline_volume.id", ondelete="SET NULL"), nullable=True),
    Column("consumed_chapter_id", id_type, ForeignKey("novel_chapter.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    Column("updated_at", DateTime, nullable=False, default=utcnow, onupdate=utcnow),
)


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _row(row) -> dict:
    return {
        "id": row["id"], "project_id": row["project_id"], "title": row["title"],
        "category": row["category"], "status": row["status"], "source": row["source"],
        "summary": row["summary"], "details": row["details"],
        "planted_outline_id": row["planted_outline_id"], "planted_volume_id": row["planted_volume_id"],
        "planted_chapter_id": row["planted_chapter_id"],
        "consumed_outline_id": row["consumed_outline_id"], "consumed_volume_id": row["consumed_volume_id"],
        "consumed_chapter_id": row["consumed_chapter_id"],
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
    }


def list_page(engine, project_id: int, page: int = 1, page_size: int = 8,
              status: str = "all", category: str = "all"):
    page = max(1, int(page))
    page_size = max(1, min(30, int(page_size)))
    clauses = [foreshadow.c.project_id == project_id]
    if status != "all":
        clauses.append(foreshadow.c.status == status)
    if category != "all":
        clauses.append(foreshadow.c.category == category)
    with engine.begin() as connection:
        total = connection.scalar(select(func.count()).select_from(foreshadow).where(*clauses)) or 0
        rows = connection.execute(select(foreshadow).where(*clauses).order_by(
            foreshadow.c.updated_at.desc(), foreshadow.c.id.desc()
        ).limit(page_size).offset((page - 1) * page_size)).mappings().all()
    return {"items": [_row(row) for row in rows], "total": total, "page": page,
            "page_size": page_size, "has_more": page * page_size < total}


def get(engine, project_id: int, foreshadow_id: int):
    with engine.begin() as connection:
        row = connection.execute(select(foreshadow).where(
            foreshadow.c.id == foreshadow_id, foreshadow.c.project_id == project_id)).mappings().first()
    return _row(row) if row else None


def create(engine, project_id: int, values: dict) -> int:
    with engine.begin() as connection:
        result = connection.execute(insert(foreshadow).values(
            project_id=project_id,
            title=values["title"][:200],
            category=(values.get("category") or "other")[:32],
            status=values.get("status") or "planned",
            source=values.get("source") or "preset",
            summary=(values.get("summary") or "")[:4000],
            details=(values.get("details") or "")[:20000],
            created_at=utcnow(), updated_at=utcnow()))
        return int(result.inserted_primary_key[0])


def available(engine, project_id: int, limit: int = 100):
    with engine.begin() as connection:
        rows = connection.execute(select(foreshadow).where(
            foreshadow.c.project_id == project_id,
            foreshadow.c.status.notin_(("consumed", "abandoned")),
        ).order_by(foreshadow.c.updated_at.desc()).limit(limit)).mappings().all()
    return [_row(row) for row in rows]


def titles(engine, project_id: int) -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(foreshadow.c.title, foreshadow.c.category,
                                         foreshadow.c.status).where(
            foreshadow.c.project_id == project_id)).mappings().all()
    return [dict(row) for row in rows]


def list_for_volume(engine, volume_id: int) -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(foreshadow).where(
            foreshadow.c.planted_volume_id == volume_id,
            foreshadow.c.status.notin_(("consumed", "abandoned")),
        ).order_by(foreshadow.c.id)).mappings().all()
    return [_row(row) for row in rows]


def list_for_chapters(engine, project_id: int, chapter_ids: list[int], volume_ids: list[int]) -> list[dict]:
    clauses = [foreshadow.c.project_id == project_id, foreshadow.c.status.notin_(("abandoned",))]
    targets = []
    if chapter_ids:
        targets.append(foreshadow.c.planted_chapter_id.in_(chapter_ids))
        targets.append(foreshadow.c.consumed_chapter_id.in_(chapter_ids))
    if volume_ids:
        targets.append(foreshadow.c.planted_volume_id.in_(volume_ids))
    if targets:
        clauses.append(or_(*targets))
    with engine.begin() as connection:
        rows = connection.execute(select(foreshadow).where(*clauses).order_by(
            foreshadow.c.id)).mappings().all()
    return [_row(row) for row in rows]


def apply_outline_selection(engine, project_id: int, consume_ids: list[int], plant_ids: list[int],
                            outline_id: int, volume_id: int | None = None):
    ids = sorted({int(value) for value in [*consume_ids, *plant_ids] if int(value) > 0})
    if not ids:
        return
    with engine.begin() as connection:
        rows = connection.execute(select(foreshadow.c.id, foreshadow.c.status).where(
            foreshadow.c.project_id == project_id, foreshadow.c.id.in_(ids))).mappings().all()
        existing = {row["id"]: row["status"] for row in rows}
        for foreshadow_id in sorted({int(value) for value in consume_ids if int(value) > 0}):
            if existing.get(foreshadow_id) in {"consumed", "abandoned"}:
                continue
            connection.execute(update(foreshadow).where(
                foreshadow.c.id == foreshadow_id, foreshadow.c.project_id == project_id
            ).values(status="consumed", consumed_outline_id=outline_id,
                     consumed_volume_id=volume_id, updated_at=utcnow()))
        for foreshadow_id in sorted({int(value) for value in plant_ids if int(value) > 0}):
            if existing.get(foreshadow_id) in {"consumed", "abandoned"}:
                continue
            connection.execute(update(foreshadow).where(
                foreshadow.c.id == foreshadow_id, foreshadow.c.project_id == project_id
            ).values(status="planted", planted_outline_id=outline_id,
                     planted_volume_id=volume_id, updated_at=utcnow()))


def mark_consumed(engine, project_id: int, foreshadow_id: int, outline_id=None,
                  volume_id=None, chapter_id=None) -> bool:
    with engine.begin() as connection:
        result = connection.execute(update(foreshadow).where(
            foreshadow.c.id == foreshadow_id, foreshadow.c.project_id == project_id,
            foreshadow.c.status != "consumed",
        ).values(status="consumed", consumed_outline_id=outline_id, consumed_volume_id=volume_id,
                 consumed_chapter_id=chapter_id, updated_at=utcnow()))
        return result.rowcount > 0


def save_analysis_candidates(engine, project_id: int, chapter: dict, candidates: list[dict]) -> int:
    """章节分析发现的新伏笔；同名未消费伏笔只更新来源章节。"""
    saved = 0
    with engine.begin() as connection:
        existing = {row["title"].strip().casefold(): row for row in connection.execute(select(
            foreshadow.c.id, foreshadow.c.title, foreshadow.c.status).where(
            foreshadow.c.project_id == project_id)).mappings().all()}
        for item in candidates:
            title = (item.get("title") or "").strip()[:200]
            if not title:
                continue
            key = title.casefold()
            if key in existing:
                current = existing[key]
                if current["status"] not in {"consumed", "abandoned"}:
                    connection.execute(update(foreshadow).where(foreshadow.c.id == current["id"]).values(
                        status="planted", planted_outline_id=chapter.get("outline_id"),
                        planted_volume_id=chapter.get("volume_id"),
                        planted_chapter_id=chapter.get("id"), updated_at=utcnow()))
                continue
            connection.execute(insert(foreshadow).values(
                project_id=project_id, title=title, category=(item.get("category") or "other")[:32],
                status="planted", source="analysis", summary=(item.get("summary") or "")[:4000],
                details=(item.get("details") or "")[:20000],
                planted_outline_id=chapter.get("outline_id"), planted_volume_id=chapter.get("volume_id"),
                planted_chapter_id=chapter.get("id"), created_at=utcnow(), updated_at=utcnow()))
            existing[key] = {"id": 0, "title": title, "status": "planted"}
            saved += 1
    return saved
