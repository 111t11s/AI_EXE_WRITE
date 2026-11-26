"""创建文件选题结果表（可重复执行）。

用法：python -m db.migrate_document_topics
无 CREATE 权限时，改用数据库管理员导入 deployment/document-topics.sql。
"""

from db.connection import get_engine
from db.document_topics import document_topic


def migrate(engine) -> bool:
    document_topic.create(engine, checkfirst=True)
    return True


def main() -> None:
    engine = get_engine()
    try:
        migrate(engine)
        print("文件选题结果表已就绪：app_document_topic")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
