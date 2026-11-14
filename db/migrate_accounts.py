"""Add account ownership in place and optionally bind legacy novels.

Run from the project root: python -m db.migrate_accounts --legacy-owner admin
Optional --create-account admin prompts for a password without echoing it.
No existing account, password, owner or novel is overwritten.
"""

import argparse
from getpass import getpass

from dotenv import load_dotenv
from pydantic import ValidationError
from sqlalchemy import inspect, insert, or_, select, update

from api.account_api import Registration
from api.novel_tables import tables
from db.accounts import account, hash_password, metadata
from db.connection import get_engine


def migrate_schema(engine):
    metadata.create_all(engine)
    for name, index, constraint in (
        ("novel_project", "idx_project_account", "fk_project_account"),
        ("novel_design_session", "idx_design_account", "fk_design_account"),
    ):
        inspector = inspect(engine)
        if not inspector.has_table(name):
            raise ValueError("请先导入 db/schema.sql 初始化小说表")
        if "account_id" not in {column["name"] for column in inspector.get_columns(name)}:
            sql_type = "BIGINT UNSIGNED" if engine.dialect.name == "mysql" else "BIGINT"
            with engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE {name} ADD COLUMN account_id {sql_type} NULL")
        if index not in {item["name"] for item in inspect(engine).get_indexes(name)}:
            with engine.begin() as connection:
                connection.exec_driver_sql(f"CREATE INDEX {index} ON {name} (account_id)")
        if engine.dialect.name == "mysql" and constraint not in {
            item["name"] for item in inspect(engine).get_foreign_keys(name)
        }:
            with engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE {name} ADD CONSTRAINT {constraint} "
                                          "FOREIGN KEY (account_id) REFERENCES app_account(id)")


def create_account(engine, username, password):
    try:
        payload = Registration(username=username, password=password)
    except ValidationError:
        raise ValueError("用户名应为3—32位字母、数字或下划线等字符；密码应为8—128位") from None
    with engine.begin() as connection:
        if connection.scalar(select(account.c.id).where(account.c.username == payload.username)) is not None:
            raise ValueError("账号已存在，不会覆盖已有密码")
        return connection.execute(insert(account).values(username=payload.username,
            display_name=payload.username, password_hash=hash_password(password))).lastrowid


def bind_legacy(engine, username):
    username = username.strip().lower()
    project, session = tables["novel_project"], tables["novel_design_session"]
    with engine.begin() as connection:
        owner = connection.scalar(select(account.c.id).where(account.c.username == username,
                                                              account.c.is_active.is_(True)))
        if owner is None:
            raise ValueError("目标账号不存在或已停用；请先注册，或使用 --create-account 创建")
        projects = connection.execute(update(project).where(project.c.account_id.is_(None)).values(account_id=owner))
        sessions = connection.execute(update(session).where(session.c.account_id.is_(None), or_(
            session.c.project_id.is_(None), session.c.project_id.in_(select(project.c.id).where(project.c.account_id == owner))
        )).values(account_id=owner))
        return {"projects": projects.rowcount, "sessions": sessions.rowcount}


def main():
    parser = argparse.ArgumentParser(description="创建账号表并迁移小说归属（保留现有数据）")
    parser.add_argument("--create-account", metavar="USERNAME", help="交互式创建账号，不在命令行提供密码")
    parser.add_argument("--legacy-owner", metavar="USERNAME", help="将未分配小说和会话统一绑定给此账号")
    args = parser.parse_args()
    load_dotenv()
    engine = get_engine()
    try:
        migrate_schema(engine)
        print("账号表与归属字段已就绪。")
        if args.create_account:
            with engine.connect() as connection:
                existing = connection.scalar(select(account.c.id).where(
                    account.c.username == args.create_account.strip().lower()))
            if existing is None:
                password = getpass("设置账号密码（至少8位）：")
                if password != getpass("再次输入密码："):
                    raise ValueError("两次密码不一致")
                create_account(engine, args.create_account, password)
                print("账号已创建。")
            else:
                print("账号已存在，保留原密码。")
        if args.legacy_owner:
            counts = bind_legacy(engine, args.legacy_owner)
            print(f"旧数据归属迁移完成：小说 {counts['projects']} 部，会话 {counts['sessions']} 个。")
    except ValueError as exc:
        parser.exit(2, f"迁移未完成：{exc}\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
