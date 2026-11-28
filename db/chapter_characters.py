"""章节规划分配的出场角色。"""

from sqlalchemy import (BigInteger, Column, ForeignKey, Integer, Table, UniqueConstraint, select)
from sqlalchemy.dialects.mysql import BIGINT

from db.accounts import metadata


id_type = BigInteger().with_variant(BIGINT(unsigned=True), "mysql").with_variant(Integer, "sqlite")
chapter_character = Table(
    "novel_chapter_character", metadata,
    Column("id", id_type, primary_key=True, autoincrement=True),
    Column("chapter_id", id_type, ForeignKey("novel_chapter.id", ondelete="CASCADE"), nullable=False),
    Column("character_id", id_type, ForeignKey("novel_character.id", ondelete="CASCADE"), nullable=False),
    UniqueConstraint("chapter_id", "character_id", name="uk_chapter_character"),
)


def ids_for_chapter(engine, chapter_id: int) -> list[int]:
    with engine.begin() as connection:
        return [int(value) for value in connection.scalars(select(chapter_character.c.character_id).where(
            chapter_character.c.chapter_id == chapter_id).order_by(chapter_character.c.id))]
