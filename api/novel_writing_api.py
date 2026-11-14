"""Coordinate writing agents with scoped MCP context and validated saves."""

import httpx
from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from api.generation_stream import generation_response
from api.account_api import CurrentAccount
from api.ownership import require_resource
from agents.mcp.worldbuilding import NovelToolError, call_novel_tool, novel_session
from api.novel_crud_api import Database
from api.novel_schemas import RowId
from api.novel_writing_store import require_empty_plan, save_chapter_plan, save_draft, save_outline
from api.writing_schemas import (
    ChapterPlanReceipt, ChapterPlanRequest, DraftReceipt, DraftRequest, OutlineReceipt, OutlineRequest,
    ReviewReport, WritingInput,
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


async def project_context(session, project_id):
    project = await read(session, "project", project_id)
    worlds = await listing(session, "world", project_id=project_id, is_current=True, limit=2)
    if len(worlds) != 1:
        raise HTTPException(409, "请先为小说项目设置唯一的当前世界观")
    characters = await listing(session, "character", project_id=project_id, limit=12)
    return {
        "project": excerpt(project, "id", "title", "genre", "setting", "tone", "target_length", "premise", "protagonist_goal"),
        "world": excerpt(worlds[0], "id", "title", "summary", "era", "history", "rules", "cultures", limit=2400),
        "characters": [excerpt(item, "name", "role", "summary", "goal", limit=300) for item in characters],
    }


async def volume_context(session, volume_id):
    volume = await read(session, "outline_volume", volume_id)
    outline = await read(session, "outline", volume["outline_id"])
    context = await project_context(session, outline["project_id"])
    context["outline"] = excerpt(outline, "id", "summary", "central_conflict", "themes")
    context["volume"] = excerpt(volume, "id", "title", "summary", "protagonist_goal", "main_conflict", "climax", "ending_hook")
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
