"""为分卷补充伏笔与字数规划字段（可重复执行）。"""

from sqlalchemy import inspect, text

from db.connection import get_engine

COLUMNS = {
    "min_foreshadows": "INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '本卷最低伏笔数量'",
    "foreshadow_categories": "JSON NULL COMMENT '本卷要求伏笔类型'",
    "target_word_min": "INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '本卷目标字数下限'",
    "target_word_max": "INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '本卷目标字数上限'",
    "plot_progression": "LONGTEXT NULL COMMENT '本卷剧情进展规划'",
}


def migrate(engine) -> bool:
    existing = {column["name"] for column in inspect(engine).get_columns("novel_outline_volume")}
    added = False
    with engine.begin() as connection:
        for name, definition in COLUMNS.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE novel_outline_volume ADD COLUMN {name} {definition}"))
                added = True
    return added


def main() -> None:
    engine = get_engine()
    try:
        print("分卷伏笔/字数规划字段已添加" if migrate(engine) else "分卷伏笔/字数规划字段已存在")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
