"""为世界观补充结构化模板列（可重复执行）。"""

from sqlalchemy import inspect, text

from db.connection import get_engine


def migrate(engine) -> bool:
    if "template" in {column["name"] for column in inspect(engine).get_columns("novel_world")}:
        return False
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE novel_world ADD COLUMN template JSON NULL COMMENT '结构化世界观模板'"))
    return True


def main() -> None:
    engine = get_engine()
    try:
        print("世界观模板列已添加" if migrate(engine) else "世界观模板列已存在")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
