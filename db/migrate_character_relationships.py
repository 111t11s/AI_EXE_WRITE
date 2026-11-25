"""Create the character relationship table for existing installations.

Run from the project root: python -m db.migrate_character_relationships
"""

from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import inspect

from db.connection import get_engine


def migrate_schema(engine):
    inspector = inspect(engine)
    if inspector.has_table("novel_character_relationship"):
        return
    if not inspector.has_table("novel_character"):
        raise ValueError("请先导入 db/schema.sql 初始化小说表")
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    start = schema.index("CREATE TABLE IF NOT EXISTS novel_character_relationship (")
    statement = schema[start:schema.index(";", start) + 1]
    with engine.begin() as connection:
        connection.exec_driver_sql(statement)


if __name__ == "__main__":
    load_dotenv()
    migrate_schema(get_engine())
    print("角色关系表已就绪。")
