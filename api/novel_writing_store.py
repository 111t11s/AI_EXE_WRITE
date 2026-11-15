"""Transactional saves for artifacts produced by the writing agents."""

from fastapi import HTTPException
from sqlalchemy import func, insert, select, update
from collections import Counter
from unicodedata import normalize

from api.novel_crud_api import get_row, response_row, storage_values, tables, transaction
from api.writing_schemas import ChapterPlanDraft, DraftReceipt, OutlineDraft, OutlineReceipt, ChapterPlanReceipt
from db.connection import legacy_json_columns
from api.writing_schemas import CharacterReceipt


CHARACTER_LABELS = {"protagonist": "主角", "important": "重要配角", "supporting": "配角"}


def character_name_key(name):
    return normalize("NFKC", name).strip().casefold()


def save_characters(engine, project_id, artifact, sources, volume_id=None, legacy_assignments=()):
    """Save new people and per-volume scenes atomically; keep existing profiles."""
    relation = tables["novel_character"]
    with transaction(engine) as connection:
        get_row(connection, tables["novel_project"], project_id, lock=True)
        current = tables["novel_world"]
        world_ids = list(connection.scalars(select(current.c.id).where(
            current.c.project_id == project_id, current.c.is_current.is_(True))))
        if world_ids != [sources[0][1]["id"]]:
            raise HTTPException(409, "生成期间当前世界观发生变化，请重新生成角色")
        for table_name, source in sources:
            saved = response_row(get_row(connection, tables[table_name], source["id"], lock=True),
                                 legacy_json_columns(connection, table_name))
            if any(saved.get(key) != value for key, value in source.items() if key not in {"created_at", "updated_at"}):
                raise HTTPException(409, "生成期间世界观或大纲资料发生变化，请重新生成角色")
        names = {character_name_key(name) for name in connection.scalars(
            select(relation.c.name).where(relation.c.project_id == project_id))}
        new_names = [character_name_key(item.name) for item in artifact.characters]
        if len(set(new_names)) != len(new_names):
            raise HTTPException(502, "模型返回了重复角色姓名，请重新生成")
        if names.intersection(new_names):
            raise HTTPException(409, "生成角色与已有角色同名，本批次未保存，请调整要求后重试")
        ids = []
        assignments = []
        for item in artifact.characters:
            values = item.model_dump(exclude={"category", "role"})
            ids.append(insert_row(connection, relation, project_id=project_id, faction_id=None,
                                  role=f"{CHARACTER_LABELS[item.category]} · {item.role}",
                                  first_appearance_chapter=None, status="active", **values))
            if volume_id is not None:
                assignments.append((ids[-1], item.summary))
        assignments.extend((item.character_id, item.scenes) for item in artifact.reused_characters)
        cast = tables["novel_volume_character"]
        # Materialize explicitly matched legacy history on first reuse, so the
        # original volume remains part of this character's history in later volumes.
        for previous_id, character_id, scenes in legacy_assignments:
            existing = connection.scalar(select(cast.c.id).where(
                cast.c.volume_id == previous_id, cast.c.character_id == character_id))
            if existing is None:
                insert_row(connection, cast, volume_id=previous_id, character_id=character_id, scenes=scenes)
        for character_id, scenes in assignments:
            existing = connection.scalar(select(cast.c.id).where(
                cast.c.volume_id == volume_id, cast.c.character_id == character_id))
            if existing is None:
                insert_row(connection, cast, volume_id=volume_id, character_id=character_id, scenes=scenes)
            else:
                connection.execute(update(cast).where(cast.c.id == existing).values(scenes=scenes))
        return CharacterReceipt(project_id=project_id, character_ids=ids,
                                counts=dict(Counter(item.category for item in artifact.characters)), volume_id=volume_id,
                                reused_character_ids=[item.character_id for item in artifact.reused_characters])


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
