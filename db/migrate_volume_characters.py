"""Add the per-volume cast table without changing existing novel data.

Run from the project root: python -m db.migrate_volume_characters
"""

from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import inspect

from db.connection import get_engine


def migrate_schema(engine):
    inspector = inspect(engine)
    if inspector.has_table("novel_volume_character"):
        return
    if not all(inspector.has_table(name) for name in ("novel_character", "novel_outline_volume")):
        raise ValueError("请先导入 db/schema.sql 初始化小说表")
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    start = schema.index("CREATE TABLE IF NOT EXISTS novel_volume_character (")
    statement = schema[start:schema.index(";", start) + 1]
    with engine.begin() as connection:
        connection.exec_driver_sql(statement)


if __name__ == "__main__":
    load_dotenv()
    migrate_schema(get_engine())
    print("分卷角色戏份表已就绪。")
