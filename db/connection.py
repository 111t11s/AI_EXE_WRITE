"""MySQL connection pool for the persistence API."""

from functools import lru_cache
from threading import Lock
from weakref import WeakKeyDictionary

from sqlalchemy import URL, Connection, Engine, create_engine

from core.config import get_settings


_legacy_json_columns = WeakKeyDictionary()
_schema_lock = Lock()


class DatabaseNotConfigured(RuntimeError):
    pass


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    if not all((settings.DB_HOST, settings.DB_USER, settings.DB_NAME)) or settings.DB_PASSWORD is None:
        raise DatabaseNotConfigured
    url = URL.create(
        "mysql+pymysql",
        username=settings.DB_USER,
        password=settings.DB_PASSWORD.get_secret_value(),
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
        query={"charset": "utf8mb4"},
    )
    return create_engine(
        url, pool_pre_ping=True, pool_recycle=1800, pool_timeout=5, hide_parameters=True,
        connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10},
    )


def legacy_json_columns(connection: Connection, table_name: str) -> frozenset[str]:
    """Find description columns kept as JSON in older installs, once per engine."""
    if connection.dialect.name != "mysql":
        return frozenset()
    with _schema_lock:
        by_table = _legacy_json_columns.get(connection.engine)
        if by_table is None:
            rows = connection.exec_driver_sql(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND DATA_TYPE = 'json' AND COLUMN_NAME <> 'collected_data'"
            )
            grouped = {}
            for table_name_in_db, column_name in rows:
                grouped.setdefault(table_name_in_db, set()).add(column_name)
            by_table = {key: frozenset(names) for key, names in grouped.items()}
            _legacy_json_columns[connection.engine] = by_table
        return by_table.get(table_name, frozenset())
