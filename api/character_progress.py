"""Save verified chapter facts atomically and read them in story order."""

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select

from api.novel_tables import tables
from db.character_progress import character_progress as progress
from db.connection import legacy_json_columns


def body_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def replace_progress(connection, chapter, content, updates, strict=True):
    """Caller locks the chapter and commits its body and facts together.

    strict=True 用于要求原子校验的调用。
    strict=False 用于后续人物进展/一致性分析：跳过无法核实的角色并返回提示。
    """
    people = tables["novel_character"]
    ids = [item.character_id for item in updates]
    if len(ids) != len(set(ids)):
        if strict:
            raise HTTPException(502, "正文人物档案包含重复角色，本章未保存")
        # Progress analysis is advisory. Keep the first update for a repeated
        # character and let the consistency pass review the model output.
        seen = set()
        updates = [item for item in updates if not (item.character_id in seen or seen.add(item.character_id))]
        ids = [item.character_id for item in updates]
    found = {row["id"]: row for row in connection.execute(select(people.c.id, people.c.name).where(
        people.c.project_id == chapter["project_id"], people.c.id.in_(ids)
    ).order_by(people.c.id).with_for_update()).mappings()}
    missing_ids = set(ids) - set(found)
    if missing_ids and strict:
        raise HTTPException(502, "正文人物档案引用了当前小说之外的角色，本章未保存")
    problems = [f"跳过不存在的角色 ID：{record_id}" for record_id in sorted(missing_ids)]
    updates = [item for item in updates if item.character_id in found]
    valid = []
    for item in updates:
        name = found[item.character_id]["name"]
        problem = None
        if name not in content:
            problem = f"正文没有出现姓名「{name}」"
        elif not any(name in quote for quote in item.evidence):
            problem = f"引文未包含姓名「{name}」"
        elif any(quote not in content for quote in item.evidence):
            problem = f"姓名「{name}」的引文无法在正文中逐字匹配"
        if problem is None:
            valid.append(item)
            continue
        problems.append(f"角色「{name}」{problem}")
        if strict:
            raise HTTPException(502, f"人物进展未保存：角色「{name}」{problem}，请重试分析")
    if problems and not valid and strict:
        raise HTTPException(502, f"人物进展未保存：{problems[0]}，请重试分析")
    if valid or not problems:
        connection.execute(delete(progress).where(progress.c.chapter_id == chapter["id"]))
    if valid:
        connection.execute(insert(progress), [dict(
            project_id=chapter["project_id"], chapter_id=chapter["id"], content_hash=body_hash(content),
            **item.model_dump(exclude={"evidence"}), evidence=json.dumps(item.evidence, ensure_ascii=False),
        ) for item in valid])
    return problems


def read_progress(connection, project_id, *, before_chapter_number=None, latest=False, limit=100, offset=0):
    chapter, people = tables["novel_chapter"], tables["novel_character"]
    statement = select(progress, chapter.c.chapter_number, chapter.c.title, chapter.c.volume_id,
                       chapter.c.content.label("source_content")).select_from(
        progress.join(chapter, progress.c.chapter_id == chapter.c.id).join(people, progress.c.character_id == people.c.id)
    ).where(progress.c.project_id == project_id, chapter.c.project_id == project_id, people.c.project_id == project_id)
    if before_chapter_number is not None:
        statement = statement.where(chapter.c.chapter_number < before_chapter_number)
    if latest:
        ranking = select(progress.c.id, func.row_number().over(
            partition_by=progress.c.character_id,
            order_by=(chapter.c.chapter_number.desc(), progress.c.id.desc())).label("position")
        ).select_from(progress.join(chapter, progress.c.chapter_id == chapter.c.id)).where(
            progress.c.project_id == project_id, chapter.c.project_id == project_id)
        if before_chapter_number is not None:
            ranking = ranking.where(chapter.c.chapter_number < before_chapter_number)
        ranked = ranking.subquery()
        statement = statement.where(progress.c.id.in_(select(ranked.c.id).where(ranked.c.position == 1)))
    statement = statement.order_by(chapter.c.chapter_number.desc(), progress.c.id.desc())
    json_columns = legacy_json_columns(connection, chapter.name)
    items, skipped = [], 0
    result = connection.execution_options(stream_results=True).execute(statement)
    try:
        for row in result.mappings():
            body = row["source_content"]
            if "content" in json_columns:
                body = json.loads(body)
            if not isinstance(body, str) or row["content_hash"] != body_hash(body):
                continue
            if skipped < offset:
                skipped += 1
                continue
            items.append({**{key: row[key] for key in (
                "id", "chapter_id", "character_id", "chapter_number", "title", "volume_id", "actions", "current_state", "relationships")},
                "evidence": json.loads(row["evidence"])})
            if len(items) > limit:
                break
    finally:
        result.close()
    return {"project_id": project_id, "items": items[:limit], "limit": limit,
            "offset": offset, "has_more": len(items) > limit}
