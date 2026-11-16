"""Add the administrator flag without changing any existing account's privileges."""

from dotenv import load_dotenv
from sqlalchemy import inspect

from db.connection import get_engine


def migrate_schema(engine):
    inspector = inspect(engine)
    if not inspector.has_table("app_account"):
        raise ValueError("请先运行 python -m db.migrate_accounts 初始化账号表")
    if "is_admin" not in {column["name"] for column in inspector.get_columns("app_account")}:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE app_account ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE")


def main():
    load_dotenv()
    engine = get_engine()
    try:
        migrate_schema(engine)
        print("管理员字段已就绪，已有账号权限保持不变。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
