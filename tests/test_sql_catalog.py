from contextlib import contextmanager
from importlib import import_module

import pytest
from fastapi.testclient import TestClient

from api import novel_story_memory_api
from db.DataBaseManager import DatabaseManager
from db.sql import mapped_sql


class RecordingCursor:
    lastrowid = 17
    rowcount = 1

    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return [{"id": 17}]

    def fetchone(self):
        return {"total": 1}


def manager_with_cursor(monkeypatch):
    cursor = RecordingCursor()

    class Connection:
        def cursor(self):
            return cursor

    @contextmanager
    def connection():
        yield Connection()

    manager = DatabaseManager()
    monkeypatch.setattr(manager, "get_connection", connection)
    return manager, cursor


def test_named_mapper_queries_bind_values(monkeypatch):
    manager, cursor = manager_with_cursor(monkeypatch)

    assert manager.insert("novel_book", {"title": "A"}) == 17
    assert cursor.calls[-1] == ("INSERT INTO novel_book (title) VALUES (%s)", ["A"])

    manager.select_by_id("novel_book", 17)
    assert cursor.calls[-1] == ("SELECT * FROM novel_book WHERE id = %s", [17])

    manager.update_by_id("novel_book", 17, {"title": "B"})
    assert cursor.calls[-1] == ("UPDATE novel_book SET title = %s WHERE id = %s", ["B", 17])

    manager.delete_by_id("novel_book", 17)
    assert cursor.calls[-1] == ("DELETE FROM novel_book WHERE id = %s", [17])

    manager.select_list("novel_story_memory", where={"book_id": 4},
                        minimums={"importance": 2},
                        order_by="importance DESC, chapter_number DESC", limit=10, offset=5)
    assert cursor.calls[-1] == (
        "SELECT * FROM novel_story_memory WHERE book_id = %s AND importance >= %s "
        "ORDER BY importance DESC, chapter_number DESC LIMIT %s OFFSET %s",
        [4, 2, 10, 5],
    )

    assert manager.count("novel_story_memory", where={"book_id": 4},
                         minimums={"importance": 2}) == 1
    assert cursor.calls[-1] == (
        "SELECT COUNT(*) AS total FROM novel_story_memory "
        "WHERE book_id = %s AND importance >= %s", [4, 2]
    )

    manager.select_list("novel_review", where={"book_id": 4, "review_type": 7},
                        order_by="alert_severity DESC, created_at DESC", limit=None)
    assert cursor.calls[-1] == (
        "SELECT * FROM novel_review WHERE book_id = %s AND review_type = %s "
        "ORDER BY alert_severity DESC, created_at DESC", [4, 7]
    )


def test_sql_identifiers_and_statement_names_are_restricted(monkeypatch):
    manager, cursor = manager_with_cursor(monkeypatch)

    with pytest.raises(ValueError):
        manager.select_by_id("novel_book; DROP TABLE novel_book", 1)
    with pytest.raises(ValueError):
        manager.select_list("novel_book", order_by="id DESC; DROP TABLE novel_book")
    with pytest.raises(ValueError):
        manager.count("novel_book", where={"id OR 1=1": 1})
    with pytest.raises(ValueError):
        mapped_sql("../core/config", "get", {"id": 1})
    assert cursor.calls == []


@pytest.mark.parametrize("resource", [
    "book", "world", "faction", "character", "outline", "chapter",
    "story_memory", "review", "generation_task", "generation_log",
])
def test_each_resource_mapper_covers_its_api_fields(resource):
    module = import_module(f"api.novel_{resource}_api")
    models = [obj for obj in vars(module).values()
              if isinstance(obj, type) and obj.__module__ == module.__name__]
    create_model = next(obj for obj in models if obj.__name__.endswith("Create"))
    update_model = next(obj for obj in models if obj.__name__.endswith("Update"))

    for statement, values in (
        ("create", {key: 1 for key in create_model.model_fields}),
        ("get", {"id": 1}),
        ("list", {"limit": 20, "offset": 0,
                  **({} if resource == "book" else {"book_id": 1})}),
        ("count", {} if resource == "book" else {"book_id": 1}),
        ("update", {"id": 1, **{key: 1 for key in update_model.model_fields}}),
        ("delete", {"id": 1}),
    ):
        sql, params = mapped_sql(module.TABLE, statement, values)
        assert sql.count("%s") == len(params)
        assert "#{" not in sql
        assert ",)" not in sql

    required = {key: 1 for key, field in create_model.model_fields.items()
                if field.is_required()}
    minimal_sql, minimal_params = mapped_sql(module.TABLE, "create", required)
    assert minimal_sql.count("%s") == len(minimal_params)
    assert ",)" not in minimal_sql


def test_special_endpoint_statements_are_separate():
    for table, statement, values in (
        ("novel_book", "update_phase", {"id": 1, "current_phase": "WRITING"}),
        ("novel_character", "update_state", {"id": 1, "state_history": "[]"}),
        ("novel_character", "by_faction", {"book_id": 1, "faction_id": 2,
                                            "limit": 100, "offset": 0}),
        ("novel_generation_task", "update_status", {"id": 1, "status": "RUNNING"}),
    ):
        sql, params = mapped_sql(table, statement, values)
        assert sql.count("%s") == len(params)


@pytest.mark.parametrize("table,statement,values,expected_params", [
    ("novel_chapter", "latest", {"book_id": 3}, [3]),
    ("novel_chapter", "word_count_total", {"book_id": 3}, [3]),
    ("novel_outline", "previous", {"book_id": 3, "sort_order": 5}, [3, 5]),
    ("novel_story_memory", "context_inclusive",
     {"book_id": 3, "chapter_number": 5, "memory_type": "FACT", "limit": 20},
     [3, 5, "FACT", 20]),
    ("novel_story_memory", "context_exclusive",
     {"book_id": 3, "chapter_number": 5, "memory_type": "FACT", "limit": 20},
     [3, 5, "FACT", 20]),
    ("novel_generation_log", "stats", {"book_id": 3}, [3]),
    ("novel_generation_log", "skill_stats", {"book_id": 3}, [3]),
    ("legacy", "fetch_chapter", {"id": 3}, [3]),
    ("legacy", "save_chapter", {"id": 3, "content": "text", "status": "draft"},
     [3, "text", "draft", "text", "draft"]),
    ("legacy", "show_tables", {}, []),
    ("legacy", "show_columns", {"table_name": "novel_book"}, ["novel_book"]),
])
def test_migrated_queries_are_in_xml(table, statement, values, expected_params):
    sql, params = mapped_sql(table, statement, values)
    assert sql.count("%s") == len(expected_params)
    assert params == expected_params


def test_story_memory_http_filter_uses_named_mapper(monkeypatch):
    manager, cursor = manager_with_cursor(monkeypatch)
    monkeypatch.setattr(novel_story_memory_api, "db", manager)
    from main import app

    response = TestClient(app).get(
        "/api/v1/story-memories",
        params={"book_id": 4, "memory_type": "FACT", "min_importance": 2},
    )

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert cursor.calls[0] == (
        "SELECT * FROM novel_story_memory WHERE book_id = %s AND memory_type = %s "
        "AND importance >= %s ORDER BY importance DESC, chapter_number DESC "
        "LIMIT %s OFFSET %s",
        [4, "FACT", 2, 20, 0],
    )
