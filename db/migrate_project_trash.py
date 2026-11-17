"""Add the project recycle-bin timestamps to an existing database."""

from sqlalchemy import inspect

from db.connection import get_engine


def migrate_schema(engine):
    inspector = inspect(engine)
    if not inspector.has_table("novel_project"):
        raise ValueError("请先导入 db/schema.sql 初始化小说表")
    columns = {column["name"] for column in inspector.get_columns("novel_project")}
    sql_type = "DATETIME" if engine.dialect.name == "mysql" else "DATETIME"
    statements = []
    if "deleted_at" not in columns:
        statements.append(f"ALTER TABLE novel_project ADD COLUMN deleted_at {sql_type} NULL")
    if "purge_at" not in columns:
        statements.append(f"ALTER TABLE novel_project ADD COLUMN purge_at {sql_type} NULL")
    indexes = {item["name"] for item in inspect(engine).get_indexes("novel_project")}
    if "idx_project_purge_at" not in indexes:
        statements.append("CREATE INDEX idx_project_purge_at ON novel_project (purge_at)")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)


def main():
    engine = get_engine()
    try:
        migrate_schema(engine)
        print("小说回收站字段已就绪。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
