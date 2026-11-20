"""Add opt-in book publication to an existing database without exposing old books."""

from sqlalchemy import inspect

from db.connection import get_engine


def migrate_schema(engine):
    inspector = inspect(engine)
    if not inspector.has_table("novel_project"):
        raise ValueError("请先导入 db/schema.sql 初始化小说表")
    columns = {column["name"] for column in inspector.get_columns("novel_project")}
    with engine.begin() as connection:
        if "is_public" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE novel_project ADD COLUMN is_public BOOLEAN NOT NULL DEFAULT FALSE")
        indexes = {item["name"] for item in inspect(connection).get_indexes("novel_project")}
        if "idx_project_public_updated" not in indexes:
            connection.exec_driver_sql(
                "CREATE INDEX idx_project_public_updated ON novel_project (is_public, updated_at)")


def main():
    engine = get_engine()
    try:
        migrate_schema(engine)
        print("公共书架字段已就绪，已有小说保持未发布。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
