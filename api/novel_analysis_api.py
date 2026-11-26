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
from api.document_topics_api import _read_uploads
from api.novel_writing_api import generate
from api.trends_api import retryable
from api.writing_schemas import (NovelAnalysisDimension, NovelAnalysisReceipt, NovelAnalysisReport,
                                 NovelAnalysisTechnique, WritingInput)
from core.app_logging import get_logger
from db import novel_analysis as analysis_db


router = APIRouter(prefix="/novel-analysis", tags=["小说拆解"])
logger = get_logger("novel_analysis")
DIMENSIONS = ("character", "profession", "world", "plot", "prose")
DIMENSION_LABELS = {"character": "人物构造", "profession": "职介构造", "world": "世界构造",
                    "plot": "剧情构造", "prose": "文笔写法"}
DEFAULT_INSTRUCTION = "只分析本次上传的文本，提取有原文证据的可复用写法；不要臆造未写出的设定。"
MAX_TECHNIQUES = 12


def clean_analysis(report: NovelAnalysisReport, documents: list[dict]) -> NovelAnalysisReport | None:
    """证据逐字核对：没有原文支撑的技法直接丢弃，材料不足的维度保留为不可分析。"""
    corpus = "\n".join(row["text"] for row in documents)
    compact = re.sub(r"\s+", "", corpus)
    dimensions, seen = [], set()
    total = 0
    for dimension in report.dimensions:
        name = dimension.dimension
        if name not in DIMENSIONS or name in seen:
            continue
        seen.add(name)
        techniques: list[NovelAnalysisTechnique] = []
        for technique in dimension.techniques:
            if total >= MAX_TECHNIQUES:
                break
            evidence = [item for item in technique.evidence
                        if item.quote.strip() and re.sub(r"\s+", "", item.quote) in compact]
            if not evidence:
                continue
            techniques.append(technique.model_copy(update={
                "dimension": name, "evidence": evidence[:4]}))
            total += 1
        available = bool(dimension.available and techniques)
        missing = dimension.missing
        if dimension.available and not techniques:
            missing = missing or "文本中没有足够证据提炼该维度技法"
        dimensions.append(NovelAnalysisDimension(
            dimension=name, available=available,
            summary=dimension.summary if available else "",
            techniques=techniques if available else [],
            missing="" if available else (missing or "本次文本未覆盖该维度"),
        ))
    for name in DIMENSIONS:
        if name not in seen:
            dimensions.append(NovelAnalysisDimension(
                dimension=name, available=False, missing="本次文本未覆盖该维度"))
    dimensions.sort(key=lambda item: DIMENSIONS.index(item.dimension))
    if total == 0:
        return None
    return report.model_copy(update={"dimensions": dimensions})


async def analyze_documents(documents: list[dict], instruction: str, model: str | None,
                            account_id: int, engine) -> NovelAnalysisReceipt:
    emit_generation_event("status", message=f"已读取 {len(documents)} 个文件，正在拆解人物、世界、剧情与文笔…")
    context = {"documents": documents}
    request = WritingInput(instruction=instruction or DEFAULT_INSTRUCTION, model=model)
    for attempt in (1, 2):
        try:
            report = await generate(agents.NOVEL_ANALYST, context, request)
        except HTTPException as exc:
            if attempt == 1 and retryable(exc):
                emit_generation_event("status", message="拆解结果未通过校验，正在重新分析…")
                continue
            raise
        cleaned = clean_analysis(report, documents)
        if cleaned is not None:
            receipt = NovelAnalysisReceipt(
                **cleaned.model_dump(),
                file_names=[row["name"] for row in documents],
                generated_at=datetime.now(timezone.utc).isoformat())
            try:
                emit_generation_event("status", message="拆解完成，正在保存技法库…")
                record_id = await run_in_threadpool(
                    analysis_db.save_report, engine, account_id,
                    [row["name"] for row in documents], sum(row["chars"] for row in documents),
                    instruction, receipt.model_dump(mode="json"))
                receipt = receipt.model_copy(update={"record_id": record_id})
                emit_generation_event("status", message="拆解结果已保存到技法库。")
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
    return {"items": await run_in_threadpool(analysis_db.list_techniques, engine, user.id),
            "dimensions": DIMENSION_LABELS}


@router.get("/{report_id}", summary="查看拆解报告")
async def get_analysis(report_id: int, user: CurrentAccount, engine: Database):
    report = await run_in_threadpool(analysis_db.get_report, engine, user.id, report_id)
    if report is None:
        raise HTTPException(404, "拆解记录不存在")
    return report


@router.post("/analyze/tasks", status_code=202, summary="拆解上传的小说章节并提取技法")
async def start_analysis(
        user: CurrentAccount, engine: Database,
        files: list[UploadFile] = File(...),
        instruction: str = Form(DEFAULT_INSTRUCTION),
        model: str | None = Form(None)):
    documents = await run_in_threadpool(_read_uploads, files)
    if not documents:
        raise HTTPException(422, "没有读取到可分析的文本")
    instruction = instruction.strip()[:2000]
    signature = hashlib.sha256(json.dumps({
        "names": [row["name"] for row in documents],
        "chars": [row["chars"] for row in documents],
        "instruction": instruction,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return generation_jobs.store.start(
        account_id=user.id, key=f"novel-analysis:{signature}", kind="analysis",
        title=f"小说拆解 · {len(documents)} 个文件",
        operation=lambda: analyze_documents(documents, instruction, model, user.id, engine)).summary()
