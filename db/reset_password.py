"""Administrator recovery: python -m db.reset_password USERNAME."""

import argparse
from getpass import getpass

from dotenv import load_dotenv
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from api.account_api import Credentials
from db.accounts import account, hash_password, replace_password
from db.connection import DatabaseNotConfigured, get_engine


def reset_password(engine, username, password):
    try:
        payload = Credentials(username=username, password=password)
    except ValidationError:
        raise ValueError("用户名格式不正确，或新密码不符合8—128位长度要求") from None
    new_hash = hash_password(payload.password.get_secret_value())
    with engine.begin() as connection:
        account_id = connection.scalar(select(account.c.id).where(account.c.username == payload.username)
                                       .with_for_update())
        if account_id is None:
            raise ValueError("账号不存在，请确认用户名；重置操作不会创建账号")
        return replace_password(connection, account_id, new_hash)


def main():
    parser = argparse.ArgumentParser(description="重置指定账号密码，并撤销该账号全部登录会话")
    parser.add_argument("username", help="需要重置的用户名")
    args = parser.parse_args()
    load_dotenv()
    engine = None
    try:
        password = getpass("输入新密码（8—128位）：")
        if password != getpass("再次输入新密码："):
            raise ValueError("两次密码不一致，未修改账号")
        engine = get_engine()
        revoked = reset_password(engine, args.username, password)
        print(f"密码已重置，已撤销 {revoked} 个登录会话，请使用新密码重新登录。")
    except ValueError as exc:
        parser.exit(2, f"重置未完成：{exc}\n")
    except (DatabaseNotConfigured, SQLAlchemyError):
        parser.exit(2, "重置未完成：数据库操作失败，请检查连接配置、账号表及数据库权限。\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(2, "\n已取消密码重置。\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
