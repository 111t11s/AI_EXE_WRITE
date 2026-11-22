"""创建持久化生成任务表（可重复执行）。

用法：python -m db.migrate_generation_jobs
无 CREATE 权限时，改用数据库管理员导入 deployment/generation-jobs.sql。
"""

from db.connection import get_engine
from db.generation_jobs import generation_job


def migrate(engine) -> bool:
    generation_job.create(engine, checkfirst=True)
    return True


def main() -> None:
    engine = get_engine()
    try:
        migrate(engine)
        print("生成任务持久化表已就绪：app_generation_job")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
