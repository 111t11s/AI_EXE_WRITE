"""Explicit, server-local administrator management; never publish default credentials."""

import argparse
from getpass import getpass

from dotenv import load_dotenv
from sqlalchemy import select, update

from db.accounts import account
from db.connection import get_engine
from db.migrate_accounts import create_account
from db.migrate_admin import migrate_schema


def set_admin(engine, username, enabled=True):
    with engine.begin() as connection:
        row = connection.execute(select(account.c.id, account.c.is_active).where(
            account.c.username == username.strip().lower()).with_for_update()).mappings().one_or_none()
        if row is None:
            raise ValueError("账号不存在；可使用 --create 交互式创建")
        if enabled and not row["is_active"]:
            raise ValueError("账号已停用，不能设为管理员")
        connection.execute(update(account).where(account.c.id == row["id"]).values(is_admin=enabled))
        return row["id"]


def main():
    parser = argparse.ArgumentParser(description="创建或设置管理员；已有账号保留原密码与小说归属")
    parser.add_argument("username", help="目标用户名")
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--create", action="store_true", help="不存在时交互式创建；存在时直接设置管理员")
    options.add_argument("--revoke", action="store_true", help="撤销管理员权限")
    args = parser.parse_args()
    load_dotenv()
    engine = get_engine()
    try:
        migrate_schema(engine)
        if args.create:
            with engine.connect() as connection:
                exists = connection.scalar(select(account.c.id).where(account.c.username == args.username.strip().lower()))
            if exists is None:
                password = getpass("设置管理员密码（至少8位）：")
                if password != getpass("再次输入密码："):
                    raise ValueError("两次密码不一致")
                create_account(engine, args.username, password)
        set_admin(engine, args.username, not args.revoke)
        print("已撤销管理员权限。" if args.revoke else "管理员已设置，可登录查看全部账号的小说；其他账号小说为只读。")
    except ValueError as exc:
        parser.exit(2, f"设置未完成：{exc}\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
