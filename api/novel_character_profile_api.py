"""Generate and browse outline-based character dossiers without schema changes."""

from collections import Counter

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from api import generation_jobs
from api.account_api import CurrentAccount
from api.character_profiles import validate_profiles
from api.database import Database, transaction
from api.novel_schemas import RowId
from api.novel_crud_api import response_row
from api.novel_tables import tables
from api.novel_writing_api import character_profile_context, generate, load_context, task_project
from api.novel_writing_store import save_characters
from api.ownership import require_readable
from api.writing_schemas import CharacterDraftList, CharacterProfileRequest, CharacterReceipt, ReusedCharacterDraft
from db.connection import legacy_json_columns


router = APIRouter(prefix="/writing", tags=["角色阶段档案"])


def volume_identity(engine, volume_id, account_id):
    project_id = task_project(engine, "novel_outline_volume", volume_id, account_id)
    volume = tables["novel_outline_volume"]
    with transaction(engine) as connection:
        outline_id = connection.scalar(select(volume.c.outline_id).where(volume.c.id == volume_id))
    return project_id, outline_id


def require_characters(engine, project_id, ids):
    character = tables["novel_character"]
    with transaction(engine) as connection:
        found = list(connection.scalars(select(character.c.id).where(character.c.project_id == project_id, character.c.id.in_(ids))))
    if set(found) != set(ids):
        raise HTTPException(404, "所选角色不存在或不属于当前小说")


@router.post("/volumes/{volume_id}/character-profiles", status_code=201, response_model=CharacterReceipt)
async def generate_profiles(volume_id: RowId, payload: CharacterProfileRequest, user: CurrentAccount, engine: Database):
    project_id, outline_id = await run_in_threadpool(volume_identity, engine, volume_id, user.id)
    await run_in_threadpool(require_characters, engine, project_id, payload.character_ids)
    emit_generation_event("status", message="正在读取大纲、角色、分卷档案与写作进度…")
    context, sources = await load_context(character_profile_context, project_id, outline_id, volume_id)
    people = {row["id"]: row for row in context["characters"]}
    if not set(payload.character_ids) <= people.keys():
        raise HTTPException(409, "角色已发生变化，请刷新后重新生成档案")
    context["target_volume"] = next(row for row in context["volumes"] if row["id"] == volume_id)
    context["selected_characters"] = [people[record_id] for record_id in payload.character_ids]
    result = await generate(agents.CHARACTER_PROFILES, context, payload)
    if Counter(row.character_id for row in result.profiles) != Counter(payload.character_ids):
        raise HTTPException(502, "模型返回的角色档案与勾选不符，本批次未保存")
    validate_profiles(result.profiles, context, volume_id)
    artifact = CharacterDraftList(characters=[], reused_characters=[ReusedCharacterDraft(
        character_id=row.character_id, scenes=row.volume_profiles[0].actions, volume_profiles=row.volume_profiles) for row in result.profiles])
    emit_generation_event("status", message="角色阶段档案校验通过，正在保存行动、阶段结果与进度关系…")
    return await run_in_threadpool(save_characters, engine, project_id, artifact, sources, volume_id)


@router.post("/volumes/{volume_id}/character-profiles/tasks", status_code=202)
async def start_profiles(volume_id: RowId, payload: CharacterProfileRequest, user: CurrentAccount, engine: Database):
    project_id, outline_id = await run_in_threadpool(volume_identity, engine, volume_id, user.id)
    await run_in_threadpool(require_characters, engine, project_id, payload.character_ids)
    return generation_jobs.store.start(account_id=user.id, key=f"character-profiles:{volume_id}", kind="characters",
        title="生成角色阶段档案", project_id=project_id, resource_id=outline_id,
        operation=lambda: generate_profiles(volume_id, payload, user, engine)).summary()


def profile_directory(engine, project_id, outline_id, user):
    outline, volume, cast, chapter = (tables[name] for name in (
        "novel_outline", "novel_outline_volume", "novel_volume_character", "novel_chapter"))
    with transaction(engine) as connection:
        require_readable(connection, "novel_project", project_id, user)
        selected = connection.execute(select(outline).where(outline.c.id == outline_id, outline.c.project_id == project_id)).mappings().one_or_none()
        if selected is None:
            raise HTTPException(404, "所选大纲不存在或不属于当前小说")
        rows = connection.execute(select(cast, volume.c.volume_number, volume.c.title.label("volume_title")).select_from(
            cast.join(volume, cast.c.volume_id == volume.c.id)).where(volume.c.outline_id == outline_id)
            .order_by(volume.c.volume_number, cast.c.character_id)).mappings().all()
        progress_rows = connection.execute(select(chapter.c.volume_id, chapter.c.chapter_number, chapter.c.content).select_from(
                chapter.join(volume, chapter.c.volume_id == volume.c.id)).where(
                    volume.c.outline_id == outline_id, chapter.c.project_id == project_id)).mappings().all()
        cast_json = set(legacy_json_columns(connection, cast.name))
        if "title" in legacy_json_columns(connection, volume.name):
            cast_json.add("volume_title")
        rows = [response_row(row, cast_json) for row in rows]
        progress_rows = [response_row(row, legacy_json_columns(connection, chapter.name)) for row in progress_rows]
    progress = {}
    for row in progress_rows:
        current = progress.setdefault(row["volume_id"], dict(planned_chapters=0, written_chapters=0, last_written_chapter=None))
        current["planned_chapters"] += 1
        if row["content"].strip():
            current["written_chapters"] += 1
            current["last_written_chapter"] = max(current["last_written_chapter"] or 0, row["chapter_number"])
    return {"outline_id": outline_id, "outline_version": selected["version_no"], "items": [
        {**row, "progress": progress.get(row["volume_id"], dict(planned_chapters=0, written_chapters=0, last_written_chapter=None))} for row in rows]}


@router.get("/projects/{project_id}/character-profiles")
async def list_profiles(project_id: RowId, outline_id: RowId, user: CurrentAccount, engine: Database):
    return await run_in_threadpool(profile_directory, engine, project_id, outline_id, user)
