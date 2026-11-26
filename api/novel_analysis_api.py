"""小说章节拆解：读取上传文本，按五个维度提取可复用技法并入库。"""

import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from api import generation_jobs
from api.account_api import CurrentAccount
from api.database import Database
from api.document_topics_api import MAX_TEXT_CHARS, MAX_TOTAL_CHARS, _read_uploads
from api.novel_writing_api import generate
from api.trends_api import retryable
from api.writing_schemas import (NovelAnalysisCandidate, NovelAnalysisDimension, NovelAnalysisReceipt,
                                 NovelAnalysisReport, NovelAnalysisTechnique, WritingInput)
from core.app_logging import get_logger
from db import novel_analysis as analysis_db


router = APIRouter(prefix="/novel-analysis", tags=["小说拆解"])
logger = get_logger("novel_analysis")
FIXED_DIMENSIONS = ("character", "profession", "world", "plot", "prose")
DIMENSION_LABELS = {"character": "人物构造", "profession": "职介构造", "world": "世界构造",
                    "plot": "剧情构造", "prose": "文笔写法"}
DEFAULT_INSTRUCTION = "只分析本次上传的文本，提取有原文证据的可复用写法；不要臆造未写出的设定。"
MAX_TECHNIQUES = 12


def _valid_evidence(items, compact: str):
    return [item for item in items
            if item.quote.strip() and re.sub(r"\s+", "", item.quote) in compact][:4]


def clean_analysis(report: NovelAnalysisReport, documents: list[dict],
                   extension_dimensions: list[dict] | None = None) -> NovelAnalysisReport | None:
    """核对证据；固定五维之外的写法进入候选，不混入已批准技法。"""
    corpus = "\n".join(row["text"] for row in documents)
    compact = re.sub(r"\s+", "", corpus)
    extension_labels = {row["dimension_key"]: row["label"] for row in (extension_dimensions or [])}
    labels = {**DIMENSION_LABELS, **extension_labels}
    known = set(labels)
    dimensions: list[NovelAnalysisDimension] = []
    by_key: dict[str, NovelAnalysisDimension] = {}
    seen: set[str] = set()
    extras: list[NovelAnalysisCandidate] = []
    total = 0

    for dimension in report.dimensions:
        key = (dimension.dimension or "").strip()[:40]
        if not key or key in seen:
            continue
        if key not in known:
            for technique in dimension.techniques:
                evidence = _valid_evidence(technique.evidence, compact)
                if not evidence:
                    continue
                extras.append(NovelAnalysisCandidate(
                    dimension=key, dimension_label=(dimension.label or key)[:40],
                    dimension_reason=(dimension.missing or "模型识别到不属于固定五维的写法")[:600],
                    name=technique.name, summary=technique.summary,
                    mechanism=technique.mechanism, reuse=technique.reuse,
                    evidence=evidence, confidence=technique.confidence,
                    applicable_genres=technique.applicable_genres))
            continue
        seen.add(key)
        techniques = []
        for technique in dimension.techniques:
            if total >= MAX_TECHNIQUES:
                break
            evidence = _valid_evidence(technique.evidence, compact)
            if not evidence:
                continue
            techniques.append(technique.model_copy(update={
                "dimension": key, "dimension_label": labels.get(key, ""), "evidence": evidence}))
            total += 1
        available = bool(dimension.available and techniques)
        missing = dimension.missing
        if dimension.available and not techniques:
            missing = missing or "文本中没有足够证据提炼该维度技法"
        item = NovelAnalysisDimension(
            dimension=key, label=labels.get(key, ""), available=available,
            summary=dimension.summary if available else "",
            techniques=techniques if available else [],
            missing="" if available else (missing or "本次文本未覆盖该维度"))
        dimensions.append(item)
        by_key[key] = item

    for candidate in report.extra_techniques:
        key = (candidate.dimension or "").strip()[:40]
        evidence = _valid_evidence(candidate.evidence, compact)
        if not key or not evidence:
            continue
        label = (candidate.dimension_label or labels.get(key) or key)[:40]
        if key in known:
            technique = NovelAnalysisTechnique(
                dimension=key, dimension_label=labels.get(key, label), name=candidate.name,
                summary=candidate.summary, mechanism=candidate.mechanism, reuse=candidate.reuse,
                evidence=evidence, confidence=candidate.confidence,
                applicable_genres=candidate.applicable_genres)
            dimension = by_key.get(key)
            if dimension is None:
                dimension = NovelAnalysisDimension(dimension=key, label=labels.get(key, label),
                                                   available=True, techniques=[], missing="")
                dimensions.append(dimension)
                by_key[key] = dimension
                seen.add(key)
            if len(dimension.techniques) < 4 and total < MAX_TECHNIQUES:
                dimension.techniques.append(technique)
                dimension.available = True
                total += 1
            continue
        extras.append(candidate.model_copy(update={
            "dimension": key, "dimension_label": label, "evidence": evidence}))

    for name in FIXED_DIMENSIONS:
        if name not in seen:
            item = NovelAnalysisDimension(dimension=name, label=labels.get(name, ""),
                                          available=False, missing="本次文本未覆盖该维度")
            dimensions.append(item)
            by_key[name] = item

    extras = extras[:5]
    if total == 0 and not extras:
        return None
    order = {key: index for index, key in enumerate(FIXED_DIMENSIONS)}
    dimensions.sort(key=lambda item: order.get(item.dimension, len(order)))
    return report.model_copy(update={"dimensions": dimensions, "extra_techniques": extras})


async def analyze_documents(documents: list[dict], instruction: str, model: str | None,
                            account_id: int, engine) -> NovelAnalysisReceipt:
    emit_generation_event("status", message=f"已读取 {len(documents)} 个文件，正在拆解人物、世界、剧情与文笔…")
    extensions = await run_in_threadpool(analysis_db.list_dimensions, engine, account_id)
    context = {"documents": documents, "extension_dimensions": extensions}
    request = WritingInput(instruction=instruction or DEFAULT_INSTRUCTION, model=model)
    for attempt in (1, 2):
        try:
            report = await generate(agents.NOVEL_ANALYST, context, request)
        except HTTPException as exc:
            if attempt == 1 and retryable(exc):
                emit_generation_event("status", message="拆解结果未通过校验，正在重新分析…")
                continue
            raise
        cleaned = clean_analysis(report, documents, extensions)
        if cleaned is not None:
            receipt = NovelAnalysisReceipt(
                **cleaned.model_dump(),
                file_names=[row["name"] for row in documents],
                generated_at=datetime.now(timezone.utc).isoformat())
            try:
                emit_generation_event("status", message="拆解完成，正在保存技法库与待审核候选…")
                labels = {**DIMENSION_LABELS, **{row["dimension_key"]: row["label"] for row in extensions}}
                record_id = await run_in_threadpool(
                    analysis_db.save_analysis, engine, account_id,
                    [row["name"] for row in documents], sum(row["chars"] for row in documents),
                    instruction, receipt.model_dump(mode="json"), set(FIXED_DIMENSIONS), labels)
                receipt = receipt.model_copy(update={"record_id": record_id})
                extra = len(receipt.extra_techniques)
                emit_generation_event("status", message=(
                    f"拆解结果已保存；{extra} 条新维度技法待审核。" if extra else "拆解结果已保存到技法库。"))
            except Exception as exc:
                logger.warning("拆解结果保存失败：异常类型=%s", type(exc).__name__)
                emit_generation_event("status", message="拆解完成，但保存到技法库失败；请检查数据库迁移。")
            return receipt
        if attempt == 1:
            emit_generation_event("status", message="没有找到可核实的技法，正在重新拆解…")
    raise HTTPException(502, "没有从文本中提取到可核实的技法，请补充章节或调整要求")


@router.get("", summary="查询拆解历史")
async def list_analyses(user: CurrentAccount, engine: Database,
                        limit: int = Query(default=20, ge=1, le=50),
                        offset: int = Query(default=0, ge=0)):
    return await run_in_threadpool(analysis_db.list_reports, engine, user.id, limit, offset)


@router.get("/techniques", summary="查询写作技法库")
async def list_techniques(user: CurrentAccount, engine: Database):
    extensions = await run_in_threadpool(analysis_db.list_dimensions, engine, user.id)
    labels = {**DIMENSION_LABELS, **{row["dimension_key"]: row["label"] for row in extensions}}
    return {"items": await run_in_threadpool(analysis_db.list_techniques, engine, user.id),
            "dimensions": labels, "categories": analysis_db.CATEGORY_LABELS}


@router.get("/candidates", summary="查询待审核的新维度技法")
async def list_candidates(user: CurrentAccount, engine: Database,
                          status: str = Query(default="pending")):
    if status not in {"pending", "approved", "rejected"}:
        raise HTTPException(422, "审核状态不支持")
    return {"items": await run_in_threadpool(analysis_db.list_candidates, engine, user.id, status)}


@router.post("/candidates/{candidate_id}/approve", summary="通过新维度技法并归入知识库分类")
async def approve_candidate(candidate_id: int, user: CurrentAccount, engine: Database,
                            category: str = Query(default="outline")):
    if category not in {"worldbuilding", "outline", "prose"}:
        raise HTTPException(422, "技法分类不支持")
    result = await run_in_threadpool(analysis_db.approve_candidate, engine, user.id, candidate_id,
                                     user.id, category)
    if result is None:
        raise HTTPException(404, "候选技法不存在或已审核")
    return {"approved": True, **result}


@router.post("/candidates/{candidate_id}/reject", summary="拒绝新维度技法")
async def reject_candidate(candidate_id: int, user: CurrentAccount, engine: Database):
    ok = await run_in_threadpool(analysis_db.reject_candidate, engine, user.id, candidate_id, user.id)
    if not ok:
        raise HTTPException(404, "候选技法不存在或已审核")
    return {"rejected": True, "candidate_id": candidate_id}


@router.get("/{report_id}", summary="查看拆解报告")
async def get_analysis(report_id: int, user: CurrentAccount, engine: Database):
    report = await run_in_threadpool(analysis_db.get_report, engine, user.id, report_id)
    if report is None:
        raise HTTPException(404, "拆解记录不存在")
    return report


@router.post("/analyze/tasks", status_code=202, summary="拆解上传的小说章节并提取技法")
async def start_analysis(
        user: CurrentAccount, engine: Database,
        files: list[UploadFile] | None = File(default=None),
        text: str = Form(""),
        instruction: str = Form(DEFAULT_INSTRUCTION),
        model: str | None = Form(None)):
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(422, f"粘贴章节不能超过 {MAX_TEXT_CHARS} 字符")
    documents = await run_in_threadpool(_read_uploads, files or [])
    text = text.strip()
    added_text = False
    if text:
        remaining = MAX_TOTAL_CHARS - sum(len(row["text"]) for row in documents)
        if remaining > 0:
            take = min(len(text), MAX_TEXT_CHARS, remaining)
            documents.append({"name": "粘贴文本", "chars": len(text), "text": text[:take],
                              "truncated": take < len(text)})
            added_text = True
    if not documents:
        raise HTTPException(422, "请上传章节文件或粘贴一段章节文本")
    instruction = instruction.strip()[:2000]
    signature = hashlib.sha256(json.dumps({
        "names": [row["name"] for row in documents],
        "chars": [row["chars"] for row in documents],
        "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16] if text else "",
        "instruction": instruction,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    file_count = sum(1 for row in documents if row["name"] != "粘贴文本")
    title = ("小说拆解 · 文件与粘贴文本" if file_count and added_text else
             "小说拆解 · 粘贴文本" if added_text else f"小说拆解 · {file_count} 个文件")
    return generation_jobs.store.start(
        account_id=user.id, key=f"novel-analysis:{signature}", kind="analysis",
        title=title,
        operation=lambda: analyze_documents(documents, instruction, model, user.id, engine)).summary()
