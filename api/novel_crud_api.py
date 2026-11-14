"""CRUD routes for the 15 tables in db/schema.sql."""

import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import delete, insert, select, update

from api.novel_contracts import CRUD_RESOURCES, CrudResource
from api.account_api import CurrentAccount
from api.database import Database, get_database, transaction
from api.novel_tables import tables
from api.ownership import owned_condition, require_owned, validate_owned_links
from core.logging import get_logger
from db.connection import legacy_json_columns


router = APIRouter()
logger = get_logger("novel_crud")


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
    relation = tables[table_name]
    is_world = table_name == "novel_world"

    def list_rows(query: Annotated[filter_schema, Query()], user: CurrentAccount, engine: Database):
        statement = select(relation).where(owned_condition(relation, user.id))
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

    def create_row(payload: create_schema, user: CurrentAccount, engine: Database):
        values = payload.model_dump()
        if resource.account_owned:
            values["account_id"] = user.id
        if resource.session_ids:
            values["id"] = str(uuid4())
        with transaction(engine) as connection:
            validate_owned_links(connection, relation, values, user.id)
            json_columns = legacy_json_columns(connection, table_name)
            validate_links(connection, relation, values)
            if is_world and values["is_current"]:
                lock_world_project(connection, values["project_id"])
                clear_current_world(connection, relation, values["project_id"])
            result = connection.execute(insert(relation).values(**storage_values(values, json_columns)))
            row = get_row(connection, relation, values["id"] if resource.session_ids else result.lastrowid)
            return response_row(row, json_columns)

    def read_row(record_id: id_type, user: CurrentAccount, engine: Database):
        with transaction(engine) as connection:
            require_owned(connection, table_name, record_id, user.id)
            row = get_row(connection, relation, record_id)
            return response_row(row, legacy_json_columns(connection, table_name))

    def patch_row(record_id: id_type, payload: patch_schema, user: CurrentAccount, engine: Database):
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(422, "请提供至少一个要更新的字段")
        with transaction(engine) as connection:
            require_owned(connection, table_name, record_id, user.id)
            json_columns = legacy_json_columns(connection, table_name)
            if is_world and changes.get("is_current") is True:
                world = get_row(connection, relation, record_id)
                lock_world_project(connection, world["project_id"])
            row = get_row(connection, relation, record_id, lock=True)
            validate_owned_links(connection, relation, {**row, **changes}, user.id)
            validate_links(connection, relation, {**row, **changes})
            if is_world and changes.get("is_current") is True:
                clear_current_world(connection, relation, row["project_id"], record_id)
            statement = update(relation).where(relation.c.id == record_id)
            connection.execute(statement.values(**storage_values(changes, json_columns)))
            return response_row(get_row(connection, relation, record_id, lock=True), json_columns)

    def delete_row(record_id: id_type, user: CurrentAccount, engine: Database):
        with transaction(engine) as connection:
            require_owned(connection, table_name, record_id, user.id)
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
