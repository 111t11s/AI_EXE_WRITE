"""创建伏笔和杂记表（可重复执行）。"""

from db.connection import get_engine
from db.foreshadows import foreshadow
from db.misc_notes import misc_note


def migrate(engine) -> bool:
    foreshadow.create(engine, checkfirst=True)
    misc_note.create(engine, checkfirst=True)
    return True


def main() -> None:
    engine = get_engine()
    try:
        migrate(engine)
        print("伏笔与杂记表已就绪：novel_foreshadow、novel_misc_note")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
