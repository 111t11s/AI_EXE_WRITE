"""Transactional saves for artifacts produced by the writing agents."""

from fastapi import HTTPException
from sqlalchemy import func, insert, select, update
from collections import Counter
from unicodedata import normalize

from api.novel_crud_api import get_row, response_row, storage_values, tables, transaction
from api.writing_schemas import (ChapterPlanDraft, DraftReceipt, OutlineDraft, OutlineReceipt, ChapterPlanReceipt,
                                 CharacterProgressReceipt)
from db.connection import legacy_json_columns
from db.accounts import utcnow
from db.foreshadows import foreshadow as foreshadow_table
from db.foreshadows import normalize_category
from db.chapter_characters import chapter_character
from api.writing_schemas import CharacterReceipt
from api.character_profiles import format_profile
from api.character_progress import replace_progress


CHARACTER_LABELS = {"protagonist": "主角", "important": "重要配角", "supporting": "配角"}


def character_name_key(name):
    return normalize("NFKC", name).strip().casefold()


def save_characters(engine, project_id, artifact, sources, volume_id=None, legacy_assignments=()):
    """Save new people and per-volume scenes atomically; keep existing profiles."""
    relation = tables["novel_character"]
    with transaction(engine) as connection:
        get_row(connection, tables["novel_project"], project_id, lock=True)
        if any(item.category == "protagonist" for item in artifact.characters):
            existing_lead = connection.scalar(select(relation.c.id).where(
                relation.c.project_id == project_id, relation.c.role.like("主角%")).limit(1))
            if existing_lead is not None:
                raise HTTPException(409, "男女主已生成；后续只能根据大纲添加配角")
        current = tables["novel_world"]
        world_ids = list(connection.scalars(select(current.c.id).where(
            current.c.project_id == project_id, current.c.is_current.is_(True))))
        if world_ids != [sources[0][1]["id"]]:
            raise HTTPException(409, "生成期间当前世界观发生变化，请重新生成角色")
        for table_name, source in sources:
            saved = response_row(get_row(connection, tables[table_name], source["id"], lock=True),
                                 legacy_json_columns(connection, table_name))
            if any(saved.get(key) != value for key, value in source.items() if key not in {"created_at", "updated_at"}):
                raise HTTPException(409, "生成期间世界观、大纲、角色或正文进度发生变化，请重新生成")
        names = {character_name_key(name) for name in connection.scalars(
            select(relation.c.name).where(relation.c.project_id == project_id))}
        new_names = [character_name_key(item.name) for item in artifact.characters]
        if len(set(new_names)) != len(new_names):
            raise HTTPException(502, "模型返回了重复角色姓名，请重新生成")
        if names.intersection(new_names):
            raise HTTPException(409, "生成角色与已有角色同名，本批次未保存，请调整要求后重试")
        faction_table = tables["novel_faction"]
        faction_ids = {
            character_name_key(source["name"]): source["id"]
            for table_name, source in sources if table_name == "novel_faction"
        }
        for faction in artifact.new_factions:
            faction_key = character_name_key(faction.name)
            if faction_key not in faction_ids:
                faction_ids[faction_key] = insert_row(connection, faction_table,
                    world_id=sources[0][1]["id"], **faction.model_dump())
        # Existing character names resolve relationship targets; newly saved
        # characters are added to the same map as their IDs are allocated.
        character_ids_by_name = {
            character_name_key(source["name"]): source["id"]
            for table_name, source in sources if table_name == "novel_character"
        }
        ids = []
        assignments = []
        for item in artifact.characters:
            faction_id = faction_ids.get(character_name_key(item.faction_name))
            if faction_id is None:
                raise HTTPException(502, "角色所属势力未能匹配到已保存势力，本批次未保存")
            values = item.model_dump(exclude={"category", "gender", "role", "volume_profiles",
                                              "faction_name", "relationships"})
            role = item.role
            if item.category == "protagonist":
                lead_label = "男主" if item.gender == "男" else "女主"
                role = f"{lead_label} · {role}"
            character_id = insert_row(connection, relation, project_id=project_id, faction_id=faction_id,
                                  role=f"{CHARACTER_LABELS[item.category]} · {role}",
                                  first_appearance_chapter=None, status="active", **values)
            ids.append(character_id)
            character_ids_by_name[character_name_key(item.name)] = character_id
            assignments.extend((profile.volume_id, ids[-1], format_profile(profile, sources)) for profile in item.volume_profiles)
            if volume_id is not None and not item.volume_profiles:
                assignments.append((volume_id, ids[-1], item.summary))
        relationship_table = tables["novel_character_relationship"]
        saved_relationships = set()
        for item in artifact.characters:
            source_id = character_ids_by_name[character_name_key(item.name)]
            for relationship in item.relationships:
                target_id = character_ids_by_name.get(character_name_key(relationship.target_name))
                if target_id is None or source_id == target_id:
                    continue
                edge_key = (source_id, target_id, relationship.relationship_type)
                if edge_key in saved_relationships:
                    continue
                insert_row(connection, relationship_table, source_character_id=source_id,
                    target_character_id=target_id, relationship_type=relationship.relationship_type,
                    description=relationship.description, changed_at_chapter=None)
                saved_relationships.add(edge_key)
        for item in artifact.reused_characters:
            assignments.extend((profile.volume_id, item.character_id, format_profile(profile, sources)) for profile in item.volume_profiles)
            if volume_id is not None and not item.volume_profiles:
                assignments.append((volume_id, item.character_id, item.scenes))
        cast = tables["novel_volume_character"]
        # Materialize explicitly matched legacy history on first reuse, so the
        # original volume remains part of this character's history in later volumes.
        for previous_id, character_id, scenes in legacy_assignments:
            existing = connection.scalar(select(cast.c.id).where(
                cast.c.volume_id == previous_id, cast.c.character_id == character_id))
            if existing is None:
                insert_row(connection, cast, volume_id=previous_id, character_id=character_id, scenes=scenes)
        for assignment_volume_id, character_id, scenes in assignments:
            existing = connection.scalar(select(cast.c.id).where(
                cast.c.volume_id == assignment_volume_id, cast.c.character_id == character_id))
            if existing is None:
                insert_row(connection, cast, volume_id=assignment_volume_id, character_id=character_id, scenes=scenes)
            else:
                connection.execute(update(cast).where(cast.c.id == existing).values(scenes=scenes))
        return CharacterReceipt(project_id=project_id, character_ids=ids,
                                outline_id=next((row["id"] for name, row in sources if name == "novel_outline"), None),
                                counts=dict(Counter(item.category for item in artifact.characters)), volume_id=volume_id,
                                reused_character_ids=[item.character_id for item in artifact.reused_characters],
                                profile_character_ids=list(dict.fromkeys(character_id for _, character_id, _ in assignments)))


def insert_row(connection, relation, **values):
    values = storage_values(values, legacy_json_columns(connection, relation.name))
    return connection.execute(insert(relation).values(**values)).lastrowid


def save_outline(engine, project_id: int, artifact: OutlineDraft, sources=(), volume_number=None,
                 settings: dict | None = None) -> OutlineReceipt:
    project, outline = tables["novel_project"], tables["novel_outline"]
    volume, beat = tables["novel_outline_volume"], tables["novel_plot_beat"]
    settings = settings or {}
    with transaction(engine) as connection:
        get_row(connection, project, project_id, lock=True)
        for table_name, source in sources:
            saved = response_row(get_row(connection, tables[table_name], source["id"], lock=True),
                                 legacy_json_columns(connection, table_name))
            if any(saved.get(key) != value for key, value in source.items() if key not in {"created_at", "updated_at"}):
                raise HTTPException(409, "重写期间原大纲或设定发生变化，请重新重写")
        version = (connection.scalar(select(func.max(outline.c.version_no)).where(outline.c.project_id == project_id)) or 0) + 1
        outline_id = insert_row(connection, outline, project_id=project_id, version_no=version, status="draft",
                                **artifact.model_dump(exclude={"volumes"}))
        volume_ids = []
        beat_count = 0
        foreshadow_count = 0
        for number, planned in enumerate(artifact.volumes, 1):
            assigned_number = int(volume_number) + number - 1 if volume_number is not None else number
            volume_id = insert_row(
                connection, volume, outline_id=outline_id, volume_number=assigned_number,
                **planned.model_dump(exclude={"beats", "foreshadows"}),
                min_foreshadows=int(settings.get("min_foreshadows") or 0),
                foreshadow_categories=settings.get("foreshadow_categories") or [],
                target_word_min=int(settings.get("target_word_min") or 0),
                target_word_max=int(settings.get("target_word_max") or 0),
                plot_progression=settings.get("plot_progression") or "")
            volume_ids.append(volume_id)
            for order, item in enumerate(planned.beats, 1):
                insert_row(connection, beat, volume_id=volume_id, sort_order=order, **item.model_dump())
                beat_count += 1
            for item in planned.foreshadows:
                insert_row(connection, foreshadow_table, project_id=project_id,
                           title=item.title.strip()[:200], category=normalize_category(item.category)[:32],
                           status="planned", source="outline", summary=item.summary[:4000],
                           details=item.details[:20000], planted_outline_id=outline_id,
                           planted_volume_id=volume_id)
                foreshadow_count += 1
        return OutlineReceipt(project_id=project_id, outline_id=outline_id, version_no=version,
                              volume_ids=volume_ids, plot_beat_count=beat_count,
                              foreshadow_count=foreshadow_count)


def next_volume_number(engine, project_id: int) -> int:
    outline, volume = tables["novel_outline"], tables["novel_outline_volume"]
    with transaction(engine) as connection:
        current = connection.scalar(select(func.max(volume.c.volume_number)).join(
            outline, volume.c.outline_id == outline.c.id).where(outline.c.project_id == project_id))
    return int(current or 0) + 1


def outline_volume_number(engine, outline_id: int) -> int:
    volume = tables["novel_outline_volume"]
    with transaction(engine) as connection:
        current = connection.scalar(select(func.min(volume.c.volume_number)).where(
            volume.c.outline_id == outline_id))
    return int(current or 1)


def save_chapter_plan(engine, project_id: int, volume_id: int, start: int,
                      artifact: ChapterPlanDraft) -> ChapterPlanReceipt:
    project, chapter = tables["novel_project"], tables["novel_chapter"]
    with transaction(engine) as connection:
        get_row(connection, project, project_id, lock=True)
        volume = get_row(connection, tables["novel_outline_volume"], volume_id)
        cast_ids = {int(value) for value in connection.scalars(select(
            tables["novel_volume_character"].c.character_id).where(
            tables["novel_volume_character"].c.volume_id == volume_id))}
        foreshadows = {row["id"]: row for row in connection.execute(select(foreshadow_table).where(
            foreshadow_table.c.project_id == project_id,
            foreshadow_table.c.planted_volume_id == volume_id)).mappings()}
        assigned_ids = set()
        new_count = 0
        for item in artifact.chapters:
            if any(int(character_id) not in cast_ids for character_id in item.character_ids):
                raise HTTPException(502, "章节规划引用了不属于本卷角色戏份的角色")
            for foreshadow_id in item.foreshadow_ids:
                if int(foreshadow_id) not in foreshadows:
                    raise HTTPException(502, f"章节规划引用了不属于本卷的伏笔 {foreshadow_id}")
                assigned_ids.add(int(foreshadow_id))
            new_count += len(item.new_foreshadows)
        minimum = int(volume.get("min_foreshadows") or 0)
        if minimum and len(assigned_ids) + new_count < minimum:
            raise HTTPException(502, f"本卷至少需要安排 {minimum} 条伏笔，规划只安排 {len(assigned_ids) + new_count} 条，结果未保存")
        taken = connection.scalar(select(chapter.c.id).where(
            chapter.c.project_id == project_id, chapter.c.chapter_number.between(start, start + len(artifact.chapters) - 1),
        ).limit(1))
        if taken is not None:
            highest = connection.scalar(select(func.max(chapter.c.chapter_number)).where(
                chapter.c.project_id == project_id))
            raise HTTPException(409, f"规划范围内已有章节，请选择未使用的章号；本项目下一个可用章号是 {int(highest or 0) + 1}")
        entries = []
        saved_foreshadows = 0
        for number, item in enumerate(artifact.chapters, start):
            chapter_id = insert_row(
                connection, chapter, project_id=project_id, volume_id=volume_id,
                chapter_number=number, status="planned", content="", word_count=0,
                **item.model_dump(exclude={"foreshadow_ids", "new_foreshadows", "character_ids"}))
            entries.append({"chapter_id": chapter_id, "chapter_number": number, "title": item.title})
            for character_id in dict.fromkeys(item.character_ids):
                connection.execute(insert(chapter_character).values(
                    chapter_id=chapter_id, character_id=int(character_id)))
            for foreshadow_id in item.foreshadow_ids:
                connection.execute(update(foreshadow_table).where(
                    foreshadow_table.c.id == int(foreshadow_id),
                    foreshadow_table.c.project_id == project_id,
                    foreshadow_table.c.planted_volume_id == volume_id,
                ).values(planted_chapter_id=chapter_id, status="planted", updated_at=utcnow()))
                saved_foreshadows += 1
            for item_foreshadow in item.new_foreshadows:
                insert_row(connection, foreshadow_table, project_id=project_id,
                           title=item_foreshadow.title.strip()[:200],
                           category=normalize_category(item_foreshadow.category)[:32],
                           status="planted", source="chapter_plan",
                           summary=item_foreshadow.summary[:4000], details=item_foreshadow.details[:20000],
                           planted_outline_id=volume["outline_id"], planted_volume_id=volume_id,
                           planted_chapter_id=chapter_id)
                saved_foreshadows += 1
        return ChapterPlanReceipt(project_id=project_id, volume_id=volume_id, chapters=entries,
                                  foreshadow_count=saved_foreshadows)


def require_empty_plan(chapter):
    if chapter["status"] != "planned" or chapter["content"].strip():
        raise HTTPException(409, "只能给尚无正文的计划章节生成初稿")


def save_draft(engine, chapter_id: int, content: str, original: dict, character_updates=()) -> DraftReceipt:
    """Save正文; ``character_updates`` is accepted only for legacy callers."""
    chapter = tables["novel_chapter"]
    count = sum(not character.isspace() for character in content)
    with transaction(engine) as connection:
        json_columns = legacy_json_columns(connection, chapter.name)
        current = response_row(get_row(connection, chapter, chapter_id, lock=True), json_columns)
        require_empty_plan(current)
        if any(current[key] != original[key] for key in ("project_id", "volume_id", "chapter_number", "title", "summary")):
            raise HTTPException(409, "生成期间章节计划发生变化，请重新生成")
        # 正文落库与人物档案解耦；人物档案由后续分析/一致性核对处理。
        connection.execute(update(chapter).where(chapter.c.id == chapter_id).values(**storage_values(
            {"content": content, "word_count": count, "status": "draft"}, json_columns)))
        return DraftReceipt(chapter_id=chapter_id, word_count=count)


def save_rewritten_draft(engine, chapter_id: int, content: str, original: dict, character_updates=()) -> DraftReceipt:
    """Replace正文; progress is normally populated by save_character_progress."""
    chapter = tables["novel_chapter"]
    count = sum(not character.isspace() for character in content)
    with transaction(engine) as connection:
        json_columns = legacy_json_columns(connection, chapter.name)
        current = response_row(get_row(connection, chapter, chapter_id, lock=True), json_columns)
        if any(current[key] != original[key] for key in (
                "project_id", "volume_id", "chapter_number", "title", "summary", "content", "status")):
            raise HTTPException(409, "重写期间章节发生变化，未覆盖原文，请刷新后重新重写")
        values = storage_values({"content": content, "word_count": count, "status": "draft"}, json_columns)
        # Rewritten text may invalidate old progress evidence; the body hash
        # makes stale facts unreadable until the next analysis refreshes them.
        connection.execute(update(chapter).where(chapter.c.id == chapter_id).values(**values))
        return DraftReceipt(chapter_id=chapter_id, word_count=count)


def save_continued_draft(engine, chapter_id: int, continuation: str, original: dict) -> DraftReceipt:
    """Append generated prose only when its source chapter is still unchanged."""
    chapter = tables["novel_chapter"]
    addition = continuation.strip()
    if not addition:
        raise HTTPException(502, "续写结果为空，原文未修改")
    separator = "" if original["content"].endswith(("\n", "\r", " ", "\t")) else "\n\n"
    content = original["content"] + separator + addition
    if len(content) > 30000:
        raise HTTPException(502, "续写后正文超过 30000 字符上限，原文未修改")
    count = sum(not character.isspace() for character in content)
    with transaction(engine) as connection:
        json_columns = legacy_json_columns(connection, chapter.name)
        current = response_row(get_row(connection, chapter, chapter_id, lock=True), json_columns)
        if any(current[key] != original[key] for key in (
                "project_id", "volume_id", "chapter_number", "title", "summary", "content", "status")):
            raise HTTPException(409, "续写期间章节发生变化，未追加正文，请刷新后重新续写")
        values = storage_values({"content": content, "word_count": count, "status": "draft"}, json_columns)
        connection.execute(update(chapter).where(chapter.c.id == chapter_id).values(**values))
        return DraftReceipt(chapter_id=chapter_id, word_count=count)


def save_character_progress(engine, chapter_id: int, original: dict, updates=()) -> CharacterProgressReceipt:
    """Save analysis for an unchanged chapter without touching its正文."""
    chapter = tables["novel_chapter"]
    with transaction(engine) as connection:
        json_columns = legacy_json_columns(connection, chapter.name)
        current = response_row(get_row(connection, chapter, chapter_id, lock=True), json_columns)
        if any(current[key] != original[key] for key in (
                "project_id", "volume_id", "chapter_number", "title", "summary", "content", "status")):
            raise HTTPException(409, "分析期间章节发生变化，请刷新后重新分析")
        problems = replace_progress(connection, current, current["content"], updates, strict=False)
        return CharacterProgressReceipt(chapter_id=chapter_id, updated_count=len(updates) - len(problems),
                                        skipped_count=len(problems), warning="；".join(problems))
