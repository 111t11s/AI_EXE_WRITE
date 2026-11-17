"""Soft-delete, restore, and scheduled cleanup for novel projects."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, true, update

from api.account_api import CurrentAccount
from api.database import Database, transaction
from api.novel_tables import tables
from api.novel_schemas import RowId


TRASH_DAYS = 7
router = APIRouter(prefix="/projects", tags=["小说回收站"])


class TrashProject(BaseModel):
    id: RowId
    account_id: RowId | None = None
    title: str
    genre: str
    deleted_at: datetime
    purge_at: datetime
    created_at: datetime
    updated_at: datetime


class TrashAction(BaseModel):
    id: RowId
    restored: Literal[True] = True


class PurgeAction(BaseModel):
    id: RowId
    purged: Literal[True] = True


def utc_now():
    # MySQL DATETIME and SQLite DATETIME are stored without timezone metadata.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def project_access_condition(project, user):
    return true() if user.is_admin else project.c.account_id == user.id


def move_project_to_trash(connection, project_id, *, now=None):
    project = tables["novel_project"]
    now = now or utc_now()
    purge_at = now + timedelta(days=TRASH_DAYS)
    result = connection.execute(update(project).where(
        project.c.id == project_id, project.c.deleted_at.is_(None)
    ).values(deleted_at=now, purge_at=purge_at))
    if result.rowcount == 0:
        raise HTTPException(404, "小说不存在或已在回收站")
    return now, purge_at


def purge_project(connection, project_id):
    """Delete sessions explicitly because their FK keeps project_id nullable."""
    project = tables["novel_project"]
    deleted_at = connection.scalar(select(project.c.deleted_at).where(
        project.c.id == project_id
    ).with_for_update())
    if deleted_at is None:
        return False
    session = tables["novel_design_session"]
    connection.execute(delete(session).where(session.c.project_id == project_id))
    result = connection.execute(delete(project).where(
        project.c.id == project_id, project.c.deleted_at.is_not(None)
    ))
    return result.rowcount > 0


def purge_expired_projects(engine, now=None):
    now = now or utc_now()
    project = tables["novel_project"]
    with transaction(engine) as connection:
        ids = list(connection.scalars(select(project.c.id).where(
            project.c.deleted_at.is_not(None), project.c.purge_at.is_not(None), project.c.purge_at <= now
        )))
        purged = sum(purge_project(connection, project_id) for project_id in ids)
    return purged


@router.get("/trash", response_model=list[TrashProject], summary="查看小说回收站")
async def list_trash(user: CurrentAccount, engine: Database):
    project = tables["novel_project"]
    with transaction(engine) as connection:
        rows = connection.execute(select(project).where(
            project_access_condition(project, user), project.c.deleted_at.is_not(None)
        ).order_by(project.c.purge_at.asc(), project.c.id.asc())).mappings().all()
    return [TrashProject.model_validate(dict(row)) for row in rows]


@router.post("/{project_id}/restore", response_model=TrashAction, summary="从回收站恢复小说")
async def restore_project(project_id: RowId, user: CurrentAccount, engine: Database):
    project = tables["novel_project"]
    with transaction(engine) as connection:
        row = connection.execute(select(project).where(
            project.c.id == project_id, project_access_condition(project, user), project.c.deleted_at.is_not(None)
        ).with_for_update()).mappings().one_or_none()
        if row is None:
            raise HTTPException(404, "回收站中不存在该小说")
        connection.execute(update(project).where(project.c.id == project_id).values(
            deleted_at=None, purge_at=None
        ))
    return TrashAction(id=project_id)


@router.delete("/{project_id}/purge", response_model=PurgeAction, summary="立即彻底删除小说")
async def purge_project_now(project_id: RowId, user: CurrentAccount, engine: Database):
    project = tables["novel_project"]
    with transaction(engine) as connection:
        exists = connection.scalar(select(project.c.id).where(
            project.c.id == project_id, project_access_condition(project, user), project.c.deleted_at.is_not(None)
        ).with_for_update())
        if exists is None:
            raise HTTPException(404, "回收站中不存在该小说")
        purge_project(connection, project_id)
    return PurgeAction(id=project_id)
