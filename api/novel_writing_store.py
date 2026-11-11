"""Transactional saves for artifacts produced by the writing agents."""

from fastapi import HTTPException
from sqlalchemy import func, insert, select, update

from api.novel_crud_api import get_row, storage_values, tables, transaction
from api.writing_schemas import ChapterPlanDraft, DraftReceipt, OutlineDraft, OutlineReceipt, ChapterPlanReceipt
from db.connection import legacy_json_columns


def insert_row(connection, relation, **values):
    values = storage_values(values, legacy_json_columns(connection, relation.name))
    return connection.execute(insert(relation).values(**values)).lastrowid


def save_outline(engine, project_id: int, artifact: OutlineDraft) -> OutlineReceipt:
    project, outline = tables["novel_project"], tables["novel_outline"]
    volume, beat = tables["novel_outline_volume"], tables["novel_plot_beat"]
    with transaction(engine) as connection:
        get_row(connection, project, project_id, lock=True)
        version = (connection.scalar(select(func.max(outline.c.version_no)).where(outline.c.project_id == project_id)) or 0) + 1
        outline_id = insert_row(connection, outline, project_id=project_id, version_no=version, status="draft",
                                **artifact.model_dump(exclude={"volumes"}))
        volume_ids = []
        beat_count = 0
        for number, planned in enumerate(artifact.volumes, 1):
            volume_id = insert_row(connection, volume, outline_id=outline_id, volume_number=number,
                                   **planned.model_dump(exclude={"beats"}))
            volume_ids.append(volume_id)
            for order, item in enumerate(planned.beats, 1):
                insert_row(connection, beat, volume_id=volume_id, sort_order=order, **item.model_dump())
                beat_count += 1
        return OutlineReceipt(project_id=project_id, outline_id=outline_id, version_no=version,
                              volume_ids=volume_ids, plot_beat_count=beat_count)


def save_chapter_plan(engine, project_id: int, volume_id: int, start: int,
                      artifact: ChapterPlanDraft) -> ChapterPlanReceipt:
    project, chapter = tables["novel_project"], tables["novel_chapter"]
    with transaction(engine) as connection:
        get_row(connection, project, project_id, lock=True)
        get_row(connection, tables["novel_outline_volume"], volume_id)
        taken = connection.scalar(select(chapter.c.id).where(
            chapter.c.project_id == project_id, chapter.c.chapter_number.between(start, start + len(artifact.chapters) - 1),
        ).limit(1))
        if taken is not None:
            raise HTTPException(409, "规划范围内已有章节，请选择未使用的章号")
        entries = []
        for number, item in enumerate(artifact.chapters, start):
            chapter_id = insert_row(connection, chapter, project_id=project_id, volume_id=volume_id,
                                    chapter_number=number, status="planned", content="", word_count=0,
                                    **item.model_dump())
            entries.append({"chapter_id": chapter_id, "chapter_number": number, "title": item.title})
        return ChapterPlanReceipt(project_id=project_id, volume_id=volume_id, chapters=entries)


def require_empty_plan(chapter):
    if chapter["status"] != "planned" or chapter["content"].strip():
        raise HTTPException(409, "只能给尚无正文的计划章节生成初稿")


def save_draft(engine, chapter_id: int, content: str, original: dict) -> DraftReceipt:
    chapter = tables["novel_chapter"]
    count = sum(not character.isspace() for character in content)
    with transaction(engine) as connection:
        current = get_row(connection, chapter, chapter_id, lock=True)
        require_empty_plan(current)
        if any(current[key] != original[key] for key in ("chapter_number", "title", "summary")):
            raise HTTPException(409, "生成期间章节计划发生变化，请重新生成")
        connection.execute(update(chapter).where(chapter.c.id == chapter_id).values(
            content=content, word_count=count, status="draft",
        ))
        return DraftReceipt(chapter_id=chapter_id, word_count=count)
