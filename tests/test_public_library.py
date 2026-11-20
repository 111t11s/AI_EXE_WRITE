"""Publication opt-in, public reading, live updates and ownership boundaries."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, update

from api.novel_tables import tables
from db.manage_admin import set_admin
from db.migrate_public_library import migrate_schema
from test_accounts import PROJECT, graph, headers, setup_accounts


PUBLIC = "/api/v1/public/projects"


def publish(client, user, project_id, value=True):
    return client.patch(f"/api/v1/projects/{project_id}", headers=headers(user), json={"is_public": value})


def test_publication_is_opt_in_and_public_reader_exposes_only_reading_data(setup_accounts):
    _, client, users = setup_accounts
    owner, reader = users["admin"], users["writer"]
    rows = graph(client, owner)
    book_id, chapter_id = rows["projects"]["id"], rows["chapters"]["id"]
    assert rows["projects"]["is_public"] is False
    assert client.get(PUBLIC).json()["items"] == []
    for path in (f"/{book_id}", f"/{book_id}/chapters", f"/{book_id}/chapters/{chapter_id}"):
        assert client.get(PUBLIC + path).status_code == 404
    assert publish(client, owner, book_id).json()["is_public"] is True
    response = client.get(PUBLIC)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert [row["id"] for row in response.json()["items"]] == [book_id]
    public = client.get(f"{PUBLIC}/{book_id}").json()
    assert set(public) == {"id", "title", "genre", "premise", "status", "author", "chapter_count", "updated_at"}
    assert public["author"] == "admin" and public["chapter_count"] == 0
    assert client.get(f"{PUBLIC}/{book_id}/chapters").json()["items"] == []
    # Public reading never grants access to the author's private graph or writes.
    for path, row in rows.items():
        url = f"/api/v1/{path}/{row['id']}"
        assert client.get(url).status_code == 401
        assert client.get(url, headers=headers(reader)).status_code == 404
    assert client.patch(f"{PUBLIC}/{book_id}", json={"title": "被改名"}).status_code == 405


def test_only_owner_can_publish_unpublish_or_modify_a_public_book(setup_accounts):
    engine, client, users = setup_accounts
    owner, other = users["writer"], users["admin"]
    rows = graph(client, owner)
    book_id = rows["projects"]["id"]
    assert client.patch(f"/api/v1/projects/{book_id}", json={"is_public": True}).status_code == 401
    assert publish(client, other, book_id).status_code == 404
    set_admin(engine, "admin")
    assert publish(client, other, book_id).status_code == 404
    assert publish(client, owner, book_id).status_code == 200
    assert publish(client, other, book_id, False).status_code == 404
    assert client.patch(f"/api/v1/chapters/{rows['chapters']['id']}", headers=headers(other),
                        json={"content": "越权修改"}).status_code == 404
    assert client.post("/api/v1/chapters", headers=headers(other), json={
        "project_id": book_id, "chapter_number": 2, "content": "越权新增"}).status_code == 404
    assert client.delete(f"/api/v1/projects/{book_id}", headers=headers(other)).status_code == 404
    assert client.get(f"{PUBLIC}/{book_id}").status_code == 200


def test_live_chapter_and_book_changes_are_visible_without_republication(setup_accounts):
    engine, client, users = setup_accounts
    owner = users["writer"]
    rows = graph(client, owner)
    book_id, chapter_id = rows["projects"]["id"], rows["chapters"]["id"]
    publish(client, owner, book_id)
    url = f"{PUBLIC}/{book_id}"
    auth = headers(owner)
    body = client.patch(f"/api/v1/chapters/{chapter_id}", headers=auth,
                        json={"title": "新章名", "content": "更新后的正文", "word_count": 7})
    assert body.status_code == 200
    client.patch(f"/api/v1/projects/{book_id}", headers=auth, json={"title": "新书名", "premise": "新简介"})
    public = client.get(url).json()
    assert (public["title"], public["premise"], public["chapter_count"]) == ("新书名", "新简介", 1)
    listing = client.get(url + "/chapters").json()["items"]
    assert listing[0]["title"] == "新章名" and "content" not in listing[0] and "summary" not in listing[0]
    chapter_response = client.get(f"{url}/chapters/{chapter_id}")
    assert chapter_response.headers["cache-control"] == "no-store"
    assert chapter_response.json()["content"] == "更新后的正文"
    assert set(chapter_response.json()) == {"id", "title", "chapter_number", "content", "word_count", "updated_at"}
    second = client.post("/api/v1/chapters", headers=auth, json={
        "project_id": book_id, "chapter_number": 2, "content": "新增章节"}).json()
    assert client.get(url).json()["chapter_count"] == 2
    assert [row["id"] for row in client.get(url + "/chapters").json()["items"]] == [chapter_id, second["id"]]
    # SQLite lacks MySQL's ON UPDATE timestamps; set a later body timestamp explicitly.
    with engine.begin() as connection:
        connection.execute(update(tables["novel_chapter"]).where(tables["novel_chapter"].c.id == chapter_id).values(
            updated_at=datetime(2099, 1, 1)))
    assert client.get(url).json()["updated_at"].startswith("2099-01-01")
    assert client.get(PUBLIC).json()["items"][0]["updated_at"].startswith("2099-01-01")
    client.patch(f"/api/v1/chapters/{chapter_id}", headers=auth, json={"content": ""})
    assert client.get(f"{url}/chapters/{chapter_id}").status_code == 404
    assert client.get(url).json()["chapter_count"] == 1
    assert client.delete(f"/api/v1/chapters/{second['id']}", headers=auth).status_code == 204
    assert client.get(url + "/chapters").json()["items"] == []


def test_unpublish_and_trash_immediately_revoke_all_public_reads(setup_accounts):
    _, client, users = setup_accounts
    owner = users["writer"]
    rows = graph(client, owner)
    book_id, chapter_id = rows["projects"]["id"], rows["chapters"]["id"]
    client.patch(f"/api/v1/chapters/{chapter_id}", headers=headers(owner), json={"content": "公开正文"})
    paths = [f"{PUBLIC}/{book_id}", f"{PUBLIC}/{book_id}/chapters", f"{PUBLIC}/{book_id}/chapters/{chapter_id}"]
    publish(client, owner, book_id)
    assert all(client.get(path).status_code == 200 for path in paths)
    publish(client, owner, book_id, False)
    assert client.get(PUBLIC).json()["items"] == []
    assert all(client.get(path).status_code == 404 for path in paths)
    publish(client, owner, book_id)
    client.delete(f"/api/v1/projects/{book_id}", headers=headers(owner))
    assert client.get(PUBLIC).json()["items"] == []
    assert all(client.get(path).status_code == 404 for path in paths)
    client.post(f"/api/v1/projects/{book_id}/restore", headers=headers(owner))
    assert all(client.get(path).status_code == 200 for path in paths)


def test_public_pagination_order_validation_and_chapter_book_boundary(setup_accounts):
    _, client, users = setup_accounts
    owner = users["writer"]
    rows = [graph(client, owner) for _ in range(3)]
    for row in rows:
        publish(client, owner, row["projects"]["id"])
    page = client.get(PUBLIC, params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["has_more"]
    assert page["items"][0]["id"] == rows[-1]["projects"]["id"]
    last = client.get(PUBLIC, params={"limit": 1, "offset": 2}).json()
    assert last["items"][0]["id"] == rows[0]["projects"]["id"] and not last["has_more"]
    book_id = rows[0]["projects"]["id"]
    for number in (3, 2):
        client.post("/api/v1/chapters", headers=headers(owner), json={
            "project_id": book_id, "chapter_number": number, "content": "正文"})
    first = client.get(f"{PUBLIC}/{book_id}/chapters", params={"limit": 1}).json()
    assert first["items"][0]["chapter_number"] == 2 and first["has_more"]
    second = client.get(f"{PUBLIC}/{book_id}/chapters", params={"limit": 1, "offset": 1}).json()
    assert second["items"][0]["chapter_number"] == 3 and not second["has_more"]
    foreign = rows[1]["chapters"]["id"]
    client.patch(f"/api/v1/chapters/{foreign}", headers=headers(owner), json={"content": "别书正文"})
    assert client.get(f"{PUBLIC}/{book_id}/chapters/{foreign}").status_code == 404
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert client.get(f"{PUBLIC}?{query}").status_code == 422
    assert client.get(f"{PUBLIC}/0").status_code == 422


def test_public_library_migration_is_idempotent_and_preserves_privacy():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE novel_project (id INTEGER PRIMARY KEY, title TEXT, updated_at DATETIME)")
            connection.exec_driver_sql("INSERT INTO novel_project (title) VALUES ('旧小说')")
        migrate_schema(engine)
        migrate_schema(engine)
        with engine.begin() as connection:
            assert tuple(connection.exec_driver_sql("SELECT title, is_public FROM novel_project").one()) == ("旧小说", 0)
            connection.exec_driver_sql("UPDATE novel_project SET is_public=1")
        migrate_schema(engine)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT is_public FROM novel_project").scalar() == 1
        assert "idx_project_public_updated" in {item["name"] for item in inspect(engine).get_indexes("novel_project")}
    finally:
        engine.dispose()


def test_public_library_migration_requires_initialized_schema():
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(ValueError, match="初始化"):
            migrate_schema(engine)
    finally:
        engine.dispose()
