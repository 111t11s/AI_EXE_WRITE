"""On-demand, read-only consistency checks over explicitly selected chapters."""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from api import generation_jobs
from api.account_api import CurrentAccount
from api.database import Database, transaction
from api.novel_schemas import RowId
from api.novel_tables import tables
from api.novel_writing_api import character_context, generate, load_context, read
from api.ownership import require_resource
from api.writing_schemas import ConsistencyReceipt, ConsistencyRequest


router = APIRouter(prefix="/writing", tags=["小说一致性核对"])
MAX_CHAPTER_TEXT = 120000
MAX_CONTEXT = 200000


def require_selection(engine, project_id, payload, account_id):
    require_resource(engine, "novel_project", project_id, account_id)
    outline, chapter = tables["novel_outline"], tables["novel_chapter"]
    with transaction(engine) as connection:
        if connection.scalar(select(outline.c.project_id).where(outline.c.id == payload.outline_id)) != project_id:
            raise HTTPException(404, "所选大纲不存在或不属于当前小说")
        rows = connection.execute(select(chapter.c.id, chapter.c.content).where(
            chapter.c.project_id == project_id, chapter.c.id.in_(payload.chapter_ids))).mappings().all()
        if len(rows) != len(payload.chapter_ids):
            raise HTTPException(404, "所选章节不存在或不属于当前小说")
        validate_bodies(rows)


def validate_bodies(chapters):
    if any(not row["content"].strip() for row in chapters):
        raise HTTPException(409, "所选章节中有尚无正文的章节，请先完成正文或取消勾选")
    if sum(len(row["content"]) for row in chapters) > MAX_CHAPTER_TEXT:
        raise HTTPException(422, "所选正文超过单次核对上限 120000 字符，请分批选择章节")


async def consistency_context(session, project_id, payload):
    context, _ = await character_context(session, project_id, payload.outline_id)
    if not context["characters"]:
        raise HTTPException(409, "请先建立人物档案，再进行一致性核对")
    chapters = []
    for chapter_id in payload.chapter_ids:
        row = await read(session, "chapter", chapter_id)
        if row["project_id"] != project_id:
            raise HTTPException(404, "所选章节不存在或不属于当前小说")
        chapters.append({key: row[key] for key in ("id", "chapter_number", "title", "summary", "content", "volume_id")})
    chapters.sort(key=lambda row: row["chapter_number"])
    validate_bodies(chapters)
    notes = ["只核对所选章节，未选章节未读取正文；结论基于任务读取时的资料快照，不代表全书评价。"]
    if any(right["chapter_number"] != left["chapter_number"] + 1 for left, right in zip(chapters, chapters[1:])):
        notes.append("选择的章号不连续，间隔章节中的事件未知，不据此认定因果缺失或人物突变。")
    volumes = {row["id"] for row in context["volumes"]}
    if any(row["volume_id"] not in volumes for row in chapters):
        notes.append("部分章节未绑定所选大纲的分卷；本次以用户明确选择的大纲版本为对照基准。")
    context.update(selected_chapters=chapters, scope_notes=notes)
    if len(json.dumps(context, ensure_ascii=False, default=str)) > MAX_CONTEXT:
        raise HTTPException(422, "章节及设定资料超过单次核对容量，请减少所选章节或精简资料")
    return context


def validate_report(report, context):
    chapters = {row["id"]: row for row in context["selected_chapters"]}
    references = {}
    for kind, rows in (("outline", [context["outline"]]), ("world", [context["world"]]),
                       ("character", context["characters"]), ("volume", context["volumes"]),
                       ("plot_beat", [beat for volume in context["volumes"] for beat in volume["beats"]]),
                       ("chapter", context["selected_chapters"])):
        references.update({(kind, row["id"]): row for row in rows})
    error = HTTPException(502, "核对结果包含无效的章节引用、对照依据或结论，请重试核对")
    if (report.verdict == "consistent" and report.issues) or (report.verdict == "drifted" and not report.issues):
        raise error
    used = {}
    for issue in report.issues:
        for evidence in issue.evidence:
            chapter = chapters.get(evidence.chapter_id)
            if chapter is None or evidence.quote not in chapter["content"]:
                raise error
        basis = issue.basis
        row = references.get((basis.kind, basis.record_id))
        value = row.get(basis.field) if row is not None else None
        if not isinstance(value, str) or basis.quote not in value:
            raise error
        label = row.get("name") or row.get("title") or f"大纲 v{context['outline']['version_no']}"
        used[(basis.kind, basis.record_id)] = dict(kind=basis.kind, record_id=basis.record_id, label=label)
    return list(used.values())


@router.post("/projects/{project_id}/consistency", response_model=ConsistencyReceipt, summary="核对所选章节的一致性")
async def check_consistency(project_id: RowId, payload: ConsistencyRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_selection, engine, project_id, payload, user.id)
    emit_generation_event("status", message="正在通过 MCP 读取所选章节、大纲与人物档案…")
    context = await load_context(consistency_context, project_id, payload)
    checked_at = datetime.now(timezone.utc).isoformat()
    report = await generate(agents.CONSISTENCY, context, payload)
    emit_generation_event("status", message="核对完成，正在校验章节原文及对照依据…")
    references = validate_report(report, context)
    values = report.model_dump()
    values["limitations"] = list(dict.fromkeys([*context["scope_notes"], *report.limitations]))
    return ConsistencyReceipt(**values, project_id=project_id, outline_id=payload.outline_id,
        outline_version=context["outline"]["version_no"], checked_at=checked_at,
        chapters=[dict(chapter_id=row["id"], chapter_number=row["chapter_number"], title=row["title"])
                  for row in context["selected_chapters"]], references=references)


@router.post("/projects/{project_id}/consistency/tasks", status_code=202, summary="后台核对所选章节的一致性")
async def start_consistency(project_id: RowId, payload: ConsistencyRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_selection, engine, project_id, payload, user.id)
    selection = {**payload.model_dump(), "chapter_ids": sorted(payload.chapter_ids)}
    signature = hashlib.sha256(json.dumps(selection, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return generation_jobs.store.start(account_id=user.id, key=f"consistency:{project_id}:{signature}", kind="consistency",
        title=f"一致性核对 · {len(payload.chapter_ids)} 章", project_id=project_id, resource_id=payload.outline_id,
        operation=lambda: check_consistency(project_id, payload, user, engine)).summary()
