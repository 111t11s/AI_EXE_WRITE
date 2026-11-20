"""Public, read-only books and bodies backed by the author's live project rows."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import case, func, select

from api.database import Database, transaction
from api.novel_crud_api import response_row
from api.novel_schemas import RowId
from api.novel_tables import tables
from db.accounts import account
from db.connection import legacy_json_columns


router = APIRouter(prefix="/public/projects", tags=["公共书架"])
project = tables["novel_project"]
chapter = tables["novel_chapter"]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=2**32 - 1)]


class PublicBook(BaseModel):
    id: int
    title: str
    genre: str
    premise: str | None
    status: str
    author: str
    chapter_count: int
    updated_at: datetime


class PublicBookPage(BaseModel):
    items: list[PublicBook]
    limit: int
    offset: int
    has_more: bool


class PublicChapter(BaseModel):
    id: int
    chapter_number: int
    title: str
    word_count: int
    updated_at: datetime


class PublicChapterBody(PublicChapter):
    content: str


class PublicChapterPage(BaseModel):
    items: list[PublicChapter]
    limit: int
    offset: int
    has_more: bool


def visible_book():
    return (project.c.is_public.is_(True), project.c.deleted_at.is_(None))


def visible_body():
    # Planning-only chapters are not part of the reader's table of contents.
    return func.length(func.trim(chapter.c.content)) > 0


def book_statement():
    stats = select(chapter.c.project_id, func.count().label("chapter_count"),
                   func.max(chapter.c.updated_at).label("updated_at")).where(visible_body()).group_by(
                       chapter.c.project_id).subquery()
    latest = case((stats.c.updated_at > project.c.updated_at, stats.c.updated_at),
                  else_=project.c.updated_at).label("updated_at")
    return select(project.c.id, project.c.title, project.c.genre, project.c.premise, project.c.status,
                  func.coalesce(func.nullif(account.c.display_name, ""), account.c.username, "作者").label("author"),
                  func.coalesce(stats.c.chapter_count, 0).label("chapter_count"), latest).select_from(
                      project.outerjoin(account, project.c.account_id == account.c.id).outerjoin(
                          stats, stats.c.project_id == project.c.id)).where(*visible_book())


def require_public(connection, project_id):
    if connection.scalar(select(project.c.id).where(project.c.id == project_id, *visible_book())) is None:
        raise HTTPException(404, "书籍不存在或尚未发布")


def page(connection, statement, limit, offset):
    rows = connection.execute(statement.offset(offset).limit(limit + 1)).mappings().all()
    return {"items": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit}


@router.get("", response_model=PublicBookPage)
def list_books(engine: Database, response: Response, limit: Limit = 20, offset: Offset = 0):
    response.headers["Cache-Control"] = "no-store"
    with transaction(engine) as connection:
        statement = book_statement()
        return page(connection, statement.order_by(statement.selected_columns.updated_at.desc(),
                                                   project.c.id.desc()), limit, offset)


@router.get("/{project_id}", response_model=PublicBook)
def read_book(project_id: RowId, engine: Database, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with transaction(engine) as connection:
        row = connection.execute(book_statement().where(project.c.id == project_id)).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "书籍不存在或尚未发布")
        return row


@router.get("/{project_id}/chapters", response_model=PublicChapterPage)
def list_chapters(project_id: RowId, engine: Database, response: Response, limit: Limit = 100, offset: Offset = 0):
    response.headers["Cache-Control"] = "no-store"
    with transaction(engine) as connection:
        require_public(connection, project_id)
        return page(connection, select(*(chapter.c[key] for key in PublicChapter.model_fields)).where(
            chapter.c.project_id == project_id, visible_body()).order_by(chapter.c.chapter_number, chapter.c.id), limit, offset)


@router.get("/{project_id}/chapters/{chapter_id}", response_model=PublicChapterBody)
def read_chapter(project_id: RowId, chapter_id: RowId, engine: Database, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with transaction(engine) as connection:
        require_public(connection, project_id)
        row = connection.execute(select(*(chapter.c[key] for key in PublicChapterBody.model_fields)).where(
            chapter.c.project_id == project_id, chapter.c.id == chapter_id, visible_body())).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "章节不存在或暂无正文")
        return response_row(row, legacy_json_columns(connection, "novel_chapter"))
