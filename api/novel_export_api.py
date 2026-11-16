"""Download account-owned novels without pagination or model calls."""

import re
from tempfile import SpooledTemporaryFile
from typing import Annotated, Literal
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from api.account_api import CurrentAccount
from api.database import Database, transaction
from api.novel_crud_api import get_row, response_row
from api.novel_schemas import RowId
from api.novel_tables import tables
from api.ownership import require_readable
from db.connection import legacy_json_columns


router = APIRouter(tags=["小说导出"])


class ExportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["txt", "md"] = "txt"
    outline_id: RowId | None = None
    chapter_ids: list[RowId] | None = Field(default=None, min_length=1)


@router.post("/projects/{project_id}/export", response_class=StreamingResponse, summary="批量导出所选章节为 ZIP")
def export_selected_chapters(project_id: RowId, payload: ExportOptions, user: CurrentAccount, engine: Database):
    return export_novel(project_id, user, engine, **payload.model_dump())


def filename(title, extension):
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "_", title)[:100].strip(" .") or "未命名小说"
    # Leave room for the extension on filesystems with a 255-byte name limit.
    name = name.encode("utf-8")[:200].decode("utf-8", errors="ignore").rstrip(" .") or "未命名小说"
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = "小说_" + name
    return f"{name}.{extension}"


def one_line(value):
    return " ".join((value or "").splitlines()).strip()


@router.get("/projects/{project_id}/export", response_class=StreamingResponse, summary="按章序导出 ZIP，每章一个 TXT 或 Markdown 文件")
def export_novel(project_id: RowId, user: CurrentAccount, engine: Database,
                 format: Literal["txt", "md"] = "txt", outline_id: RowId | None = None,
                 chapter_ids: Annotated[list[RowId] | None, Query()] = None):
    output = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    markdown = format == "md"

    def block(value, level=None):
        text = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not text.strip():
            return
        if level:
            text = ("#" * level + " " if markdown else "") + one_line(text)
        text += "\n\n"
        document.write((text if markdown else text.replace("\n", "\r\n")).encode("utf-8"))

    try:
        with transaction(engine) as connection:
            require_readable(connection, "novel_project", project_id, user)
            project = response_row(get_row(connection, tables["novel_project"], project_id),
                                   legacy_json_columns(connection, "novel_project"))
            outline_table, volume_table, beat_table = (tables[name] for name in (
                "novel_outline", "novel_outline_volume", "novel_plot_beat"))
            volume_columns = legacy_json_columns(connection, "novel_outline_volume")
            volumes = [response_row(row, volume_columns) for row in connection.execute(select(volume_table).where(
                volume_table.c.outline_id.in_(select(outline_table.c.id).where(
                    outline_table.c.project_id == project_id))).order_by(volume_table.c.volume_number)).mappings()]
            by_volume = {row["id"]: row for row in volumes}
            chapter = tables["novel_chapter"]
            selected_ids = set(chapter_ids) if chapter_ids is not None else None
            if selected_ids is not None:
                if not selected_ids:
                    raise HTTPException(422, "请至少选择一个章节")
                existing = set(connection.scalars(select(chapter.c.id).where(
                    chapter.c.project_id == project_id, chapter.c.id.in_(selected_ids))))
                if existing != selected_ids:
                    raise HTTPException(404, "所选章节不存在或不属于当前小说")
            with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
                if outline_id is not None:
                    require_readable(connection, "novel_outline", outline_id, user)
                    outline = response_row(get_row(connection, outline_table, outline_id),
                                           legacy_json_columns(connection, "novel_outline"))
                    if outline["project_id"] != project_id:
                        raise HTTPException(404, "所选大纲不属于当前小说")
                    with archive.open(filename(f"大纲-v{outline['version_no']}", format), "w", force_zip64=True) as document:
                        if not markdown:
                            document.write(b"\xef\xbb\xbf")
                        block(project["title"], 1)
                        if project.get("premise"):
                            block(project["premise"])
                        block(f'大纲 v{outline["version_no"]}', 2)
                        for label, field in (("全书摘要", "summary"), ("核心冲突", "central_conflict"), ("主题", "themes")):
                            if outline.get(field):
                                block(label, 3)
                                block(outline[field])
                        selected_volumes = [row for row in volumes if row["outline_id"] == outline_id]
                        beat_columns = legacy_json_columns(connection, "novel_plot_beat")
                        beats = [response_row(row, beat_columns) for row in connection.execute(select(beat_table).where(
                            beat_table.c.volume_id.in_([row["id"] for row in selected_volumes])
                        ).order_by(beat_table.c.sort_order, beat_table.c.id)).mappings()]
                        for volume in selected_volumes:
                            block(f'第 {volume["volume_number"]} 卷 · {volume["title"]}', 3)
                            for label, field in (("分卷摘要", "summary"), ("主角目标", "protagonist_goal"),
                                                 ("主要冲突", "main_conflict"), ("高潮", "climax"), ("结尾悬念", "ending_hook")):
                                if volume.get(field):
                                    block(f'{label}：{volume[field]}')
                            for beat in beats:
                                if beat["volume_id"] == volume["id"]:
                                    block(beat["title"], 4)
                                    block(beat["summary"])
                                    if beat.get("foreshadowing"):
                                        block(f'伏笔：{beat["foreshadowing"]}')
                columns = legacy_json_columns(connection, "novel_chapter")
                query = select(chapter).where(chapter.c.project_id == project_id)
                if selected_ids is not None:
                    query = query.where(chapter.c.id.in_(selected_ids))
                # Fetch one chapter at a time and compress directly into a spooled
                # archive rather than keeping the complete novel in memory.
                result = connection.execution_options(stream_results=True).execute(
                    query.order_by(chapter.c.chapter_number, chapter.c.id))
                count = 0
                try:
                    for row in result.mappings():
                        saved = response_row(row, columns)
                        if not saved["content"].strip():
                            continue
                        title = saved["title"] or "未命名章节"
                        entry = filename(f'第{saved["chapter_number"]:05d}章_{title}', format)
                        with archive.open(entry, "w", force_zip64=True) as document:
                            if not markdown:
                                document.write(b"\xef\xbb\xbf")
                            block(project["title"], 1)
                            volume = by_volume.get(saved["volume_id"])
                            if volume:
                                block(f'第 {volume["volume_number"]} 卷 · {volume["title"]}', 2)
                            block(f'第 {saved["chapter_number"]} 章 · {title}', 3 if volume else 2)
                            block(saved["content"])
                        count += 1
                finally:
                    result.close()
                if not count and outline_id is None:
                    raise HTTPException(409, "暂无可导出的正文，请先保存章节正文，或选择附带大纲后导出")
        size = output.tell()
        output.seek(0)
    except BaseException:
        output.close()
        raise

    def chunks():
        try:
            while data := output.read(64 * 1024):
                yield data
        finally:
            output.close()

    return StreamingResponse(chunks(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="novel-{project_id}.zip"; '
                                       f"filename*=UTF-8''{quote(filename(project['title'], 'zip'), safe='')}",
                 "Content-Length": str(size), "Cache-Control": "no-store",
                 "X-Content-Type-Options": "nosniff"}, background=BackgroundTask(output.close))
