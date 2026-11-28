"""创建章节出场角色表（可重复执行）。"""

from db.chapter_characters import chapter_character
from db.connection import get_engine


def migrate(engine) -> bool:
    chapter_character.create(engine, checkfirst=True)
    return True


def main() -> None:
    engine = get_engine()
    try:
        print("章节出场角色表已就绪" if migrate(engine) else "章节出场角色表已存在")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
