"""Run: python -m db.migrate_character_progress (additive and idempotent)."""

from dotenv import load_dotenv
from sqlalchemy import inspect

from db.character_progress import character_progress
from db.connection import get_engine


def migrate_schema(engine):
    inspector = inspect(engine)
    if inspector.has_table(character_progress.name):
        return
    if not all(inspector.has_table(name) for name in ("novel_project", "novel_character", "novel_chapter")):
        raise ValueError("请先导入 db/schema.sql 初始化小说表")
    character_progress.create(engine, checkfirst=True)


def main():
    load_dotenv()
    engine = get_engine()
    try:
        migrate_schema(engine)
        print("正文人物进展档案表已就绪，现有小说与人物设定保持不变。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
