"""伏笔与杂记的项目级查询、手工补充接口。"""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from api.account_api import CurrentAccount
from api.database import Database, transaction
from api.novel_schemas import RowId
from api.novel_tables import tables
from api.ownership import require_resource
from api.writing_schemas import ForeshadowCreate, MiscNoteCreate
from db import foreshadows as foreshadow_db
from db import misc_notes as misc_db


router = APIRouter(prefix="/writing", tags=["伏笔与杂记"])
FORESHADOW_CATEGORIES = {
    "identity": "身份秘密", "treasure": "秘宝奇物", "event": "事件真相",
    "relationship": "关系伏笔", "ability": "能力来历", "world": "世界规则",
    "prophecy": "预言征兆", "debt": "人情仇恨", "location": "地点秘密", "other": "其他",
}
FORESHADOW_STATUSES = {"planned": "待预埋", "planted": "已预埋", "consumed": "已消费", "abandoned": "已废弃"}
MISC_TYPES = {
    "event": "关键事件", "item": "独特物品", "beast": "灵兽",
    "passerby": "路人角色", "technique": "功法技能", "location": "地点场所", "custom": "其他",
}


def _labels(engine, project_id: int):
    outline, volume, chapter = tables["novel_outline"], tables["novel_outline_volume"], tables["novel_chapter"]
    with transaction(engine) as connection:
        outlines = {row["id"]: row["version_no"] for row in connection.execute(
            select(outline.c.id, outline.c.version_no).where(outline.c.project_id == project_id)).mappings()}
        volumes = {row["id"]: (row["volume_number"], row["title"]) for row in connection.execute(
            select(volume.c.id, volume.c.volume_number, volume.c.title).where(
                volume.c.outline_id.in_(outlines.keys()) if outlines else False)).mappings()} if outlines else {}
        chapters = {row["id"]: row["chapter_number"] for row in connection.execute(
            select(chapter.c.id, chapter.c.chapter_number).where(chapter.c.project_id == project_id)).mappings()}
    return outlines, volumes, chapters


def _foreshadow_item(item, outlines, volumes, chapters):
    return {**item,
            "category_label": FORESHADOW_CATEGORIES.get(item["category"], item["category"]),
            "status_label": FORESHADOW_STATUSES.get(item["status"], item["status"]),
            "planted_outline_label": f"大纲 v{outlines[item['planted_outline_id']]}" if item.get("planted_outline_id") in outlines else "",
            "planted_volume_label": (f"第 {volumes[item['planted_volume_id']][0]} 卷 · {volumes[item['planted_volume_id']][1]}"
                                     if item.get("planted_volume_id") in volumes else ""),
            "planted_chapter_label": f"第 {chapters[item['planted_chapter_id']]} 章" if item.get("planted_chapter_id") in chapters else "",
            "consumed_outline_label": f"大纲 v{outlines[item['consumed_outline_id']]}" if item.get("consumed_outline_id") in outlines else "",
            "consumed_volume_label": (f"第 {volumes[item['consumed_volume_id']][0]} 卷 · {volumes[item['consumed_volume_id']][1]}"
                                      if item.get("consumed_volume_id") in volumes else ""),
            "consumed_chapter_label": f"第 {chapters[item['consumed_chapter_id']]} 章" if item.get("consumed_chapter_id") in chapters else ""}


def _misc_item(item, chapters):
    return {**item, "type_label": MISC_TYPES.get(item["note_type"], item["note_type"]),
            "first_chapter_label": f"第 {chapters[item['first_chapter_id']]} 章" if item.get("first_chapter_id") in chapters else "",
            "last_chapter_label": f"第 {chapters[item['last_chapter_id']]} 章" if item.get("last_chapter_id") in chapters else ""}


@router.get("/projects/{project_id}/foreshadows", summary="分页查询伏笔")
async def list_foreshadows(project_id: RowId, user: CurrentAccount, engine: Database,
                           page: int = Query(default=1, ge=1), page_size: int = Query(default=8, ge=1, le=30),
                           status: str = Query(default="all"), category: str = Query(default="all")):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    if status != "all" and status not in FORESHADOW_STATUSES:
        raise HTTPException(422, "伏笔状态不支持")
    result = await run_in_threadpool(foreshadow_db.list_page, engine, project_id, page, page_size, status, category)
    outlines, volumes, chapters = await run_in_threadpool(_labels, engine, project_id)
    result["items"] = [_foreshadow_item(row, outlines, volumes, chapters) for row in result["items"]]
    result["categories"] = FORESHADOW_CATEGORIES
    result["statuses"] = FORESHADOW_STATUSES
    return result


@router.get("/projects/{project_id}/foreshadows/available", summary="查询可用于新大纲的伏笔")
async def available_foreshadows(project_id: RowId, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    items = await run_in_threadpool(foreshadow_db.available, engine, project_id)
    return {"items": [{**item, "category_label": FORESHADOW_CATEGORIES.get(item["category"], item["category"])}
                      for item in items], "categories": FORESHADOW_CATEGORIES}


@router.get("/projects/{project_id}/foreshadows/{foreshadow_id}", summary="查看伏笔详情")
async def get_foreshadow(project_id: RowId, foreshadow_id: int, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    item = await run_in_threadpool(foreshadow_db.get, engine, project_id, foreshadow_id)
    if item is None:
        raise HTTPException(404, "伏笔不存在")
    outlines, volumes, chapters = await run_in_threadpool(_labels, engine, project_id)
    return _foreshadow_item(item, outlines, volumes, chapters)


@router.post("/projects/{project_id}/foreshadows", status_code=201, summary="手工新增伏笔")
async def create_foreshadow(project_id: RowId, payload: ForeshadowCreate,
                            user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    foreshadow_id = await run_in_threadpool(
        foreshadow_db.create, engine, project_id, {**payload.model_dump(), "source": "preset"})
    item = await run_in_threadpool(foreshadow_db.get, engine, project_id, foreshadow_id)
    outlines, volumes, chapters = await run_in_threadpool(_labels, engine, project_id)
    return _foreshadow_item(item, outlines, volumes, chapters)


@router.get("/projects/{project_id}/misc-notes", summary="分页查询杂记")
async def list_misc_notes(project_id: RowId, user: CurrentAccount, engine: Database,
                          page: int = Query(default=1, ge=1), page_size: int = Query(default=8, ge=1, le=30),
                          note_type: str = Query(default="all")):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    if note_type != "all" and note_type not in MISC_TYPES:
        raise HTTPException(422, "杂记分类不支持")
    result = await run_in_threadpool(misc_db.list_page, engine, project_id, page, page_size, note_type)
    _, _, chapters = await run_in_threadpool(_labels, engine, project_id)
    result["items"] = [_misc_item(row, chapters) for row in result["items"]]
    result["types"] = MISC_TYPES
    return result


@router.get("/projects/{project_id}/misc-notes/{note_id}", summary="查看杂记详情")
async def get_misc_note(project_id: RowId, note_id: int, user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    item = await run_in_threadpool(misc_db.get, engine, project_id, note_id)
    if item is None:
        raise HTTPException(404, "杂记不存在")
    _, _, chapters = await run_in_threadpool(_labels, engine, project_id)
    return _misc_item(item, chapters)


@router.post("/projects/{project_id}/misc-notes", status_code=201, summary="手工新增杂记")
async def create_misc_note(project_id: RowId, payload: MiscNoteCreate,
                           user: CurrentAccount, engine: Database):
    await run_in_threadpool(require_resource, engine, "novel_project", project_id, user.id)
    note_id = await run_in_threadpool(misc_db.create, engine, project_id, {**payload.model_dump(), "source": "manual"})
    item = await run_in_threadpool(misc_db.get, engine, project_id, note_id)
    _, _, chapters = await run_in_threadpool(_labels, engine, project_id)
    return _misc_item(item, chapters)
