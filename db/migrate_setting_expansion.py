"""创建设定补全结果表（可重复执行）。

用法：python -m db.migrate_setting_expansion
无 CREATE 权限时，改用数据库管理员导入 deployment/setting-expansion.sql。
"""

from db.connection import get_engine
from db.setting_expansion import setting_expansion


def migrate(engine) -> bool:
    setting_expansion.create(engine, checkfirst=True)
    return True


def main() -> None:
    engine = get_engine()
    try:
        migrate(engine)
        print("设定补全结果表已就绪：app_setting_expansion")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
