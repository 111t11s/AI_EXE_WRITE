"""文件选题：读取用户上传的 TXT/Markdown/DOCX 文本并生成原创题材。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from api import generation_jobs
from api.account_api import CurrentAccount
from api.database import Database
from api.novel_writing_api import generate
from api.trends_api import clean_report, retryable
from api.writing_schemas import SettingExpansionReceipt, TrendReceipt, WritingInput
from core.app_logging import get_logger
from db import document_topics as topic_db
from db import setting_expansion as setting_db


router = APIRouter(prefix="/document-topics", tags=["文件选题"])
logger = get_logger("document_topics")
ALLOWED_SUFFIXES = {".txt", ".md", ".markdown", ".docx"}
MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 25000
MAX_DOC_CHARS = 25000
MAX_TOTAL_CHARS = 60000
DEFAULT_DOCUMENT_INSTRUCTION = "根据上传文本提炼可开写的原创题材；不要照搬原文、原有人物或情节。"
DEFAULT_EXPANSION_INSTRUCTION = "以粘贴或上传的文本为已定设定，只补充世界观、力量体系、职介、社会和剧情细节；不要重新拆题或替换核心概念。"
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    elif data.startswith(b"\xef\xbb\xbf"):
        encodings = ("utf-8-sig",)
    else:
        encodings = ("utf-8", "gb18030")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ValueError("DOCX 文件无法解析") from exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("DOCX 文件内容损坏") from exc
    lines = []
    for paragraph in root.iter(f"{WORD_NS}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{WORD_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{WORD_NS}tab":
                parts.append("\t")
            elif node.tag in (f"{WORD_NS}br", f"{WORD_NS}cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _normalize(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_document(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.casefold()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"不支持的文件格式：{suffix or name}")
    if suffix == ".docx":
        text = _docx_text(data)
    else:
        text = _decode_text(data)
    text = _normalize(text)
    if not text:
        raise ValueError(f"文件没有可读取的文字：{name}")
    return text


def _read_uploads(files: list[UploadFile]) -> list[dict]:
    if len(files) > MAX_FILES:
        raise HTTPException(422, f"一次最多上传 {MAX_FILES} 个文件")
    documents, total = [], 0
    for upload in files:
        name = Path(upload.filename or "未命名文本").name
        suffix = Path(name).suffix.casefold()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(415, f"不支持的文件格式：{suffix or name}，请上传 TXT、Markdown 或 DOCX")
        data = upload.file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"文件超过 10MB：{name}")
        try:
            text = parse_document(name, data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        take = min(len(text), MAX_DOC_CHARS, MAX_TOTAL_CHARS - total)
        if take <= 0:
            break
        documents.append({"name": name, "chars": len(text), "text": text[:take],
                          "truncated": take < len(text)})
        total += take
        if total >= MAX_TOTAL_CHARS:
            break
    return documents


def _source_type(documents: list[dict]) -> str:
    has_text = any(row["name"] == "粘贴文本" for row in documents)
    has_files = any(row["name"] != "粘贴文本" for row in documents)
    if has_text and has_files:
        return "mixed"
    return "text" if has_text else "files"


async def _collect_materials(files: list[UploadFile] | None, text: str):
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(422, f"粘贴文本不能超过 {MAX_TEXT_CHARS} 字符")
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
        raise HTTPException(422, "请上传文件或粘贴一段文本")
    return documents, text, added_text


async def expand_setting(documents: list[dict], instruction: str, model: str | None,
                         account_id: int, engine) -> SettingExpansionReceipt:
    emit_generation_event("status", message="已读取已有设定，正在沿原有核心概念补充细节…")
    context = {"documents": documents}
    request = WritingInput(instruction=instruction or DEFAULT_EXPANSION_INSTRUCTION, model=model)
    for attempt in (1, 2):
        try:
            report = await generate(agents.SETTING_EXPANDER, context, request)
        except HTTPException as exc:
            if attempt == 1 and retryable(exc):
                emit_generation_event("status", message="设定补全结果未通过校验，正在重新补全…")
                continue
            raise
        receipt = SettingExpansionReceipt(
            **report.model_dump(),
            file_names=[row["name"] for row in documents],
            generated_at=datetime.now(timezone.utc).isoformat())
        try:
            emit_generation_event("status", message="补全完成，正在保存设定稿…")
            record_id = await run_in_threadpool(
                setting_db.save_report, engine, account_id,
                [row["name"] for row in documents], sum(row["chars"] for row in documents),
                instruction, receipt.model_dump(mode="json"))
            receipt = receipt.model_copy(update={"record_id": record_id})
            emit_generation_event("status", message="设定补全稿已保存。")
        except Exception as exc:
            logger.warning("设定补全保存失败：异常类型=%s", type(exc).__name__)
            emit_generation_event("status", message="补全完成，但保存失败；请检查数据库迁移。")
        return receipt
    raise HTTPException(502, "设定补全结果未通过校验，请重试")


async def analyze_documents(documents: list[dict], instruction: str, genre_preference: str,
                            audience: str, idea_count: int, model: str | None,
                            account_id: int, engine) -> TrendReceipt:
    emit_generation_event("status", message=f"已读取 {len(documents)} 段材料，正在分析文本并提炼题材…")
    context = {
        "documents": documents,
        "preferences": {"genre_preference": genre_preference, "audience": audience,
                        "idea_count": idea_count},
    }
    request = WritingInput(instruction=instruction or DEFAULT_DOCUMENT_INSTRUCTION, model=model)
    for attempt in (1, 2):
        try:
            report = await generate(agents.DOCUMENT_ANALYST, context, request)
        except HTTPException as exc:
            if attempt == 1 and retryable(exc):
                emit_generation_event("status", message="模型输出未通过校验，正在重新分析…")
                continue
            raise
        cleaned = clean_report(report, [], require_reference=False)
        if cleaned is not None:
            receipt = TrendReceipt(**cleaned.model_dump(),
                                   generated_at=datetime.now(timezone.utc).isoformat(),
                                   topics=[row["name"] for row in documents], sources=["上传文件"])
            try:
                emit_generation_event("status", message="选题已生成，正在保存到历史…")
                record_id = await run_in_threadpool(
                    topic_db.save_report, engine, account_id, _source_type(documents),
                    [row["name"] for row in documents], sum(row["chars"] for row in documents),
                    instruction, genre_preference, audience, receipt.model_dump(mode="json"))
                receipt = receipt.model_copy(update={"record_id": record_id})
                emit_generation_event("status", message="选题已保存到历史记录。")
            except Exception as exc:
                logger.warning("选题结果保存失败：异常类型=%s", type(exc).__name__)
                emit_generation_event("status", message="选题已生成，但保存到历史失败；请检查数据库迁移。")
            emit_generation_event("status", message="文件分析完成，已生成选题方案。")
            return receipt
        if attempt == 1:
            emit_generation_event("status", message="部分选题缺少必要字段，正在重新分析…")
    raise HTTPException(502, "模型返回的选题缺少必要字段，请重试生成")


@router.get("", summary="查询历史选题")
async def list_document_topics(user: CurrentAccount, engine: Database,
                               limit: int = Query(default=20, ge=1, le=50),
                               offset: int = Query(default=0, ge=0)):
    return await run_in_threadpool(topic_db.list_reports, engine, user.id, limit, offset)


@router.get("/expansions", summary="查询设定补全历史")
async def list_setting_expansions(user: CurrentAccount, engine: Database,
                                  limit: int = Query(default=20, ge=1, le=50),
                                  offset: int = Query(default=0, ge=0)):
    return await run_in_threadpool(setting_db.list_reports, engine, user.id, limit, offset)


@router.get("/expansions/{report_id}", summary="查看设定补全稿")
async def get_setting_expansion(report_id: int, user: CurrentAccount, engine: Database):
    report = await run_in_threadpool(setting_db.get_report, engine, user.id, report_id)
    if report is None:
        raise HTTPException(404, "设定补全记录不存在")
    return report


@router.get("/{report_id}", summary="查看历史选题详情")
async def get_document_topic(report_id: int, user: CurrentAccount, engine: Database):
    report = await run_in_threadpool(topic_db.get_report, engine, user.id, report_id)
    if report is None:
        raise HTTPException(404, "选题记录不存在")
    return report


@router.post("/expand/tasks", status_code=202, summary="在已有设定基础上补全世界观细节")
async def start_setting_expansion(
        user: CurrentAccount, engine: Database,
        files: list[UploadFile] | None = File(default=None),
        text: str = Form(""),
        instruction: str = Form(DEFAULT_EXPANSION_INSTRUCTION),
        model: str | None = Form(None)):
    documents, text, added_text = await _collect_materials(files, text)
    instruction = instruction.strip()[:2000]
    signature = hashlib.sha256(json.dumps({
        "names": [row["name"] for row in documents],
        "chars": [row["chars"] for row in documents],
        "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16] if text else "",
        "instruction": instruction,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    file_count = sum(1 for row in documents if row["name"] != "粘贴文本")
    title = ("设定补全 · 文件与粘贴文本" if file_count and added_text else
             "设定补全 · 粘贴文本" if added_text else f"设定补全 · {file_count} 个文件")
    return generation_jobs.store.start(
        account_id=user.id, key=f"setting-expansion:{signature}", kind="setting",
        title=title,
        operation=lambda: expand_setting(documents, instruction, model, user.id, engine)).summary()


@router.post("/analyze/tasks", status_code=202, summary="从上传文件或粘贴文本分析并推荐题材")
async def start_document_analysis(
        user: CurrentAccount, engine: Database,
        files: list[UploadFile] | None = File(default=None),
        text: str = Form(""),
        instruction: str = Form(DEFAULT_DOCUMENT_INSTRUCTION),
        genre_preference: str = Form("都市、玄幻、言情、悬疑等网文类型"),
        audience: str = Form("关注热门网络小说的读者"),
        idea_count: int = Form(4),
        model: str | None = Form(None)):
    documents, text, added_text = await _collect_materials(files, text)
    idea_count = max(2, min(5, idea_count))
    instruction = instruction.strip()[:2000]
    genre_preference = genre_preference.strip()[:40] or "都市、玄幻、言情、悬疑等网文类型"
    audience = audience.strip()[:40] or "关注热门网络小说的读者"
    signature = hashlib.sha256(json.dumps({
        "names": [row["name"] for row in documents],
        "chars": [row["chars"] for row in documents],
        "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16] if text else "",
        "instruction": instruction,
        "genre": genre_preference,
        "audience": audience,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    file_count = sum(1 for row in documents if row["name"] != "粘贴文本")
    title = ("文件与文本选题分析" if file_count and added_text else
             "文本选题分析" if added_text else f"文件选题分析 · {file_count} 个文件")
    return generation_jobs.store.start(
        account_id=user.id, key=f"document-topics:{signature}", kind="topics",
        title=title,
        operation=lambda: analyze_documents(documents, instruction, genre_preference,
                                            audience, idea_count, model, user.id, engine)).summary()
