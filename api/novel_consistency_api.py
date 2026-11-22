"""Consistency checks and explicitly adopted repairs of cited chapters."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from api import generation_jobs
from api.account_api import CurrentAccount
from api.database import Database, transaction
from api.novel_schemas import RowId
from api.novel_crud_api import get_row, response_row, storage_values
from api.character_progress import replace_progress
from api.novel_tables import tables
from api.novel_writing_api import all_context_rows, character_context, generate, load_context, read
from api.ownership import require_resource
from api.writing_schemas import (ConsistencyReceipt, ConsistencyRepairRequest, ConsistencyRequest,
                                 ConsistencySuggestion, WritingInput)
from db.connection import legacy_json_columns
from core.logging import get_logger


router = APIRouter(prefix="/writing", tags=["小说一致性核对"])
logger = get_logger("novel_consistency")
MAX_CHAPTER_TEXT = 120000
MAX_CONTEXT = 200000
CHAPTER_FIELDS = ("id", "project_id", "volume_id", "chapter_number", "title", "summary", "content", "status")


def chapter_fingerprint(chapter):
    value = {key: chapter[key] for key in CHAPTER_FIELDS}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

# Only story fields can establish a discrepancy; IDs, timestamps and workflow
# status strings are not evidence of a narrative contradiction.
BASIS_FIELDS = {
    "project": {"setting", "tone", "premise", "protagonist_goal"},
    "outline": {"summary", "central_conflict", "themes"},
    "volume": {"summary", "protagonist_goal", "main_conflict", "climax", "ending_hook"},
    "plot_beat": {"title", "summary", "foreshadowing"},
    "character": {"name", "role", "summary", "personality", "background", "goal", "motivation", "weakness", "character_arc", "abilities", "status"},
    "character_relationship": {"relationship_type", "description"},
    "volume_character": {"scenes"},
    "world": {"summary", "era", "history", "rules", "cultures"},
    "region": {"name", "description", "climate", "resources", "locations"},
    "power_system": {"name", "source", "rules", "limitations", "growth_rules"},
    "power_rank": {"name", "description", "breakthrough_condition", "capabilities"},
    "cheat_system": {"name", "cheat_type", "origin", "functions", "limitations", "costs", "growth_path", "story_secret"},
    "faction": {"name", "faction_type", "summary", "goal", "leader", "territory", "strengths", "weaknesses", "relationships"},
    "chapter": {"summary", "content"},
}


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
    character_ids = {row["id"] for row in context["characters"]}
    context["character_relationships"] = []
    for character_id in sorted(character_ids):
        relationships = await all_context_rows(session, "character_relationship", source_character_id=character_id)
        context["character_relationships"].extend(
            row for row in relationships if row["target_character_id"] in character_ids)
    chapters = []
    for chapter_id in payload.chapter_ids:
        row = await read(session, "chapter", chapter_id)
        if row["project_id"] != project_id:
            raise HTTPException(404, "所选章节不存在或不属于当前小说")
        chapters.append({key: row[key] for key in CHAPTER_FIELDS})
    chapters.sort(key=lambda row: row["chapter_number"])
    validate_bodies(chapters)
    notes = ["只核对所选章节，未选章节未读取正文；结论基于任务读取时的资料快照，不代表全书评价。",
             "以已保存的大纲、人物及相关档案为准；资料没有明确约束或缺少对应时期信息时，不凭常识、类型套路或个人偏好认定偏移。"]
    if not context["character_relationships"]:
        notes.append("尚无人物关系档案；人物关系仅依据已提供的档案和正文，未记载的关系不作推断。")
    if any(right["chapter_number"] != left["chapter_number"] + 1 for left, right in zip(chapters, chapters[1:])):
        notes.append("选择的章号不连续，间隔章节中的事件未知，不据此认定因果缺失或人物突变。")
    volumes = {row["id"] for row in context["volumes"]}
    if any(row["volume_id"] not in volumes for row in chapters):
        notes.append("部分章节未绑定所选大纲的分卷；本次以用户明确选择的大纲版本为对照基准。")
    context.update(selected_chapters=chapters, scope_notes=notes)
    if len(json.dumps(context, ensure_ascii=False, default=str)) > MAX_CONTEXT:
        raise HTTPException(422, "章节及设定资料超过单次核对容量，请减少所选章节或精简资料")
    return context


def reference_records(context):
    """Use exactly the MCP snapshot passed to the model for citation checks."""
    references = {}
    for kind, rows in (("project", [context["project"]]), ("outline", [context["outline"]]), ("world", [context["world"]]),
                       ("character", context["characters"]), ("volume", context["volumes"]),
                       ("character_relationship", context["character_relationships"]),
                       ("volume_character", [row for volume in context["volumes"] for row in volume["cast"]]),
                       ("plot_beat", [beat for volume in context["volumes"] for beat in volume["beats"]]),
                       *((kind, context.get(key, [])) for kind, key in (
                           ("region", "region"), ("power_system", "power_system"), ("power_rank", "power_ranks"),
                           ("cheat_system", "cheat_system"), ("faction", "faction"))),
                       ("chapter", context["selected_chapters"])):
        references.update({(kind, row["id"]): row for row in rows})
    return references


def reference_label(kind, row, context):
    characters = {person["id"]: person["name"] for person in context["characters"]}
    if kind == "character_relationship":
        return f"{characters[row['source_character_id']]} → {characters[row['target_character_id']]}"
    if kind == "volume_character":
        volume = next(volume for volume in context["volumes"] if volume["id"] == row["volume_id"])
        return f"{characters.get(row['character_id'], '未找到人物')} · 第 {volume['volume_number']} 卷 · {volume['title']}"
    return row.get("name") or row.get("title") or f"大纲 v{context['outline']['version_no']}"


def issue_problem(issue, chapters, references):
    """返回该问题引文校验失败的原因；通过校验时返回 None。"""
    for evidence in issue.evidence:
        chapter = chapters.get(evidence.chapter_id)
        if chapter is None:
            return f"引用了未选择的章节 {evidence.chapter_id}"
        if evidence.quote not in chapter["content"]:
            return "章节引文无法逐字匹配"
    basis = issue.basis
    if basis.field not in BASIS_FIELDS[basis.kind]:
        return f"不允许引用字段 {basis.field}"
    row = references.get((basis.kind, basis.record_id))
    value = row.get(basis.field) if row is not None else None
    if not isinstance(value, str) or basis.quote not in value:
        return "对照依据引文无法逐字匹配"
    if basis.kind == "chapter" and (
            issue.category != "continuity" or any(evidence.chapter_id == basis.record_id for evidence in issue.evidence)):
        return "章节不能作为本章自身的对照依据"
    return None


def validate_report(report, context):
    """逐条校验问题引文，丢弃无法核对的问题，避免一条坏引文作废整次核对。"""
    chapters = {row["id"]: row for row in context["selected_chapters"]}
    references = reference_records(context)
    used, valid, dropped = {}, [], 0
    for index, issue in enumerate(report.issues):
        problem = issue_problem(issue, chapters, references)
        if problem:
            dropped += 1
            logger.warning("核对问题未通过引用校验：序号=%s，标题=%s，原因=%s",
                           index + 1, issue.title, problem)
            continue
        valid.append(issue)
        basis = issue.basis
        row = references[(basis.kind, basis.record_id)]
        used[(basis.kind, basis.record_id)] = dict(
            kind=basis.kind, record_id=basis.record_id, label=reference_label(basis.kind, row, context))

    verdict = report.verdict
    limitations = list(report.limitations)
    if verdict == "consistent" and valid:
        verdict = "drifted"
        limitations.append("模型结论与所列问题不一致，已按可核对的问题判定为发现偏移。")
    if verdict == "drifted" and not valid:
        verdict = "insufficient"
        limitations.append("模型报告的问题依据无法在本次资料中逐字核对，未能确认偏移。")
    if dropped:
        limitations.append(f"另有 {dropped} 条问题因章节或档案引文无法逐字核对已忽略。")
    cleaned = report.model_copy(update={
        "verdict": verdict, "issues": valid, "limitations": list(dict.fromkeys(limitations))})
    return cleaned, list(used.values())


@router.post("/projects/{project_id}/consistency", response_model=ConsistencyReceipt, summary="核对所选章节的一致性")
async def check_consistency(project_id: RowId, payload: ConsistencyRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_selection, engine, project_id, payload, user.id)
    emit_generation_event("status", message="正在通过 MCP 读取所选章节、大纲与人物档案…")
    context = await load_context(consistency_context, project_id, payload)
    materials = [dict(kind=kind, record_id=record_id, label=reference_label(kind, row, context))
                 for (kind, record_id), row in reference_records(context).items() if kind != "chapter"]
    emit_generation_event("status", message=f"已读取大纲 v{context['outline']['version_no']}、{len(context['volumes'])} 卷、"
        f"{len(context['characters'])} 份人物档案、{len(context['character_relationships'])} 份关系档案及相关设定，正在逐项对照原文…")
    checked_at = datetime.now(timezone.utc).isoformat()
    report = await generate(agents.CONSISTENCY, context, payload)
    emit_generation_event("status", message="核对完成，正在校验章节原文及对照依据…")
    report, references = validate_report(report, context)
    values = report.model_dump()
    values["limitations"] = list(dict.fromkeys([*context["scope_notes"], *report.limitations]))
    return ConsistencyReceipt(**values, project_id=project_id, outline_id=payload.outline_id,
        outline_version=context["outline"]["version_no"], checked_at=checked_at,
        chapters=[dict(chapter_id=row["id"], chapter_number=row["chapter_number"], title=row["title"])
                  for row in context["selected_chapters"]], references=references, reference_materials=materials,
        chapter_fingerprints={str(row["id"]): chapter_fingerprint(row) for row in context["selected_chapters"]})


@router.post("/projects/{project_id}/consistency/tasks", status_code=202, summary="后台核对所选章节的一致性")
async def start_consistency(project_id: RowId, payload: ConsistencyRequest, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_selection, engine, project_id, payload, user.id)
    selection = {**payload.model_dump(), "chapter_ids": sorted(payload.chapter_ids)}
    signature = hashlib.sha256(json.dumps(selection, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    async def operation():
        report = await check_consistency(project_id, payload, user, engine)
        report.job_id = job.id
        return report

    job = generation_jobs.store.start(account_id=user.id, key=f"consistency:{project_id}:{signature}", kind="consistency",
        title=f"一致性核对 · {len(payload.chapter_ids)} 章", project_id=project_id, resource_id=payload.outline_id,
        operation=operation)
    return job.summary()


def report_issue(project_id, job_id, issue_index, account_id):
    job = generation_jobs.store.owned(job_id, account_id)
    if job.kind != "consistency" or job.project_id != project_id or job.status != "completed" or not job.result:
        raise HTTPException(404, "一致性核对报告不存在或尚未完成")
    issues = job.result["issues"]
    if not 0 <= issue_index < len(issues):
        raise HTTPException(404, "修复意见不存在")
    return job.result, issues[issue_index]


def require_editable_issue(issue):
    if issue.get("repair_status") in {"running", "completed"}:
        raise HTTPException(409, "该意见已采纳，请查看修复进度；修复后如需继续调整，请重新核对")


@router.patch("/projects/{project_id}/consistency/{job_id}/issues/{issue_index}", summary="保存编辑后的修复意见")
async def edit_suggestion(project_id: RowId, job_id: str, issue_index: int, payload: ConsistencySuggestion,
                          user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    _, issue = report_issue(project_id, job_id, issue_index, user.id)
    require_editable_issue(issue)
    issue["suggestion"] = payload.suggestion
    return issue


def validate_repair_chapters(engine, project_id, report, account_id, lock=False, connection=None):
    """Compare with the checked snapshot, including chapters used for continuity."""
    if connection is None:
        require_resource(engine, "novel_project", project_id, account_id)
        with transaction(engine) as connection:
            return validate_repair_chapters(engine, project_id, report, account_id, lock, connection)
    relation = tables["novel_chapter"]
    json_columns = legacy_json_columns(connection, relation.name)
    rows = []
    for entry in sorted(report["chapters"], key=lambda row: row["chapter_id"]):
        row = response_row(get_row(connection, relation, entry["chapter_id"], lock=lock), json_columns)
        if row["project_id"] != project_id:
            raise HTTPException(404, "对应章节不存在或不属于当前小说")
        if report.get("chapter_fingerprints", {}).get(str(row["id"])) != chapter_fingerprint(row):
            raise HTTPException(409, "核对后章节已发生变化或报告缺少正文快照，请重新核对后采纳修复意见")
        rows.append(row)
    return rows


def save_repairs(engine, project_id, report, generated, account_id):
    require_resource(engine, "novel_project", project_id, account_id)
    with transaction(engine) as connection:
        # Lock and validate every source before writing, so multi-chapter repairs
        # either all save or leave every chapter unchanged.
        rows = validate_repair_chapters(engine, project_id, report, account_id, True, connection)
        relation = tables["novel_chapter"]
        json_columns = legacy_json_columns(connection, relation.name)
        completed, fingerprints = [], {}
        for row in rows:
            if row["id"] not in generated:
                continue
            content = generated[row["id"]]
            count = sum(not letter.isspace() for letter in content)
            replace_progress(connection, row, content, ())
            values = storage_values(dict(content=content, word_count=count, status="draft"), json_columns)
            connection.execute(update(relation).where(relation.c.id == row["id"]).values(**values))
            completed.append(dict(chapter_id=row["id"], chapter_number=row["chapter_number"], title=row["title"], word_count=count))
            fingerprints[str(row["id"])] = chapter_fingerprint({**row, "content": content, "status": "draft"})
    return dict(project_id=project_id, completed_count=len(completed), chapters=completed), fingerprints


async def repair_issue(engine, project_id, report, approved, user, model=None):
    await run_in_threadpool(validate_repair_chapters, engine, project_id, report, user.id)
    payload = ConsistencyRequest(outline_id=report["outline_id"], chapter_ids=[row["chapter_id"] for row in report["chapters"]])
    emit_generation_event("status", message="正在读取核对范围的正文与档案，按采纳的意见修复…")
    context = await load_context(consistency_context, project_id, payload)
    for row in context["selected_chapters"]:
        if report["chapter_fingerprints"].get(str(row["id"])) != chapter_fingerprint(row):
            raise HTTPException(409, "核对后正文发生变化，请重新核对")
    chapters = {row["id"]: row for row in context["selected_chapters"]}
    references = reference_records(context)
    basis = approved["basis"]
    source = references.get((basis["kind"], basis["record_id"]), {}).get(basis["field"])
    if (any(entry["quote"] not in chapters[entry["chapter_id"]]["content"] for entry in approved["evidence"])
            or not isinstance(source, str) or basis["quote"] not in source):
        raise HTTPException(409, "该意见的正文或档案依据已变化，请重新核对后修复")
    targets = {entry["chapter_id"] for entry in approved["evidence"]}
    generated = {}
    for row in context["selected_chapters"]:
        if row["id"] not in targets:
            continue
        emit_generation_event("status", message=f"正在修复第 {row['chapter_number']} 章…")
        artifact = await generate(agents.CONSISTENCY_REPAIR, {**context, "chapter": row, "approved_fix": approved},
                                  WritingInput(model=model))
        generated[row["id"]] = artifact.content
    emit_generation_event("status", message="修复完成，正在校验并保存对应章节…")
    return await run_in_threadpool(save_repairs, engine, project_id, report, generated, user.id)


@router.post("/projects/{project_id}/consistency/{job_id}/repairs/tasks", status_code=202,
             summary="批量采纳修复意见并后台修复对应章节")
async def start_bulk_repair(project_id: RowId, job_id: str, payload: ConsistencyRepairRequest,
                            user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    report, _ = report_issue(project_id, job_id, 0, user.id)
    issues = report["issues"]
    indexes = sorted(set(payload.issue_indexes))
    if any(index < 0 or index >= len(issues) for index in indexes):
        raise HTTPException(404, "修复意见不存在")
    targets = set()
    for index in indexes:
        require_editable_issue(issues[index])
        targets.update(entry["chapter_id"] for entry in issues[index]["evidence"])
    rows = await run_in_threadpool(validate_repair_chapters, engine, project_id, report, user.id)
    if any(len(row["content"]) > 30000 for row in rows if row["id"] in targets):
        raise HTTPException(422, "对应章节超过单次修复上限 30000 字符，请精简后重新核对")
    repair_report = deepcopy(report)
    job_ref = {}

    async def operation():
        completed, results = {}, []
        for index in indexes:
            issue = report["issues"][index]
            approved = {key: value for key, value in issue.items() if not key.startswith("repair_")}
            try:
                receipt, fingerprints = await repair_issue(engine, project_id, repair_report, approved, user,
                                                           payload.model)
            except Exception as exc:
                issue["repair_status"] = "failed"
                results.append({"issue_index": index, "status": "failed",
                                "message": exc.detail if isinstance(exc, HTTPException) else "修复过程异常"})
                continue
            issue["repair_status"] = "completed"
            issue["repair_job_id"] = job_ref["job"].id
            # 后续问题以本次修复后的正文为新的核对快照。
            repair_report["chapter_fingerprints"].update(fingerprints)
            for row in receipt["chapters"]:
                completed[row["chapter_id"]] = row
            results.append({"issue_index": index, "status": "completed", "chapters": receipt["chapters"]})
        return {"project_id": project_id, "completed_count": len(completed),
                "chapters": list(completed.values()), "issues": results,
                "failed_count": sum(1 for row in results if row["status"] == "failed")}

    signature = hashlib.sha256(json.dumps(indexes).encode()).hexdigest()[:16]
    job = generation_jobs.store.start(
        account_id=user.id, key=f"consistency-repairs:{job_id}:{signature}", kind="consistency_repair",
        title=f"一致性修复 · {len(indexes)} 个问题", project_id=project_id,
        resource_id=issues[indexes[0]]["evidence"][0]["chapter_id"], operation=operation)
    job_ref["job"] = job
    for index in indexes:
        issues[index].update(repair_job_id=job.id, repair_status="running")
    return job.summary()


@router.post("/projects/{project_id}/consistency/{job_id}/issues/{issue_index}/repair/tasks", status_code=202,
             summary="采纳修复意见并后台修复对应章节")
async def start_repair(project_id: RowId, job_id: str, issue_index: int, payload: ConsistencySuggestion,
                       user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    report, issue = report_issue(project_id, job_id, issue_index, user.id)
    if issue.get("repair_status") == "running" and issue["suggestion"] == payload.suggestion:
        return generation_jobs.store.owned(issue["repair_job_id"], user.id).summary()
    require_editable_issue(issue)
    rows = await run_in_threadpool(validate_repair_chapters, engine, project_id, report, user.id)
    targets = {entry["chapter_id"] for entry in issue["evidence"]}
    if any(len(row["content"]) > 30000 for row in rows if row["id"] in targets):
        raise HTTPException(422, "对应章节超过单次修复上限 30000 字符，请精简后重新核对")
    # Recheck after database IO, before mutating the process-local report.
    require_editable_issue(issue)
    repair_report = deepcopy(report)
    approved = {key: value for key, value in issue.items() if not key.startswith("repair_")}
    approved["suggestion"] = payload.suggestion

    async def operation():
        try:
            receipt, fingerprints = await repair_issue(engine, project_id, repair_report, approved, user, payload.model)
        except Exception:
            issue["repair_status"] = "failed"
            raise
        issue["repair_status"] = "completed"
        # Allow the other suggestions to be adopted against our own saved
        # revisions. Each running task keeps its independent snapshot so a
        # concurrent repair cannot overwrite an earlier repair.
        report["chapter_fingerprints"].update(fingerprints)
        return receipt

    repair = generation_jobs.store.start(account_id=user.id, key=f"consistency-repair:{job_id}:{issue_index}",
        kind="consistency_repair", title=f"一致性修复 · {issue['title']}", project_id=project_id,
        resource_id=issue["evidence"][0]["chapter_id"], operation=operation)
    issue.update(suggestion=payload.suggestion, repair_job_id=repair.id, repair_status="running")
    return repair.summary()
