"""Chapter-scoped, evidence-backed character facts; base profiles stay intact."""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import BIGINT


metadata = MetaData()
row_id = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
for name in ("novel_project", "novel_chapter", "novel_character"):
    Table(name, metadata, Column("id", row_id, primary_key=True))

character_progress = Table(
    "novel_character_progress", metadata,
    Column("id", row_id, primary_key=True, autoincrement=True, comment="正文人物进展主键"),
    Column("project_id", row_id, ForeignKey("novel_project.id", ondelete="CASCADE"), nullable=False, index=True, comment="所属小说项目 ID"),
    Column("chapter_id", row_id, ForeignKey("novel_chapter.id", ondelete="CASCADE"), nullable=False, comment="来源章节 ID"),
    Column("character_id", row_id, ForeignKey("novel_character.id", ondelete="CASCADE"), nullable=False, index=True, comment="对应人物 ID"),
    Column("content_hash", String(64), nullable=False, comment="来源正文 SHA256，防止旧事实用于已改正文"),
    Column("actions", Text, nullable=False, comment="本章已发生的行动与结果"),
    Column("current_state", Text, nullable=False, comment="截至本章的人物状态"),
    Column("relationships", Text, nullable=False, comment="截至本章的人物关系与本章变化"),
    Column("evidence", Text, nullable=False, comment="本章正文原句数组的 JSON 文本"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp(), comment="记录创建时间"),
    UniqueConstraint("chapter_id", "character_id", name="uk_progress_chapter_character"),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", comment="正文人物进展档案表",
)
