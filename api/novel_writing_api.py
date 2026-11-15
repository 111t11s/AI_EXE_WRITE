"""Coordinate writing agents with scoped MCP context and validated saves."""

import json
from collections import Counter

import httpx
from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from agents.outline_skills import OutlineSkillError
from api.generation_stream import generation_response
from api import generation_jobs
from api.account_api import CurrentAccount
from api.ownership import require_resource
from agents.mcp.worldbuilding import NovelToolError, call_novel_tool, novel_session
from api.novel_crud_api import Database
from api.novel_schemas import RowId
from api.novel_writing_store import require_empty_plan, save_chapter_plan, save_draft, save_outline
from api.novel_writing_store import save_characters
from api.novel_tables import tables
from api.database import transaction
from sqlalchemy import select
from api.writing_schemas import (
    ChapterPlanReceipt, ChapterPlanRequest, DraftReceipt, DraftRequest, OutlineReceipt, OutlineRequest,
    ReviewReport, WritingInput,
    CoreCharacterRequest, SupportingCharacterRequest, CharacterReceipt,
)
from core.logging import get_logger


router = APIRouter(prefix="/writing", tags=["小说写作智能体"])
logger = get_logger("novel_writing")


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
        "characters": [excerpt(item, "id", "name", "role", "summary", "personality", "goal", "motivation",
                               "weakness", "abilities", "character_arc", limit=300) for item in characters],
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
    context["project"] = await read(session, "project", project_id)
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


async def create_characters(project_id, outline_id, payload, user, engine):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    if outline_id is not None:
        await run_in_threadpool(require_resource, engine, "novel_outline", outline_id, user.id)
    emit_generation_event("status", message="正在通过 MCP 读取世界观、已有角色和剧情资料…")
    context, sources = await load_context(character_context, project_id, outline_id)
    core = outline_id is None
    volume_id = None if core else payload.volume_id
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
    expected = {"protagonist": payload.protagonist_count, "important": payload.important_count} if core else {
        "supporting": payload.supporting_count}
    if +counts != +Counter(expected):
        raise HTTPException(502, "模型返回的角色数量或层级与请求不符，本批次未保存")
    returned = [item.character_id for item in artifact.reused_characters]
    if Counter(returned) != Counter(selected):
        raise HTTPException(502, "模型返回的复用角色与勾选不符，本批次未保存")
    emit_generation_event("status", message="角色设定校验通过，正在整批保存角色…")
    return await run_in_threadpool(save_characters, engine, project_id, artifact, sources, volume_id, legacy_assignments)


@router.post("/projects/{project_id}/characters/core", response_model=CharacterReceipt, status_code=201,
             summary="根据世界观生成主角和重要配角")
async def write_core_characters(project_id: RowId, payload: CoreCharacterRequest, user: CurrentAccount, engine: Database):
    return await create_characters(project_id, None, payload, user, engine)


@router.post("/outlines/{outline_id}/characters/supporting", response_model=CharacterReceipt, status_code=201,
             summary="根据大纲生成其余配角")
async def write_supporting_characters(outline_id: RowId, payload: SupportingCharacterRequest,
                                      user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_outline", outline_id, user.id)
    return await create_characters(project_id, outline_id, payload, user, engine)


@router.post("/projects/{project_id}/characters/core/tasks", status_code=202, summary="后台生成主角和重要配角")
async def start_core_characters(project_id: RowId, payload: CoreCharacterRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"characters:core:{project_id}", kind="characters",
        title="根据世界观生成主角与重要配角", project_id=project_id, resource_id=project_id,
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
    context["volume"] = excerpt(volume, "id", "volume_number", "title", "summary", "protagonist_goal", "main_conflict", "climax", "ending_hook")
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


async def plan_context(session, volume_id, start):
    context = await volume_context(session, volume_id)
    project_id = context["project"]["id"]
    if await listing(session, "chapter", project_id=project_id, chapter_number=start, limit=1):
        raise HTTPException(409, "起始章号已使用，请选择未使用的章号")
    context["previous_chapter"] = await previous_chapter(session, project_id, start)
    return context


async def chapter_context(session, chapter_id, review):
    chapter = await read(session, "chapter", chapter_id)
    if review and not chapter["content"].strip():
        raise HTTPException(409, "该章节还没有可审读的正文")
    if not review:
        require_empty_plan(chapter)
    if chapter["volume_id"] is not None:
        context = await volume_context(session, chapter["volume_id"])
    else:
        context = await project_context(session, chapter["project_id"])
    if not review and not context["characters"]:
        raise HTTPException(409, "尚未生成角色信息，请先在角色页面设置数量并生成角色，再生成正文")
    if len(chapter["summary"]) > 8000:
        raise HTTPException(422, "章节摘要超过单次写作上限 8000 字符")
    context["chapter"] = {key: chapter[key] for key in ("id", "chapter_number", "title", "summary")}
    context["previous_chapter"] = await previous_chapter(session, chapter["project_id"], chapter["chapter_number"])
    if review:
        if len(chapter["content"]) > 30000:
            raise HTTPException(422, "正文超过单次审读上限 30000 字符")
        context["chapter"]["content"] = chapter["content"]
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


async def generate(role, context, payload):
    try:
        return await run_in_threadpool(agents.generate, role, {**context, "request": payload.model_dump()})
    except OutlineSkillError as exc:
        raise HTTPException(503, str(exc)) from None
    except (APITimeoutError, httpx.TimeoutException):
        logger.warning("模型请求超时：任务=%s", role.title)
        raise HTTPException(504, "模型响应超时，请稍后重试") from None
    except (APIConnectionError, httpx.RequestError):
        logger.warning("模型网关连接失败：任务=%s", role.title)
        raise HTTPException(502, "无法连接模型网关，请检查网络或模型服务配置") from None
    except APIStatusError as exc:
        logger.warning("模型网关请求失败：任务=%s，状态码=%s", role.title, exc.status_code)
        if exc.status_code in (401, 403):
            detail = "模型网关拒绝了访问，请检查 API 密钥和模型权限"
        elif exc.status_code == 429:
            detail = "模型网关限制了请求，请稍后重试或检查额度"
        elif exc.status_code >= 500:
            detail = "模型网关暂时不可用，请稍后重试"
        else:
            detail = "模型网关拒绝了生成请求，请检查模型和服务地址配置"
        raise HTTPException(502, detail) from None
    except ValidationError:
        logger.warning("模型输出未通过内容校验：任务=%s", role.title)
        raise HTTPException(502, "模型已返回内容，但格式未通过校验，请重试生成") from None
    except Exception as exc:
        logger.warning("写作智能体生成失败：任务=%s，异常类型=%s", role.title, type(exc).__name__)
        raise HTTPException(502, "模型生成失败或未返回有效的小说内容") from None


@router.post("/projects/{project_id}/outline", response_model=OutlineReceipt, status_code=201, summary="生成并保存小说大纲")
async def write_outline(project_id: RowId, payload: OutlineRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    context = await load_context(project_context, project_id)
    artifact = await generate(agents.OUTLINE, context, payload)
    if len(artifact.volumes) != payload.volume_count:
        raise HTTPException(502, "模型返回的分卷数量与请求不符")
    emit_generation_event("status", message="大纲校验通过，正在保存大纲与分卷…")
    return await run_in_threadpool(save_outline, engine, project_id, artifact)


@router.post("/volumes/{volume_id}/chapter-plan", response_model=ChapterPlanReceipt, status_code=201, summary="生成并保存分卷的章节规划")
async def write_chapter_plan(volume_id: RowId, payload: ChapterPlanRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_outline_volume", volume_id, user.id)
    if payload.start_chapter + payload.chapter_count - 1 > 2**32 - 1:
        raise HTTPException(422, "章节规划范围超出允许的章号")
    context = await load_context(plan_context, volume_id, payload.start_chapter)
    artifact = await generate(agents.PLAN, context, payload)
    if len(artifact.chapters) != payload.chapter_count:
        raise HTTPException(502, "模型返回的章节数量与请求不符")
    emit_generation_event("status", message="章节规划校验通过，正在保存…")
    return await run_in_threadpool(save_chapter_plan, engine, context["project"]["id"], volume_id, payload.start_chapter, artifact)


@router.post("/chapters/{chapter_id}/draft", response_model=DraftReceipt, summary="生成并保存章节正文初稿")
async def write_draft(chapter_id: RowId, payload: DraftRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    context = await load_context(chapter_context, chapter_id, False)
    artifact = await generate(agents.DRAFT, context, payload)
    emit_generation_event("status", message="正文校验通过，正在保存初稿…")
    return await run_in_threadpool(save_draft, engine, chapter_id, artifact.content, context["chapter"])


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


@router.post("/chapters/{chapter_id}/review/stream", summary="实时审读章节正文")
async def stream_review(chapter_id: RowId, payload: WritingInput, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_chapter", chapter_id, user.id)
    return generation_response(lambda: review_chapter(chapter_id, payload, user, engine))


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
        title="生成小说大纲", project_id=project_id, resource_id=project_id,
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


@router.post("/chapters/{chapter_id}/review/tasks", status_code=202, summary="后台审读正文")
async def start_review(chapter_id: RowId, payload: WritingInput, user: CurrentAccount, engine: Database):
    project_id = await run_in_threadpool(task_project, engine, "novel_chapter", chapter_id, user.id)
    return generation_jobs.store.start(account_id=user.id, key=f"review:{chapter_id}", kind="review",
        title="审读章节正文", project_id=project_id, resource_id=chapter_id,
        operation=lambda: review_chapter(chapter_id, payload, user, engine)).summary()
