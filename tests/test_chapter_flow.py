from copy import deepcopy

import pytest
from fastapi import HTTPException

from api import novel_chapter_api
from db.sql import mapped_sql
from agent.skill.storage import ChapterSkill


class FakeDatabase:
    def __init__(self):
        self.rows = {
            "novel_book": {1: {"id": 1, "last_chapter_number": 0, "total_word_count": 0}},
            "novel_outline": {2: {"id": 2, "book_id": 2}},
            "novel_chapter": {},
        }
        self.next_id = 1

    def select_by_id(self, table, record_id):
        return deepcopy(self.rows[table].get(record_id))

    def select_list(self, table, where=None, order_by="id DESC", limit=20, offset=0):
        rows = [deepcopy(row) for row in self.rows[table].values()
                if all(row.get(key) == value for key, value in (where or {}).items())]
        key, direction = order_by.split()
        rows.sort(key=lambda row: row[key], reverse=direction == "DESC")
        return rows[offset:offset + limit]

    def insert(self, table, data):
        record_id = self.next_id
        self.next_id += 1
        self.rows[table][record_id] = {"id": record_id, "version": 1, **data}
        return record_id

    def update_by_id(self, table, record_id, data):
        self.rows[table][record_id].update(data)
        return 1

    def delete_by_id(self, table, record_id):
        del self.rows[table][record_id]
        return 1

    def execute_sql(self, sql, params):
        chapters = [row for row in self.rows["novel_chapter"].values()
                    if row["book_id"] == params[0]]
        if "SUM(word_count)" in sql:
            return [{"total": sum(row["word_count"] for row in chapters)}]
        return [deepcopy(max(chapters, key=lambda row: row["chapter_number"]))] if chapters else []

    def execute_mapped(self, table, statement, values):
        sql, params = mapped_sql(table, statement, values)
        return self.execute_sql(sql, params)


def test_chapter_writes_keep_book_progress_and_version_in_sync(monkeypatch):
    fake_db = FakeDatabase()
    skill = ChapterSkill()
    skill.db = fake_db
    monkeypatch.setattr(novel_chapter_api, "db", fake_db)
    monkeypatch.setattr(novel_chapter_api, "chapter_skill", skill)

    created = novel_chapter_api.create_chapter(
        novel_chapter_api.ChapterCreate(book_id=1, chapter_number=1, content="abc", word_count=999)
    )["data"]
    assert created["word_count"] == 3
    assert fake_db.rows["novel_book"][1]["total_word_count"] == 3
    assert fake_db.rows["novel_book"][1]["last_chapter_number"] == 1

    updated = novel_chapter_api.update_chapter(
        created["id"], novel_chapter_api.ChapterUpdate(content="abcde", word_count=999, version=99)
    )["data"]
    assert (updated["word_count"], updated["version"]) == (5, 2)
    assert fake_db.rows["novel_book"][1]["total_word_count"] == 5

    novel_chapter_api.delete_chapter(created["id"])
    assert fake_db.rows["novel_book"][1]["total_word_count"] == 0
    assert fake_db.rows["novel_book"][1]["last_chapter_number"] == 0


def test_chapter_rejects_outline_from_another_book(monkeypatch):
    fake_db = FakeDatabase()
    monkeypatch.setattr(novel_chapter_api, "db", fake_db)

    with pytest.raises(HTTPException) as exc:
        novel_chapter_api.create_chapter(
            novel_chapter_api.ChapterCreate(book_id=1, chapter_number=1, outline_id=2)
        )
    assert exc.value.status_code == 404
    assert fake_db.rows["novel_chapter"] == {}


def test_skill_rejects_cross_book_outline_on_create_and_update():
    fake_db = FakeDatabase()
    skill = ChapterSkill()
    skill.db = fake_db

    with pytest.raises(ValueError, match="大纲不存在或不属于该书籍"):
        skill.create_chapter(book_id=1, chapter_number=1, outline_id=2)

    chapter = skill.create_chapter(book_id=1, chapter_number=1)
    with pytest.raises(ValueError, match="大纲不存在或不属于该书籍"):
        skill.update_chapter(chapter["id"], outline_id=2)
    assert "outline_id" not in fake_db.select_by_id("novel_chapter", chapter["id"])
