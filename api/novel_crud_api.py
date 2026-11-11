"""CRUD routes for the 15 tables in db/schema.sql."""

import json
from contextlib import contextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import JSON, Boolean, Engine, column, delete, insert, select, table, update
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout

from api.novel_contracts import CRUD_RESOURCES, CrudResource
from core.logging import get_logger
from db.connection import DatabaseNotConfigured, get_engine, legacy_json_columns


router = APIRouter()
logger = get_logger("novel_crud")
tables = {}


def get_database() -> Engine:
    try:
        return get_engine()
    except DatabaseNotConfigured:
        raise HTTPException(503, "数据库连接未配置") from None


Database = Annotated[Engine, Depends(get_database)]


@contextmanager
def transaction(engine):
    try:
        with engine.begin() as connection:
            yield connection
    except PoolTimeout:
        raise HTTPException(503, "数据库连接池暂时繁忙") from None
    except DBAPIError as exc:
        code = next(iter(getattr(exc.orig, "args", ())), None)
        if code == 1062:
            raise HTTPException(409, "记录与现有数据的唯一键冲突") from None
        if code in {1451, 1452}:
            raise HTTPException(409, "关联记录不存在或仍被引用") from None
        if code in {1048, 1264, 1366, 1406, 3140, 3141}:
            raise HTTPException(422, "字段值超出数据库允许的范围") from None
        logger.error("小说数据操作失败 code=%s", code if isinstance(code, int) else "unknown")
        if exc.connection_invalidated or code in {1044, 1045, 1049, 1146, 1205, 1213, 2002, 2003, 2006, 2013}:
            raise HTTPException(503, "数据库暂时不可用") from None
        raise HTTPException(500, "数据库操作失败") from None


def get_row(connection, relation, record_id, *, lock=False):
    statement = select(relation).where(relation.c.id == record_id)
    if lock:
        statement = statement.with_for_update()
    row = connection.execute(statement).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "记录不存在")
    return row


def validate_links(connection, relation, values):
    """Enforce project ownership for links that a single foreign key cannot check."""
    if relation.name == "novel_character" and values["faction_id"] is not None:
        faction, world = tables["novel_faction"], tables["novel_world"]
        statement = select(world.c.project_id).select_from(
            faction.join(world, faction.c.world_id == world.c.id)
        ).where(faction.c.id == values["faction_id"])
    elif relation.name == "novel_chapter" and values["volume_id"] is not None:
        volume, outline = tables["novel_outline_volume"], tables["novel_outline"]
        statement = select(outline.c.project_id).select_from(
            volume.join(outline, volume.c.outline_id == outline.c.id)
        ).where(volume.c.id == values["volume_id"])
    elif relation.name == "novel_character_relationship":
        source, target = values["source_character_id"], values["target_character_id"]
        if source == target:
            raise HTTPException(422, "角色关系的发起角色和目标角色必须不同")
        character = tables["novel_character"]
        projects = dict(connection.execute(select(character.c.id, character.c.project_id).where(
            character.c.id.in_((source, target))
        )).all())
        if source not in projects or target not in projects:
            raise HTTPException(409, "关联角色不存在")
        if projects[source] != projects[target]:
            raise HTTPException(409, "两个角色必须属于同一小说项目")
        return
    else:
        return
    if connection.scalar(statement) != values["project_id"]:
        raise HTTPException(409, "关联记录不存在或不属于同一小说项目")


def lock_world_project(connection, project_id):
    project = tables["novel_project"]
    if connection.scalar(select(project.c.id).where(project.c.id == project_id).with_for_update()) is None:
        raise HTTPException(409, "关联小说项目不存在")


def clear_current_world(connection, relation, project_id, record_id=None):
    statement = update(relation).where(relation.c.project_id == project_id, relation.c.is_current.is_(True))
    if record_id is not None:
        statement = statement.where(relation.c.id != record_id)
    connection.execute(statement.values(is_current=False))


def storage_values(values, json_columns):
    return {key: json.dumps(value, ensure_ascii=False) if key in json_columns else value
            for key, value in values.items()}


def response_row(row, json_columns):
    values = dict(row)
    for key in json_columns.intersection(values):
        raw = values[key]
        if raw is not None:
            value = json.loads(raw)
            values[key] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return values


def add_resource(resource: CrudResource):
    path, table_name, label = resource.path, resource.table_name, resource.label
    create_schema, id_type = resource.create_schema, resource.id_type
    patch_schema, read_schema = resource.patch_schema, resource.read_schema
    page_schema, filter_schema = resource.page_schema, resource.filter_schema
    sql_types = {"collected_data": JSON(), "is_current": Boolean()}
    relation = table(
        table_name, column("id"), *(column(key, sql_types.get(key)) for key in create_schema.model_fields),
        *(column(key) for key in resource.timestamps),
    )
    tables[table_name] = relation
    is_world = table_name == "novel_world"

    def list_rows(query: Annotated[filter_schema, Query()], engine: Database):
        statement = select(relation)
        for key, value in query.model_dump(exclude={"limit", "offset"}, exclude_none=True).items():
            statement = statement.where(relation.c[key] == value)
        ordering = (*resource.order, "id")
        statement = statement.order_by(*(relation.c[key] for key in ordering))
        statement = statement.offset(query.offset).limit(query.limit + 1)
        with transaction(engine) as connection:
            rows = connection.execute(statement).mappings().all()
            json_columns = legacy_json_columns(connection, table_name)
        return {"items": [response_row(row, json_columns) for row in rows[:query.limit]],
                "limit": query.limit, "offset": query.offset,
                "has_more": len(rows) > query.limit}

    def create_row(payload: create_schema, engine: Database):
        values = payload.model_dump()
        if resource.session_ids:
            values["id"] = str(uuid4())
        with transaction(engine) as connection:
            json_columns = legacy_json_columns(connection, table_name)
            validate_links(connection, relation, values)
            if is_world and values["is_current"]:
                lock_world_project(connection, values["project_id"])
                clear_current_world(connection, relation, values["project_id"])
            result = connection.execute(insert(relation).values(**storage_values(values, json_columns)))
            row = get_row(connection, relation, values["id"] if resource.session_ids else result.lastrowid)
            return response_row(row, json_columns)

    def read_row(record_id: id_type, engine: Database):
        with transaction(engine) as connection:
            row = get_row(connection, relation, record_id)
            return response_row(row, legacy_json_columns(connection, table_name))

    def patch_row(record_id: id_type, payload: patch_schema, engine: Database):
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(422, "请提供至少一个要更新的字段")
        with transaction(engine) as connection:
            json_columns = legacy_json_columns(connection, table_name)
            if is_world and changes.get("is_current") is True:
                world = get_row(connection, relation, record_id)
                lock_world_project(connection, world["project_id"])
            row = get_row(connection, relation, record_id, lock=True)
            validate_links(connection, relation, {**row, **changes})
            if is_world and changes.get("is_current") is True:
                clear_current_world(connection, relation, row["project_id"], record_id)
            statement = update(relation).where(relation.c.id == record_id)
            connection.execute(statement.values(**storage_values(changes, json_columns)))
            return response_row(get_row(connection, relation, record_id, lock=True), json_columns)

    def delete_row(record_id: id_type, engine: Database):
        with transaction(engine) as connection:
            result = connection.execute(delete(relation).where(relation.c.id == record_id))
            if result.rowcount == 0:
                raise HTTPException(404, "记录不存在")
        return Response(status_code=204)

    options = {"tags": [label]}
    item_path = path + "/{record_id}"
    router.add_api_route(path, list_rows, methods=["GET"], response_model=page_schema,
                         summary=f"查询{label}列表", name=f"list_{table_name}", **options)
    router.add_api_route(path, create_row, methods=["POST"], response_model=read_schema, status_code=201,
                         summary=f"新增{label}", name=f"create_{table_name}", **options)
    router.add_api_route(item_path, read_row, methods=["GET"], response_model=read_schema,
                         summary=f"读取{label}", name=f"read_{table_name}", **options)
    router.add_api_route(item_path, patch_row, methods=["PATCH"], response_model=read_schema,
                         summary=f"更新{label}", name=f"update_{table_name}", **options)
    router.add_api_route(item_path, delete_row, methods=["DELETE"], status_code=204,
                         response_class=Response, response_model=None,
                         summary=f"删除{label}", name=f"delete_{table_name}", **options)


for resource in CRUD_RESOURCES:
    add_resource(resource)
