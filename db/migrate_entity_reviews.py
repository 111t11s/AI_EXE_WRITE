"""创建正文实体审核表（可重复执行）。"""

from db.connection import get_engine
from db.entity_reviews import entity_review


def migrate(engine) -> bool:
    entity_review.create(engine, checkfirst=True)
    return True


def main() -> None:
    engine = get_engine()
    try:
        migrate(engine)
        print("正文实体审核表已就绪：app_entity_review")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
