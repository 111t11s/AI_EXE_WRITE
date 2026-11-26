"""创建小说章节拆解结果表（可重复执行）。

用法：python -m db.migrate_novel_analysis
无 CREATE 权限时，改用数据库管理员导入 deployment/novel-analysis.sql。
"""

from db.connection import get_engine
from db.novel_analysis import novel_analysis


def migrate(engine) -> bool:
    novel_analysis.create(engine, checkfirst=True)
    return True


def main() -> None:
    engine = get_engine()
    try:
        migrate(engine)
        print("小说拆解结果表已就绪：app_novel_analysis")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
