"""创建小说章节拆解结果表（可重复执行）。

用法：python -m db.migrate_novel_analysis
无 CREATE 权限时，改用数据库管理员导入 deployment/novel-analysis.sql。
"""

from sqlalchemy import inspect, text

from db.connection import get_engine
from db.novel_analysis import (analysis_candidate, analysis_dimension, analysis_technique,
                              novel_analysis)


def migrate(engine) -> bool:
    for table in (novel_analysis, analysis_dimension, analysis_candidate, analysis_technique):
        table.create(engine, checkfirst=True)
    columns = {column["name"] for column in inspect(engine).get_columns(analysis_dimension.name)}
    if "category" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE app_analysis_dimension "
                "ADD COLUMN category VARCHAR(20) NOT NULL DEFAULT 'outline'"))
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
