"""Shared SQL table expressions for the API and account ownership checks."""

from sqlalchemy import JSON, Boolean, column, table

from api.novel_contracts import CRUD_RESOURCES


_sql_types = {"collected_data": JSON(), "is_current": Boolean()}
tables = {
    resource.table_name: table(
        resource.table_name, column("id"),
        *(column(key, _sql_types.get(key)) for key in resource.create_schema.model_fields),
        *(column(key) for key in resource.timestamps),
        *([column("account_id")] if resource.account_owned else []),
    ) for resource in CRUD_RESOURCES
}
