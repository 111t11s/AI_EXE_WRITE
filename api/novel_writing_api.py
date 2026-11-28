"""Coordinate writing agents with scoped MCP context and validated saves."""

import json
from collections import Counter

import httpx
from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from agents import writing_agents as agents
from agents.llm_streaming import IncompleteGenerationError, emit_generation_event
from agents.outline_skills import OutlineSkillError
from agents.prose_skills import ProseSkillError
from api.generation_stream import generation_response
from api import generation_jobs
from api.account_api import CurrentAccount
from api.ownership import require_resource
from agents.mcp.worldbuilding import NovelToolError, call_novel_tool, novel_session
from api.novel_crud_api import Database, get_row, response_row
from api.novel_schemas import RowId
from api.novel_writing_store import (character_name_key, next_volume_number, outline_volume_number,
                                     require_empty_plan, save_chapter_plan, save_draft, save_outline)
from api.novel_writing_store import (save_characters, save_rewritten_draft, save_character_progress,
                                     save_continued_draft)
from api.novel_tables import tables
from api.database import transaction
from sqlalchemy import func, select
from db.connection import legacy_json_columns
from db import novel_analysis as analysis_db
from db import foreshadows as foreshadow_db
from db import chapter_characters as chapter_cast_db
from api.writing_schemas import (
    ChapterPlanReceipt, ChapterPlanRequest, DraftReceipt, DraftRequest, OutlineDraft, OutlineReceipt, OutlineRequest,
    FORESHADOW_CATEGORY_LABELS,
    ReviewReport, WritingInput,
    CoreCharacterRequest, SupportingCharacterRequest, CharacterReceipt,
    CharacterProgressReceipt,
    BatchDraftRequest, BatchDraftReceipt, BatchDraftChapterReceipt,
    CharacterFactionDraft, CharacterRelationshipDraft,
)
from api.character_profiles import validate_profiles
from core.app_logging import get_logger
from core.models import ModelProfileError, endpoint_host, resolve_profile, safe_error_reason


router = APIRouter(prefix="/writing", tags=["小说写作智能体"])
logger = get_logger("novel_writing")
OUTLINE_TECHNIQUE_CATEGORIES = {"worldbuilding", "outline"}
PROSE_TECHNIQUE_CATEGORIES = {"prose"}


async def read(session, table, record_id):
    return await call_novel_tool(session, f"read_novel_{table}", {"record_id": record_id})


async def listing(session, table, **filters):
    page = await call_novel_tool(session, f"list_novel_{table}", {"filters": filters})
    return page["items"]


def excerpt(row, *fields, limit=1400):
    return {key: value[:limit] if isinstance(value, str) else value for key in fields if (value := row.get(key)) is not None}


async def project_context(session, project_id, include_characters=True):
    project = await read(session, "project", project_id)
    worlds = await listing(session, "world", project_id=project_id, is_current=True, limit=2)
    if len(worlds) != 1:
        raise HTTPException(409, "请先为小说项目设置唯一的当前世界观")
    characters = await all_context_rows(session, "character", project_id=project_id) if include_characters else []
    return {
        "project": excerpt(project, "id", "title", "genre", "setting", "tone", "target_length", "premise", "protagonist_goal"),
        "world": excerpt(worlds[0], "id", "title", "summary", "era", "history", "rules", "cultures", limit=2400),
        "characters": [excerpt(item, "id", "name", "role", "summary", "personality", "background", "goal", "motivation",
                               "weakness", "abilities", "character_arc", "status", limit=600) for item in characters],
    }


async def all_context_rows(session, table, **filters):
    rows = []
    while True:
        page = await call_novel_tool(session, f"list_novel_{table}",
                                     {"filters": {**filters, "limit": 100, "offset": len(rows)}})
        rows.extend(page["items"])
        if not page["has_more"]:
            return rows
        if not page["items"] or len(rows) >= 500:
            raise HTTPException(422, "小说资料记录过多，请缩减单个项目资料后重试")


async def character_context(session, project_id, outline_id=None):
    context = await project_context(session, project_id, include_characters=False)
    world = await read(session, "world", context["world"]["id"])
    project = await read(session, "project", project_id)
    # Owner labels are presentation data, not part of the novel snapshot checked on save.
    context["project"] = {key: value for key, value in project.items() if key in tables["novel_project"].c}
    context["world"] = world
    sources = [("novel_world", world)]
    context["characters"] = await all_context_rows(session, "character", project_id=project_id)
    sources.append(("novel_project", context["project"]))
    sources.extend(("novel_character", row) for row in context["characters"])
    for name in ("region", "power_system", "cheat_system", "faction"):
        rows = await all_context_rows(session, name, world_id=world["id"])
        context[name] = rows
        sources.extend((f"novel_{name}", row) for row in rows)
    for system in context["power_system"]:
        ranks = await all_context_rows(session, "power_rank", power_system_id=system["id"])
        context.setdefault("power_ranks", []).extend(ranks)
        sources.extend(("novel_power_rank", row) for row in ranks)
    if outline_id is not None:
        outline = await read(session, "outline", outline_id)
        if outline["project_id"] != project_id:
            raise HTTPException(404, "相关小说记录不存在")
        context["outline"] = outline
        sources.append(("novel_outline", outline))
        volumes = await all_context_rows(session, "outline_volume", outline_id=outline_id)
        context["volumes"] = []
        for volume in volumes:
            beats = await all_context_rows(session, "plot_beat", volume_id=volume["id"])
            cast = await all_context_rows(session, "volume_character", volume_id=volume["id"])
            context["volumes"].append({**volume, "beats": beats, "cast": cast})
            sources.append(("novel_outline_volume", volume))
            sources.extend(("novel_plot_beat", row) for row in beats)
            sources.extend(("novel_volume_character", row) for row in cast)
    # Keep a useful bound rather than silently omitting later characters or plot nodes.
    if len(json.dumps(context, ensure_ascii=False, default=str)) > 150000:
        raise HTTPException(422, "角色生成资料超过单次容量，请精简世界观、角色或大纲")
    return context, sources


async def character_profile_context(session, project_id, outline_id, volume_id=None):
    context, sources = await character_context(session, project_id, outline_id)
    volumes = [row for row in context["volumes"] if volume_id is None or row["id"] == volume_id]
    if not volumes:
        raise HTTPException(404, "目标分卷不属于当前大纲")
    context["writing_progress"] = []
    for volume in volumes:
        chapters = await all_context_rows(session, "chapter", project_id=project_id, volume_id=volume["id"])
        sources.extend(("novel_chapter", row) for row in chapters)
        written = [row for row in chapters if row["content"].strip()]
        context["writing_progress"].append({
            "volume_id": volume["id"], "planned_chapters": len(chapters), "written_chapters": len(written),
            "last_written_chapter": max((row["chapter_number"] for row in written), default=None),
            "chapters": [{key: row[key] for key in ("id", "chapter_number", "title", "summary", "status")} for row in chapters],
            "note": "这里只提供写作数量与章节计划，不包含正文；不能据此确认某个角色行动或结局已发生。",
        })
    if len(json.dumps(context, ensure_ascii=False, default=str)) > 200000:
        raise HTTPException(422, "角色档案与进度资料超过单次容量，请减少章节或精简资料")
    return context, sources


def reusable_characters(context, volume_id):
    target = next((row for row in context["volumes"] if row["id"] == volume_id), None)
    if target is None:
        raise HTTPException(404, "目标分卷不属于当前大纲")
    previous = sorted((row for row in context["volumes"] if row["volume_number"] < target["volume_number"]),
                      key=lambda row: row["volume_number"])
    candidates = []
    for character in context["characters"]:
        if character["role"].startswith("主角"):
            continue
        history = []
        has_cast = any(row["character_id"] == character["id"] for volume in context["volumes"] for row in volume["cast"])
        for volume in previous:
            assignment = next((row for row in volume["cast"] if row["character_id"] == character["id"]), None)
            # Earlier releases included a volume's title in summaries, without cast links.
            if assignment or (not has_cast and volume["title"] in character["summary"]):
                history.append({"volume_id": volume["id"], "volume_number": volume["volume_number"],
                                "title": volume["title"], "scenes": assignment["scenes"] if assignment else character["summary"]})
        if history:
            candidates.append({**character, "previous_scenes": history})
    return target, candidates


@router.get("/volumes/{volume_id}/reusable-characters", summary="查询前面分卷可复用的配角及戏份")
async def list_reusable_characters(volume_id: RowId, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_outline_volume", volume_id, user.id)
    with transaction(engine) as connection:
        relation = tables["novel_outline_volume"]
        outline_id = connection.scalar(select(relation.c.outline_id).where(relation.c.id == volume_id))
    project_id = await run_in_threadpool(task_project, engine, "novel_outline", outline_id, user.id)
    context, _ = await load_context(character_context, project_id, outline_id)
    _, candidates = reusable_characters(context, volume_id)
    return {"items": candidates}


def ensure_character_assignments(artifact, context, *, core):
    """Fill any missing faction or protagonist link before the atomic save."""
    existing_characters = list(context.get("characters", []))
    generated_characters = artifact.characters
    all_names = {character_name_key(row["name"]) for row in existing_characters}
    all_names.update(character_name_key(row.name) for row in generated_characters)
    faction_names = {character_name_key(row["name"]): row["name"] for row in context.get("faction", [])}
    for faction in artifact.new_factions:
        faction_names.setdefault(character_name_key(faction.name), faction.name)

    leads = [row for row in existing_characters if row.get("role", "").startswith("主角")]
    leads.extend(row for row in generated_characters if row.category == "protagonist")
    if not core and not leads:
        raise HTTPException(409, "请先生成男主和女主，再创建配角关系")

    def add_faction(name, summary):
        key = character_name_key(name)
        faction_names[key] = name
        artifact.new_factions.append(CharacterFactionDraft(
            name=name, faction_type="自动归属", summary=summary[:4000]))

    for character in generated_characters:
        faction_key = character_name_key(character.faction_name)
        if faction_key and faction_key not in faction_names:
            # Keep the model's chosen name and create a concrete faction record
            # when it did not include a separate faction definition.
            add_faction(character.faction_name,
                        f"{character.name}及相关人物所属的势力。设定依据：{character.summary}")
        elif not faction_key:
            if not faction_names:
                add_faction("独立行动者", "用于归档尚未加入固定组织的独立角色，可在势力树中调整为具体势力。")
            character.faction_name = next(iter(faction_names.values()))
        else:
            character.faction_name = faction_names[faction_key]

        valid_relationships = []
        for relationship in character.relationships:
            target_key = character_name_key(relationship.target_name)
            if target_key in all_names and target_key != character_name_key(character.name):
                valid_relationships.append(relationship)
        character.relationships = valid_relationships
        protagonist_targets = [row for row in leads
                               if character_name_key(row["name"] if isinstance(row, dict) else row.name)
                               != character_name_key(character.name)]
        has_lead_link = any(character_name_key(item.target_name) in {
            character_name_key(row["name"] if isinstance(row, dict) else row.name) for row in protagonist_targets
        } for item in character.relationships)
        if not has_lead_link and protagonist_targets:
            target = protagonist_targets[0]
            target_name = target["name"] if isinstance(target, dict) else target.name
            character.relationships.append(CharacterRelationshipDraft(
                target_name=target_name,
                relationship_type="主线关联" if character.category != "protagonist" else "共同主角",
                description=(f"{character.name}与{target_name}共同承载当前大纲主线；具体关系随剧情展开。"
                             if character.category == "protagonist" else
                             f"{character.name}参与{target_name}所在的主线剧情，具体关系可随剧情发展调整。")))


async def create_characters(project_id, outline_id, payload, user, engine):
    core = isinstance(payload, CoreCharacterRequest)
    if core:
        outline_id = payload.outline_id
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    # Core characters are part of the active story plan whenever an outline
    # already exists.  Keep the field optional only for a brand-new project
    # that has not received its first outline yet.
    if core and outline_id is None:
        outline_id = await run_in_threadpool(latest_outline_id, engine, project_id)
    if outline_id is not None:
        await run_in_threadpool(require_resource, engine, "novel_outline", outline_id, user.id)
    emit_generation_event("status", message="正在通过 MCP 读取世界观、已有角色和剧情资料…")
    if outline_id is not None:
        context, sources = await load_context(character_profile_context, project_id, outline_id, payload.volume_id)
        if core and not context["volumes"]:
            raise HTTPException(409, "当前大纲尚未生成分卷，无法按大纲绑定核心人物")
    else:
        context, sources = await load_context(character_context, project_id)
    if core and any(row["role"].startswith("主角") for row in context["characters"]):
        raise HTTPException(409, "男女主已生成；后续只能根据大纲添加配角")
    volume_id = payload.volume_id
    selected = [] if core else payload.reuse_character_ids
    legacy_assignments = []
    if volume_id is not None:
        target, candidates = reusable_characters(context, volume_id)
        by_id = {row["id"]: row for row in candidates}
        if any(record_id not in by_id for record_id in selected):
            raise HTTPException(422, "只能复用同一大纲前面分卷已经出场的配角")
        context["target_volume"] = target
        context["reuse_characters"] = [by_id[record_id] for record_id in selected]
        for character in context["reuse_characters"]:
            for scene in character["previous_scenes"]:
                previous_volume = next(row for row in context["volumes"] if row["id"] == scene["volume_id"])
                if not any(row["character_id"] == character["id"] for row in previous_volume["cast"]):
                    legacy_assignments.append((scene["volume_id"], character["id"], scene["scenes"]))
    artifact = await generate(agents.CORE_CHARACTERS if core else agents.SUPPORTING_CHARACTERS, context, payload)
    counts = Counter(item.category for item in artifact.characters)
    if core:
        if +counts != +Counter({"protagonist": 2, "important": 0}):
            raise HTTPException(502, "模型返回的角色数量或层级与请求不符，本批次未保存")
    elif payload.supporting_count is not None:
        if +counts != +Counter({"supporting": payload.supporting_count}):
            raise HTTPException(502, "模型返回的角色数量或层级与请求不符，本批次未保存")
    elif any(item.category != "supporting" for item in artifact.characters):
        raise HTTPException(502, "自动挑选配角时只能返回 supporting 层级的角色，本批次未保存")
    if core and Counter(item.gender for item in artifact.characters) != Counter({"男": 1, "女": 1}):
        raise HTTPException(502, "模型必须分别返回一位男主和一位女主，本批次未保存")
    ensure_character_assignments(artifact, context, core=core)
    returned = [item.character_id for item in artifact.reused_characters]
    if Counter(returned) != Counter(selected):
        raise HTTPException(502, "模型返回的复用角色与勾选不符，本批次未保存")
    if outline_id is not None:
        validate_profiles([*artifact.characters, *artifact.reused_characters], context, volume_id)
    elif any(item.volume_profiles for item in artifact.characters):
        raise HTTPException(502, "尚无指定大纲，不能保存虚构的分卷角色档案")
    emit_generation_event("status", message="角色设定校验通过，正在整批保存角色…")
    return await run_in_threadpool(save_characters, engine, project_id, artifact, sources, volume_id, legacy_assignments)


def latest_outline_id(engine, project_id):
    outline = tables["novel_outline"]
    with transaction(engine) as connection:
        return connection.scalar(select(outline.c.id).where(outline.c.project_id == project_id).order_by(
            outline.c.version_no.desc(), outline.c.id.desc()).limit(1))


@router.post("/projects/{project_id}/characters/core", response_model=CharacterReceipt, status_code=201,
             summary="生成唯一的一位男主和一位女主")
async def write_core_characters(project_id: RowId, payload: CoreCharacterRequest, user: CurrentAccount, engine: Database):
    return await create_characters(project_id, None, payload, user, engine)


@router.post("/outlines/{outline_id}/characters/supporting", response_model=CharacterReceipt, status_code=201,
             summary="根据大纲生成其余配角")
async def write_supporting_characters(outline_id: RowId, payload: SupportingCharacterRequest,
                                      user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_outline", outline_id, user.id)
    return await create_characters(project_id, outline_id, payload, user, engine)


@router.post("/projects/{project_id}/characters/core/tasks", status_code=202, summary="后台生成唯一的一位男主和一位女主")
async def start_core_characters(project_id: RowId, payload: CoreCharacterRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    if payload.outline_id is not None:
        related_project = await run_in_threadpool(task_project, engine, "novel_outline", payload.outline_id, user.id)
        if related_project != project_id:
            raise HTTPException(404, "所选大纲不属于当前小说")
        if payload.volume_id is not None:
            await run_in_threadpool(require_resource, engine, "novel_outline_volume", payload.volume_id, user.id)
            with transaction(engine) as connection:
                relation = tables["novel_outline_volume"]
                belongs = connection.scalar(select(relation.c.outline_id).where(relation.c.id == payload.volume_id))
            if belongs != payload.outline_id:
                raise HTTPException(404, "目标分卷不属于当前大纲")
    return generation_jobs.store.start(account_id=user.id, key=f"characters:core:{project_id}", kind="characters",
        title="生成男女主", project_id=project_id, resource_id=payload.outline_id or project_id,
        operation=lambda: write_core_characters(project_id, payload, user, engine)).summary()


@router.post("/outlines/{outline_id}/characters/supporting/tasks", status_code=202, summary="后台生成大纲配角")
async def start_supporting_characters(outline_id: RowId, payload: SupportingCharacterRequest,
                                      user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_outline", outline_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"characters:supporting:{outline_id}:{payload.volume_id}", kind="characters",
        title="根据大纲补充配角", project_id=project_id, resource_id=outline_id,
        operation=lambda: write_supporting_characters(outline_id, payload, user, engine)).summary()


async def volume_context(session, volume_id):
    volume = await read(session, "outline_volume", volume_id)
    outline = await read(session, "outline", volume["outline_id"])
    context = await project_context(session, outline["project_id"])
    context["outline"] = excerpt(outline, "id", "summary", "central_conflict", "themes")
    context["volume"] = excerpt(volume, "id", "volume_number", "title", "summary", "protagonist_goal", "main_conflict",
                                "climax", "ending_hook", "min_foreshadows", "foreshadow_categories",
                                "target_word_min", "target_word_max", "plot_progression")
    context["volume_characters"] = await all_context_rows(session, "volume_character", volume_id=volume_id)
    beats = await listing(session, "plot_beat", volume_id=volume_id, limit=12)
    context["plot_beats"] = [excerpt(item, "sort_order", "title", "summary", "foreshadowing", limit=500) for item in beats]
    return context


async def previous_chapter(session, project_id, number):
    if number <= 1:
        return None
    rows = await listing(session, "chapter", project_id=project_id, chapter_number=number - 1, limit=1)
    if not rows:
        return None
    return {**excerpt(rows[0], "chapter_number", "title", "summary"), "ending": rows[0]["content"][-1200:]}


async def plan_context(session, volume_id, start, count=1, next_number=None):
    context = await volume_context(session, volume_id)
    project_id = context["project"]["id"]
    for number in range(start, start + count):
        if await listing(session, "chapter", project_id=project_id, chapter_number=number, limit=1):
            hint = f"本项目下一个可用章号是 {next_number}。" if next_number else ""
            raise HTTPException(409, f"章号 {number} 已被使用，请选择未使用的章号。{hint}")
    context["previous_chapter"] = await previous_chapter(session, project_id, start)
    return context


def next_free_chapter_number(engine, project_id):
    """下一个可用章号按全书最大章号 + 1 计算。"""
    chapter = tables["novel_chapter"]
    with engine.connect() as connection:
        highest = connection.scalar(select(func.max(chapter.c.chapter_number)).where(
            chapter.c.project_id == project_id))
    return int(highest or 0) + 1


@router.get("/projects/{project_id}/chapter-plan/next", summary="查询下一个可用章号")
async def next_chapter_number(project_id: RowId, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    return {"next_chapter_number": await run_in_threadpool(next_free_chapter_number, engine, project_id)}


async def chapter_context(session, chapter_id, review, rewrite=False):
    chapter = await read(session, "chapter", chapter_id)
    if rewrite and not chapter["content"].strip():
        raise HTTPException(409, "该章节还没有正文，请先生成正文后再重写")
    if review and not chapter["content"].strip():
        raise HTTPException(409, "该章节还没有可审读的正文")
    if not review and not rewrite:
        require_empty_plan(chapter)
    if chapter["volume_id"] is not None:
        context = await volume_context(session, chapter["volume_id"])
    else:
        context = await project_context(session, chapter["project_id"])
    if (not review or rewrite) and not context["characters"]:
        raise HTTPException(409, "尚未生成角色信息，请先在角色页面设置数量并生成角色，再生成正文")
    if len(chapter["summary"]) > 8000:
        raise HTTPException(422, "章节摘要超过单次写作上限 8000 字符")
    context["chapter"] = {key: chapter[key] for key in ("id", "project_id", "volume_id", "chapter_number", "title", "summary")}
    context["previous_chapter"] = await previous_chapter(session, chapter["project_id"], chapter["chapter_number"])
    context["character_progress"] = []
    while True:
        page = await call_novel_tool(session, "list_novel_character_progress", {
            "project_id": chapter["project_id"], "before_chapter_number": chapter["chapter_number"],
            "latest": True, "limit": 100, "offset": len(context["character_progress"]),
        })
        context["character_progress"].extend(page["items"])
        if not page["has_more"]:
            break
        if not page["items"] or len(context["character_progress"]) >= 500:
            raise HTTPException(422, "人物正文进展档案过多，请精简单个项目的角色数量")
    if review or rewrite:
        if len(chapter["content"]) > 30000:
            raise HTTPException(422, "正文超过单次处理上限 30000 字符")
        context["chapter"]["content"] = chapter["content"]
    if rewrite:
        context["chapter"].update({key: chapter[key] for key in ("project_id", "volume_id", "status")})
    if len(json.dumps(context, ensure_ascii=False, default=str)) > 200000:
        raise HTTPException(422, "章节与人物资料超过单次容量，请精简档案后重试")
    return context


async def character_progress_context(session, chapter_id):
    """Load a saved chapter for standalone character-progress analysis."""
    context = await chapter_context(session, chapter_id, True)
    chapter = await read(session, "chapter", chapter_id)
    context["chapter"]["status"] = chapter["status"]
    return context


async def load_context(loader, *args):
    problem = None
    try:
        async with novel_session() as session:
            try:
                return await loader(session, *args)
            except (HTTPException, NovelToolError) as exc:
                problem = exc
    except Exception as exc:
        logger.warning("写作资料连接失败：异常类型=%s", type(exc).__name__)
        raise HTTPException(503, "无法连接小说 MCP 服务") from None
    if isinstance(problem, HTTPException):
        raise problem
    raise HTTPException(problem.status, "相关小说记录不存在" if problem.status == 404 else str(problem)) from None


TREND_FIELD_LABELS = {
    "overview": "总览", "ideas": "推荐方案", "genre": "类型", "setting": "时代背景",
    "power_system": "力量体系", "cheat": "金手指", "protagonist": "主角设定", "goal": "主角目标",
    "tone": "基调", "length": "篇幅", "title": "参考书名", "premise": "一句话梗概",
    "core_conflict": "核心冲突", "discussion_hook": "话题点", "selling_points": "卖点",
    "reference_topics": "参考热点", "risks": "风险提示",
}


def trend_problem(fields: list[str]) -> str:
    """把 Schema 字段路径转成作者看得懂的说明。"""
    if not fields:
        return "缺少必要内容"
    parts = fields[0].split(".")
    label = TREND_FIELD_LABELS.get(parts[-1], parts[-1])
    if len(parts) >= 3 and parts[0] == "ideas":
        try:
            index = int(parts[1]) + 1
        except ValueError:
            index = 0
        if index:
            return f"第 {index} 条推荐的{label}不符合要求"
    return f"{label}不符合要求"


async def generate(role, context, payload):
    try:
        profile = resolve_profile(payload.model)
    except ModelProfileError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        request = payload.model_dump()
        # 模型配置只用于选择调用参数，不进入发给模型的资料。
        request.pop("model", None)
        # Keep omitted theme fields out of the context for older callers while
        # agents.generate supplies the backwards-compatible default guidance.
        if isinstance(payload, OutlineRequest) and "outline_theme" not in payload.model_fields_set:
            request.pop("outline_theme", None)
        return await run_in_threadpool(agents.generate, role, {**context, "request": request}, profile)
    except (OutlineSkillError, ProseSkillError) as exc:
        raise HTTPException(503, str(exc)) from None
    except (APITimeoutError, httpx.TimeoutException):
        logger.warning("模型请求超时：任务=%s", role.title)
        raise HTTPException(504, "模型响应超时，请稍后重试") from None
    except (APIConnectionError, httpx.RequestError):
        logger.warning("模型网关连接失败：任务=%s，主机=%s", role.title,
                       endpoint_host(profile.base_url))
        raise HTTPException(502, "无法连接模型网关，请检查网络或模型服务配置") from None
    except APIStatusError as exc:
        host = endpoint_host(profile.base_url)
        model_name = profile.model
        reason = safe_error_reason(exc)
        logger.warning("模型网关请求失败：任务=%s，模型=%s，主机=%s，状态码=%s，原因=%s",
                       role.title, model_name, host, exc.status_code, reason or "未提供")
        if exc.status_code in (401, 403):
            detail = (f"模型网关拒绝了访问（{exc.status_code}）：请检查 API 密钥、模型权限或 IP 白名单，"
                      "也可在生成时改用其他已配置模型")
        elif exc.status_code == 429:
            detail = "模型网关限制了请求，请稍后重试或检查额度"
        elif exc.status_code >= 500:
            detail = f"模型服务 {host} 返回 {exc.status_code}，上游暂时不可用，请稍后重试或切换模型"
        else:
            detail = "模型网关拒绝了生成请求，请检查模型和服务地址配置"
        raise HTTPException(502, detail) from None
    except IncompleteGenerationError as exc:
        raise HTTPException(502, str(exc)) from None
    except ValidationError as exc:
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors(include_input=False)]
        logger.warning("模型输出未通过内容校验：任务=%s，字段=%s", role.title, fields[:3])
        if role is agents.TREND_ANALYST:
            raise HTTPException(502, f"模型返回的题材格式不符合要求：{trend_problem(fields)}，请重试生成") from None
        raise HTTPException(502, "模型已返回内容，但格式未通过校验，请重试生成") from None
    except Exception as exc:
        logger.warning("写作智能体生成失败：任务=%s，异常类型=%s", role.title, type(exc).__name__)
        raise HTTPException(502, "模型生成失败或未返回有效的小说内容") from None


async def apply_techniques(context: dict, payload: WritingInput, user: CurrentAccount, engine,
                           categories: set[str]) -> dict:
    """把用户选中的知识库技法注入生成上下文；只借鉴写法，不改既定事实。"""
    ids = list(payload.technique_ids)
    if not ids:
        return context
    rows = await run_in_threadpool(analysis_db.get_techniques, engine, user.id, ids)
    if len(rows) != len(ids):
        raise HTTPException(404, "所选技法不存在或已删除，请重新选择")
    invalid = [row["name"] for row in rows if row.get("category") not in categories]
    if invalid:
        raise HTTPException(422, f"以下技法分类不适用于本次任务：{'、'.join(invalid)}")
    context["techniques"] = rows
    lines = ["\n用户从写作技法知识库选择的参考技法（只借鉴写法，不照搬原文；不得改变已确定的人物、世界观、大纲和正文事实）："]
    for index, row in enumerate(rows, 1):
        lines.append(f"{index}.〔{row.get('category_label', '')}·{row.get('dimension_label', '')}〕"
                     f"{row.get('name', '')}：{row.get('summary', '')}")
        if row.get("reuse"):
            lines.append(f"   复用方式：{row['reuse']}")
        evidence = row.get("evidence") or []
        if evidence and evidence[0].get("quote"):
            lines.append(f"   原文依据：{evidence[0]['quote'][:160]}")
    lines.append("新建内容时用这些技法组织表达；重写、续写或润色时在当前内容基础上融入技法，"
                 "保留原有情节、人物关系、视角和结果，不另起故事。")
    context["technique_prompt"] = "\n".join(lines)
    emit_generation_event("status", message=f"已加载 {len(rows)} 条写作技法参考。")
    return context


def validate_outline_foreshadows(artifact: OutlineDraft, payload: OutlineRequest) -> None:
    planned = artifact.volumes[0].foreshadows
    if len(planned) < payload.min_foreshadows:
        raise HTTPException(502, f"本卷至少需要 {payload.min_foreshadows} 条伏笔，模型只返回 {len(planned)} 条，结果未保存")
    present = {foreshadow_db.normalize_category(item.category) for item in planned}
    missing = [FORESHADOW_CATEGORY_LABELS[key] for key in payload.foreshadow_categories if key not in present]
    if missing:
        raise HTTPException(502, "模型没有生成本卷要求的伏笔类型：" + "、".join(missing) + "，结果未保存")


@router.post("/projects/{project_id}/outline", response_model=OutlineReceipt, status_code=201, summary="生成并保存单卷小说大纲")
async def write_outline(project_id: RowId, payload: OutlineRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    volume_number = payload.volume_number or await run_in_threadpool(next_volume_number, engine, project_id)
    context = await load_context(project_context, project_id)
    context["volume_plan"] = {"volume_number": volume_number,
                              "theme": payload.volume_theme.strip() or payload.instruction.strip()}
    context["outline_plan"] = {
        "target_word_min": payload.target_word_min,
        "target_word_max": payload.target_word_max,
        "plot_progression": payload.plot_progression,
        "min_foreshadows": payload.min_foreshadows,
        "foreshadow_categories": {key: FORESHADOW_CATEGORY_LABELS[key] for key in payload.foreshadow_categories},
    }
    context = await apply_techniques(context, payload, user, engine, OUTLINE_TECHNIQUE_CATEGORIES)
    artifact = await generate(agents.OUTLINE, context, payload)
    if len(artifact.volumes) != 1:
        raise HTTPException(502, "每次只生成一卷大纲，模型返回了多个分卷，结果未保存")
    validate_outline_foreshadows(artifact, payload)
    emit_generation_event("status", message="单卷大纲校验通过，正在保存大纲、伏笔与分卷…")
    receipt = await run_in_threadpool(save_outline, engine, project_id, artifact, (),
                                      volume_number, payload.model_dump(include={
                                          "min_foreshadows", "foreshadow_categories", "target_word_min",
                                          "target_word_max", "plot_progression"}))
    try:
        await run_in_threadpool(foreshadow_db.apply_outline_selection, engine, project_id,
                                payload.consume_foreshadow_ids, payload.plant_foreshadow_ids,
                                receipt.outline_id, receipt.volume_ids[0] if receipt.volume_ids else None)
    except Exception:
        logger.warning("大纲伏笔消费/预埋保存失败；大纲已保存", exc_info=True)
    return receipt


@router.post("/volumes/{volume_id}/chapter-plan", response_model=ChapterPlanReceipt, status_code=201, summary="生成并保存分卷的章节规划")
async def write_chapter_plan(volume_id: RowId, payload: ChapterPlanRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_outline_volume", volume_id, user.id)
    if payload.start_chapter + payload.chapter_count - 1 > 2**32 - 1:
        raise HTTPException(422, "章节规划范围超出允许的章号")
    project_id = await run_in_threadpool(task_project, engine, "novel_outline_volume", volume_id, user.id)
    try:
        next_number = await run_in_threadpool(next_free_chapter_number, engine, project_id)
    except Exception:
        next_number = None
    context = await load_context(plan_context, volume_id, payload.start_chapter, payload.chapter_count, next_number)
    context["foreshadows"] = await run_in_threadpool(foreshadow_db.list_for_volume, engine, volume_id)
    artifact = await generate(agents.PLAN, context, payload)
    if len(artifact.chapters) != payload.chapter_count:
        raise HTTPException(502, "模型返回的章节数量与请求不符")
    emit_generation_event("status", message="章节规划校验通过，正在保存…")
    return await run_in_threadpool(save_chapter_plan, engine, context["project"]["id"], volume_id, payload.start_chapter, artifact)


async def auto_select_prose_techniques(engine, user, context, payload):
    try:
        rows = await run_in_threadpool(analysis_db.list_techniques, engine, user.id)
    except Exception:
        logger.warning("正文技法库读取失败；本次不注入参考技法", exc_info=True)
        return []
    candidates = [row for row in rows if row.get("category") == "prose"][:80]
    if not candidates:
        return []
    selection_context = {
        "chapter": context.get("chapter"),
        "volume": context.get("volume"),
        "plot_beats": context.get("plot_beats"),
        "characters": context.get("characters"),
        "available_techniques": [{
            "id": row["id"], "name": row.get("name", ""),
            "dimension_label": row.get("dimension_label", ""),
            "summary": (row.get("summary") or "")[:500],
            "reuse": (row.get("reuse") or "")[:400],
        } for row in candidates],
    }
    try:
        selected = await generate(agents.TECHNIQUE_SELECTOR, selection_context,
                                  WritingInput(model=payload.model))
        valid = {row["id"] for row in candidates}
        ids = list(dict.fromkeys(int(value) for value in selected.technique_ids
                                 if int(value) in valid))[:5]
    except Exception:
        logger.warning("AI 正文技法选配失败；本次不注入参考技法", exc_info=True)
        return []
    if not ids:
        return []
    chosen = await run_in_threadpool(analysis_db.get_techniques, engine, user.id, ids)
    emit_generation_event("status", message=f"AI 已按章节规划选择 {len(chosen)} 条参考技法。")
    return chosen


async def attach_chapter_characters(engine, chapter_id, context):
    try:
        ids = set(await run_in_threadpool(chapter_cast_db.ids_for_chapter, engine, chapter_id))
    except Exception:
        logger.warning("章节出场角色读取失败；使用分卷角色资料继续", exc_info=True)
        ids = set()
    context["chapter_characters"] = [
        row for row in context.get("characters", [])
        if (row.get("id") if isinstance(row, dict) else getattr(row, "id", None)) in ids
    ]
    return context


@router.post("/chapters/{chapter_id}/draft", response_model=DraftReceipt, summary="生成并保存章节正文初稿")
async def write_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    context = await load_context(chapter_context, chapter_id, False)
    context = await attach_chapter_characters(engine, chapter_id, context)
    selected_techniques = await auto_select_prose_techniques(engine, user, context, payload)
    technique_payload = payload.model_copy(update={
        "technique_ids": [row["id"] for row in selected_techniques]})
    context = await apply_techniques(context, technique_payload, user, engine, PROSE_TECHNIQUE_CATEGORIES)
    artifact = await generate(agents.DRAFT, context, payload)
    emit_generation_event("status", message="正文生成完成，正在保存正文；人物进展请另行启动分析任务…")
    # Legacy combined artifacts may still be returned by an older caller. New
    # body agents use ChapterContentDraft and never produce these updates.
    return await run_in_threadpool(save_draft, engine, chapter_id, artifact.content, context["chapter"],
                                   getattr(artifact, "character_updates", ()))


def validate_batch_chapters(engine, project_id, chapter_ids, account_id):
    """Check and snapshot all chapters before a serial batch starts."""
    require_resource(engine, "novel_project", project_id, account_id)
    chapter = tables["novel_chapter"]
    with transaction(engine) as connection:
        rows = {}
        json_columns = legacy_json_columns(connection, chapter.name)
        for chapter_id in chapter_ids:
            row = response_row(get_row(connection, chapter, chapter_id), json_columns)
            if row["project_id"] != project_id:
                raise HTTPException(404, "所选章节不属于当前小说")
            if row["status"] != "planned" or row["content"].strip():
                raise HTTPException(409, f"第 {row['chapter_number']} 章已有正文或不是待写作状态")
            rows[chapter_id] = row
    return sorted(rows.values(), key=lambda row: (row["chapter_number"], row["id"]))


async def write_batch_drafts(project_id: RowId, payload: BatchDraftRequest,
                             user: CurrentAccount, engine: Database):
    chapters = await run_in_threadpool(validate_batch_chapters, engine, project_id,
                                       payload.chapter_ids, user.id)
    drafts, completed, warnings = [], [], []
    for index, chapter in enumerate(chapters, 1):
        emit_generation_event("status", message=f"正在生成第 {chapter['chapter_number']} 章（{index}/{len(chapters)}）…")
        draft = await write_draft(chapter["id"], DraftRequest(
            target_characters=payload.target_characters, instruction=payload.instruction,
            model=payload.model, technique_ids=payload.technique_ids),
            user, engine)
        drafts.append((chapter, draft))

    emit_generation_event("status", message="所选章节正文已全部生成，正在统一分析并更新人物进展档案…")
    for chapter, draft in drafts:
        try:
            progress = await analyze_character_progress(
                chapter["id"], WritingInput(instruction=payload.instruction, model=payload.model), user, engine)
        except HTTPException as exc:
            # 正文已保存，人物进展失败不应让整批任务作废；保留正文并继续后续章节。
            progress_count = 0
            warnings.append(f"第 {chapter['chapter_number']} 章：{exc.detail}")
            emit_generation_event("status", message=f"第 {chapter['chapter_number']} 章正文已保存，人物进展未保存：{exc.detail}")
        else:
            progress_count = progress.updated_count
            if progress.warning:
                warnings.append(f"第 {chapter['chapter_number']} 章：{progress.warning}")
            emit_generation_event("status", message=f"已更新第 {chapter['chapter_number']} 章的人物进展档案。")
        completed.append(BatchDraftChapterReceipt(
            chapter_id=chapter["id"], chapter_number=chapter["chapter_number"], title=chapter["title"],
            word_count=draft.word_count, progress_count=progress_count))
    return BatchDraftReceipt(project_id=project_id, completed_count=len(completed), chapters=completed,
                             warning="；".join(warnings))


@router.post("/projects/{project_id}/chapters/batch-draft", response_model=BatchDraftReceipt,
             summary="按章节顺序批量生成正文并分析人物进展")
async def write_batch_draft(project_id: RowId, payload: BatchDraftRequest,
                            user: CurrentAccount, engine: Database):
    await run_in_threadpool(validate_batch_chapters, engine, project_id, payload.chapter_ids, user.id)
    return await write_batch_drafts(project_id, payload, user, engine)


@router.post("/outlines/{outline_id}/rewrite", response_model=OutlineReceipt, status_code=201, summary="重写大纲并保存为新版本")
async def rewrite_outline(outline_id: RowId, payload: OutlineRequest, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_outline", outline_id, user.id)
    volume_number = payload.volume_number or await run_in_threadpool(outline_volume_number, engine, outline_id)
    context, sources = await load_context(character_context, project_id, outline_id)
    context["volume_plan"] = {"volume_number": volume_number,
                              "theme": payload.volume_theme.strip() or payload.instruction.strip()}
    context["outline_plan"] = {
        "target_word_min": payload.target_word_min,
        "target_word_max": payload.target_word_max,
        "plot_progression": payload.plot_progression,
        "min_foreshadows": payload.min_foreshadows,
        "foreshadow_categories": {key: FORESHADOW_CATEGORY_LABELS[key] for key in payload.foreshadow_categories},
    }
    context = await apply_techniques(context, payload, user, engine, OUTLINE_TECHNIQUE_CATEGORIES)
    artifact = await generate(agents.OUTLINE_REWRITE, context, payload)
    if len(artifact.volumes) != 1:
        raise HTTPException(502, "每次只重写一卷大纲，模型返回了多个分卷，重写结果未保存")
    validate_outline_foreshadows(artifact, payload)
    emit_generation_event("status", message="单卷大纲校验通过，正在保存新版本与伏笔…")
    receipt = await run_in_threadpool(save_outline, engine, project_id, artifact, sources, volume_number,
                                      payload.model_dump(include={
                                          "min_foreshadows", "foreshadow_categories", "target_word_min",
                                          "target_word_max", "plot_progression"}))
    try:
        await run_in_threadpool(foreshadow_db.apply_outline_selection, engine, project_id,
                                payload.consume_foreshadow_ids, payload.plant_foreshadow_ids,
                                receipt.outline_id, receipt.volume_ids[0] if receipt.volume_ids else None)
    except Exception:
        logger.warning("重写大纲伏笔消费/预埋保存失败；大纲已保存", exc_info=True)
    return receipt


@router.post("/chapters/{chapter_id}/rewrite", response_model=DraftReceipt, summary="重写章节正文并保存为草稿")
async def rewrite_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    context = await load_context(chapter_context, chapter_id, False, True)
    context = await attach_chapter_characters(engine, chapter_id, context)
    context = await apply_techniques(context, payload, user, engine, PROSE_TECHNIQUE_CATEGORIES)
    artifact = await generate(agents.DRAFT_REWRITE, context, payload)
    emit_generation_event("status", message="重写正文生成完成，正在替换正文；人物进展请另行启动分析任务…")
    return await run_in_threadpool(save_rewritten_draft, engine, chapter_id, artifact.content, context["chapter"],
                                   getattr(artifact, "character_updates", ()))


@router.post("/chapters/{chapter_id}/polish", response_model=DraftReceipt, summary="润色章节正文并保存为草稿")
async def polish_chapter(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    return await polish_draft(chapter_id, payload, user, engine)


@router.post("/chapters/{chapter_id}/continue", response_model=DraftReceipt, summary="续写章节并追加到原正文")
async def continue_chapter(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    return await continue_draft(chapter_id, payload, user, engine)


async def polish_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    context = await load_context(chapter_context, chapter_id, False, True)
    context = await attach_chapter_characters(engine, chapter_id, context)
    context = await apply_techniques(context, payload, user, engine, PROSE_TECHNIQUE_CATEGORIES)
    artifact = await generate(agents.DRAFT_POLISH, context, payload)
    emit_generation_event("status", message="正文润色完成，正在核对原文并保存…")
    return await run_in_threadpool(save_rewritten_draft, engine, chapter_id, artifact.content,
                                   context["chapter"], getattr(artifact, "character_updates", ()))


async def continue_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    context = await load_context(chapter_context, chapter_id, False, True)
    context = await attach_chapter_characters(engine, chapter_id, context)
    context = await apply_techniques(context, payload, user, engine, PROSE_TECHNIQUE_CATEGORIES)
    artifact = await generate(agents.DRAFT_CONTINUE, context, payload)
    emit_generation_event("status", message="续写完成，正在核对章节原文并追加保存…")
    return await run_in_threadpool(save_continued_draft, engine, chapter_id, artifact.content,
                                   context["chapter"])


@router.post("/chapters/{chapter_id}/character-progress", response_model=CharacterProgressReceipt,
             summary="分析已保存章节中的人物正文进展")
async def analyze_character_progress(chapter_id: RowId, payload: WritingInput, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    context = await load_context(character_progress_context, chapter_id)
    artifact = await generate(agents.CHARACTER_PROGRESS, context, payload)
    emit_generation_event("status", message="人物进展分析完成，正在校验原文依据并保存…")
    return await run_in_threadpool(save_character_progress, engine, chapter_id, context["chapter"],
                                   artifact.character_updates)


@router.post("/outlines/{outline_id}/rewrite/stream", summary="实时重写大纲")
async def stream_rewrite_outline(outline_id: RowId, payload: OutlineRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_outline", outline_id, user.id)
    return generation_response(lambda: rewrite_outline(outline_id, payload, user, engine))


@router.post("/chapters/{chapter_id}/rewrite/stream", summary="实时重写正文")
async def stream_rewrite_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    return generation_response(lambda: rewrite_draft(chapter_id, payload, user, engine))


@router.post("/chapters/{chapter_id}/polish/stream", summary="实时润色章节正文")
async def stream_polish_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    return generation_response(lambda: polish_draft(chapter_id, payload, user, engine))


@router.post("/chapters/{chapter_id}/continue/stream", summary="实时续写章节正文并追加保存")
async def stream_continue_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    return generation_response(lambda: continue_draft(chapter_id, payload, user, engine))


@router.post("/chapters/{chapter_id}/review", response_model=ReviewReport, summary="审读章节正文并返回修改建议")
async def review_chapter(chapter_id: RowId, payload: WritingInput, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    context = await load_context(chapter_context, chapter_id, True)
    return await generate(agents.REVIEW, context, payload)


@router.post("/projects/{project_id}/outline/stream", summary="实时生成并保存小说大纲")
async def stream_outline(project_id: RowId, payload: OutlineRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    return generation_response(lambda: write_outline(project_id, payload, user, engine))


@router.post("/volumes/{volume_id}/chapter-plan/stream", summary="实时生成并保存章节规划")
async def stream_chapter_plan(volume_id: RowId, payload: ChapterPlanRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_outline_volume", volume_id, user.id)
    return generation_response(lambda: write_chapter_plan(volume_id, payload, user, engine))


@router.post("/chapters/{chapter_id}/draft/stream", summary="实时生成并保存章节正文")
async def stream_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    return generation_response(lambda: write_draft(chapter_id, payload, user, engine))


@router.post("/projects/{project_id}/chapters/batch-draft/stream", summary="实时批量生成章节正文")
async def stream_batch_draft(project_id: RowId, payload: BatchDraftRequest,
                             user: CurrentAccount, engine: Database):
    await run_in_threadpool(validate_batch_chapters, engine, project_id, payload.chapter_ids, user.id)
    return generation_response(lambda: write_batch_drafts(project_id, payload, user, engine))


@router.post("/chapters/{chapter_id}/review/stream", summary="实时审读章节正文")
async def stream_review(chapter_id: RowId, payload: WritingInput, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    return generation_response(lambda: review_chapter(chapter_id, payload, user, engine))


@router.post("/chapters/{chapter_id}/character-progress/stream", summary="实时分析章节人物正文进展")
async def stream_character_progress(chapter_id: RowId, payload: WritingInput, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    return generation_response(lambda: analyze_character_progress(chapter_id, payload, user, engine))


def task_project(engine, table_name, record_id, account_id):
    require_resource(engine, table_name, record_id, account_id)
    relation = tables[table_name]
    with transaction(engine) as connection:
        if table_name == "novel_project":
            return record_id
        if table_name == "novel_outline_volume":
            outline = tables["novel_outline"]
            return connection.scalar(select(outline.c.project_id).select_from(
                relation.join(outline, relation.c.outline_id == outline.c.id)).where(relation.c.id == record_id))
        return connection.scalar(select(relation.c.project_id).where(relation.c.id == record_id))


@router.post("/projects/{project_id}/outline/tasks", status_code=202, summary="后台生成大纲")
async def start_outline(project_id: RowId, payload: OutlineRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"outline:{project_id}", kind="outline",
        title="生成单卷大纲", project_id=project_id, resource_id=project_id,
        operation=lambda: write_outline(project_id, payload, user, engine)).summary()


@router.post("/volumes/{volume_id}/chapter-plan/tasks", status_code=202, summary="后台规划章节")
async def start_plan(volume_id: RowId, payload: ChapterPlanRequest, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_outline_volume", volume_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"plan:{volume_id}", kind="plan",
        title="生成章节规划", project_id=project_id, resource_id=volume_id,
        operation=lambda: write_chapter_plan(volume_id, payload, user, engine)).summary()


@router.post("/chapters/{chapter_id}/draft/tasks", status_code=202, summary="后台生成正文")
async def start_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_chapter", chapter_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"draft:{chapter_id}", kind="draft",
        title="生成章节正文", project_id=project_id, resource_id=chapter_id,
        operation=lambda: write_draft(chapter_id, payload, user, engine)).summary()


@router.post("/projects/{project_id}/chapters/batch-draft/tasks", status_code=202,
             summary="后台按顺序批量生成章节正文并分析人物进展")
async def start_batch_draft(project_id: RowId, payload: BatchDraftRequest,
                            user: CurrentAccount, engine: Database):
    await run_in_threadpool(validate_batch_chapters, engine, project_id, payload.chapter_ids, user.id)
    return generation_jobs.store.start(
        account_id=user.id, key=f"batch-draft:{project_id}", kind="batch_draft",
        title="批量生成章节正文", project_id=project_id, resource_id=project_id,
        operation=lambda: write_batch_drafts(project_id, payload, user, engine)).summary()


@router.post("/outlines/{outline_id}/rewrite/tasks", status_code=202, summary="后台重写大纲")
async def start_rewrite_outline(outline_id: RowId, payload: OutlineRequest, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_outline", outline_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"outline:{project_id}", kind="outline",
        title="重写小说大纲", project_id=project_id, resource_id=outline_id,
        operation=lambda: rewrite_outline(outline_id, payload, user, engine)).summary()


@router.post("/chapters/{chapter_id}/rewrite/tasks", status_code=202, summary="后台重写正文")
async def start_rewrite_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_chapter", chapter_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"draft:{chapter_id}", kind="draft",
        title="重写章节正文", project_id=project_id, resource_id=chapter_id,
        operation=lambda: rewrite_draft(chapter_id, payload, user, engine)).summary()


@router.post("/chapters/{chapter_id}/polish/tasks", status_code=202, summary="后台润色正文")
async def start_polish_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_chapter", chapter_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"polish:{chapter_id}", kind="draft",
        title="润色章节正文", project_id=project_id, resource_id=chapter_id,
        operation=lambda: polish_draft(chapter_id, payload, user, engine)).summary()


@router.post("/chapters/{chapter_id}/continue/tasks", status_code=202, summary="后台续写正文并追加保存")
async def start_continue_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_chapter", chapter_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"continue:{chapter_id}", kind="draft",
        title="续写章节正文", project_id=project_id, resource_id=chapter_id,
        operation=lambda: continue_draft(chapter_id, payload, user, engine)).summary()


@router.post("/chapters/{chapter_id}/review/tasks", status_code=202, summary="后台审读正文")
async def start_review(chapter_id: RowId, payload: WritingInput, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_chapter", chapter_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"review:{chapter_id}", kind="review",
        title="审读章节正文", project_id=project_id, resource_id=chapter_id,
        operation=lambda: review_chapter(chapter_id, payload, user, engine)).summary()


@router.post("/chapters/{chapter_id}/character-progress/tasks", status_code=202,
             summary="后台分析章节人物正文进展")
async def start_character_progress(chapter_id: RowId, payload: WritingInput, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_chapter", chapter_id, user.id)
    with transaction(engine) as connection:
        chapter = response_row(get_row(connection, tables["novel_chapter"], chapter_id),
                               legacy_json_columns(connection, "novel_chapter"))
    if not chapter["content"].strip():
        raise HTTPException(409, "该章节还没有正文，无法分析人物进展")
    return generation_jobs.store.start(account_id=user.id, key=f"character-progress:{chapter_id}", kind="character_progress",
        title="分析本章人物进展", project_id=project_id, resource_id=chapter_id,
        operation=lambda: analyze_character_progress(chapter_id, payload, user, engine)).summary()
