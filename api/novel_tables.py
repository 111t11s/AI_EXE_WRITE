"""Shared SQL table expressions for the API and account ownership checks."""

from sqlalchemy import JSON, Boolean, DateTime, column, table

from api.novel_contracts import CRUD_RESOURCES


_sql_types = {"collected_data": JSON(), "is_current": Boolean(), "is_public": Boolean()}
tables = {
    resource.table_name: table(
        resource.table_name, column("id"),
        *(column(key, _sql_types.get(key)) for key in resource.create_schema.model_fields),
        *(column(key) for key in resource.timestamps),
        *([column("account_id")] if resource.account_owned else []),
        *(column(key, DateTime()) for key in ("deleted_at", "purge_at")
          if resource.table_name == "novel_project"),
    ) for resource in CRUD_RESOURCES
}
