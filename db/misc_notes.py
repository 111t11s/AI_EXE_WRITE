"""杂记：关键事件、独特物品、灵兽、路人、功法等长期记录。"""

from datetime import timezone

from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, Text,
                        UniqueConstraint, func, insert, select, update)
from sqlalchemy.dialects.mysql import BIGINT, LONGTEXT

from db.accounts import metadata, utcnow


id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
LONG_TYPE = Text().with_variant(LONGTEXT(), "mysql")
misc_note = Table(
    "novel_misc_note", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("project_id", id_type, ForeignKey("novel_project.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("note_type", String(32), nullable=False),
    Column("title", String(200), nullable=False),
    Column("summary", Text, nullable=False, default=""),
    Column("details", LONG_TYPE, nullable=False, default=""),
    Column("source", String(16), nullable=False, default="analysis"),
    Column("first_chapter_id", id_type, ForeignKey("novel_chapter.id", ondelete="SET NULL"), nullable=True),
    Column("last_chapter_id", id_type, ForeignKey("novel_chapter.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", DateTime, nullable=False, default=utcnow),
    Column("updated_at", DateTime, nullable=False, default=utcnow, onupdate=utcnow),
    UniqueConstraint("project_id", "note_type", "title", name="uk_misc_note_project_type_title"),
)


def _iso(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _row(row) -> dict:
    return {
        "id": row["id"], "project_id": row["project_id"], "note_type": row["note_type"],
        "title": row["title"], "summary": row["summary"], "details": row["details"],
        "source": row["source"], "first_chapter_id": row["first_chapter_id"],
        "last_chapter_id": row["last_chapter_id"],
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
    }


def list_page(engine, project_id: int, page: int = 1, page_size: int = 8, note_type: str = "all"):
    page = max(1, int(page))
    page_size = max(1, min(30, int(page_size)))
    clauses = [misc_note.c.project_id == project_id]
    if note_type != "all":
        clauses.append(misc_note.c.note_type == note_type)
    with engine.begin() as connection:
        total = connection.scalar(select(func.count()).select_from(misc_note).where(*clauses)) or 0
        rows = connection.execute(select(misc_note).where(*clauses).order_by(
            misc_note.c.updated_at.desc(), misc_note.c.id.desc()
        ).limit(page_size).offset((page - 1) * page_size)).mappings().all()
    return {"items": [_row(row) for row in rows], "total": total, "page": page,
            "page_size": page_size, "has_more": page * page_size < total}


def get(engine, project_id: int, note_id: int):
    with engine.begin() as connection:
        row = connection.execute(select(misc_note).where(
            misc_note.c.id == note_id, misc_note.c.project_id == project_id)).mappings().first()
    return _row(row) if row else None


def create(engine, project_id: int, values: dict) -> int:
    with engine.begin() as connection:
        result = connection.execute(insert(misc_note).values(
            project_id=project_id,
            note_type=(values.get("note_type") or "event")[:32],
            title=values["title"][:200],
            summary=(values.get("summary") or "")[:4000],
            details=(values.get("details") or "")[:20000],
            source=values.get("source") or "manual",
            created_at=utcnow(), updated_at=utcnow()))
        return int(result.inserted_primary_key[0])


def titles(engine, project_id: int) -> list[dict]:
    with engine.begin() as connection:
        rows = connection.execute(select(misc_note.c.note_type, misc_note.c.title).where(
            misc_note.c.project_id == project_id)).mappings().all()
    return [dict(row) for row in rows]


def save_analysis_candidates(engine, project_id: int, chapter: dict, candidates: list[dict]) -> int:
    saved = 0
    chapter_id = chapter.get("id")
    with engine.begin() as connection:
        rows = connection.execute(select(misc_note).where(
            misc_note.c.project_id == project_id)).mappings().all()
        existing = {(row["note_type"], row["title"].strip().casefold()): row for row in rows}
        for item in candidates:
            name = (item.get("title") or "").strip()[:200]
            note_type = (item.get("note_type") or "event")[:32]
            if not name:
                continue
            key = (note_type, name.casefold())
            if key in existing:
                current = existing[key]
                values = {"last_chapter_id": chapter_id, "updated_at": utcnow()}
                if item.get("summary"):
                    values["summary"] = item["summary"][:4000]
                if item.get("details"):
                    values["details"] = item["details"][:20000]
                connection.execute(update(misc_note).where(misc_note.c.id == current["id"]).values(**values))
                continue
            connection.execute(insert(misc_note).values(
                project_id=project_id, note_type=note_type, title=name,
                summary=(item.get("summary") or "")[:4000],
                details=(item.get("details") or "")[:20000], source="analysis",
                first_chapter_id=chapter_id, last_chapter_id=chapter_id,
                created_at=utcnow(), updated_at=utcnow()))
            existing[key] = {"id": 0, "note_type": note_type, "title": name}
            saved += 1
    return saved
