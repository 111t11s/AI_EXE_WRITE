"""Resolve every novel resource to its owning account in SQL."""

from fastapi import HTTPException
from sqlalchemy import select

from api.database import transaction
from api.novel_tables import tables


# Additional foreign keys are checked too, so a payload cannot link another
# account's faction, chapter volume, character or questionnaire project.
LINKS = {
    "novel_design_session": {"project_id": "novel_project"},
    "novel_design_message": {"session_id": "novel_design_session"},
    "novel_world": {"project_id": "novel_project"},
    "novel_region": {"world_id": "novel_world"},
    "novel_power_system": {"world_id": "novel_world"},
    "novel_power_rank": {"power_system_id": "novel_power_system"},
    "novel_cheat_system": {"world_id": "novel_world"},
    "novel_faction": {"world_id": "novel_world"},
    "novel_character": {"project_id": "novel_project", "faction_id": "novel_faction"},
    "novel_character_relationship": {"source_character_id": "novel_character", "target_character_id": "novel_character"},
    "novel_outline": {"project_id": "novel_project"},
    "novel_outline_volume": {"outline_id": "novel_outline"},
    "novel_plot_beat": {"volume_id": "novel_outline_volume"},
    "novel_chapter": {"project_id": "novel_project", "volume_id": "novel_outline_volume"},
}


def owned_condition(relation, account_id):
    if "account_id" in relation.c:
        return relation.c.account_id == account_id
    foreign_key, parent_name = next(iter(LINKS[relation.name].items()))
    parent = tables[parent_name]
    return relation.c[foreign_key].in_(select(parent.c.id).where(owned_condition(parent, account_id)))


def require_owned(connection, table_name, record_id, account_id):
    relation = tables[table_name]
    exists = connection.scalar(select(relation.c.id).where(relation.c.id == record_id,
                                                          owned_condition(relation, account_id)))
    if exists is None:
        raise HTTPException(404, "记录不存在")


def validate_owned_links(connection, relation, values, account_id):
    for foreign_key, parent in LINKS.get(relation.name, {}).items():
        if values.get(foreign_key) is not None:
            require_owned(connection, parent, values[foreign_key], account_id)


def require_resource(engine, table_name, record_id, account_id):
    with transaction(engine) as connection:
        require_owned(connection, table_name, record_id, account_id)
