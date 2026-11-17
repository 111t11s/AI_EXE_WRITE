"""Recycle-bin lifecycle and project-wide cascade tests."""

from datetime import timedelta

from sqlalchemy import select, text, update

from api.project_trash_api import purge_expired_projects, utc_now
from api.novel_tables import tables
from test_accounts import graph, headers, setup_accounts


def test_deleted_project_is_hidden_and_can_be_restored(setup_accounts):
    engine, client, users = setup_accounts
    user = users["admin"]
    rows = graph(client, user)
    project_id = rows["projects"]["id"]

    response = client.delete(f"/api/v1/projects/{project_id}", headers=headers(user))
    assert response.status_code == 204
    assert all(item["id"] != project_id for item in client.get(
        "/api/v1/projects", headers=headers(user)).json()["items"])
    assert client.get(f"/api/v1/projects/{project_id}", headers=headers(user)).status_code == 404

    trash = client.get("/api/v1/projects/trash", headers=headers(user))
    assert trash.status_code == 200
    item = next(item for item in trash.json() if item["id"] == project_id)
    assert item["purge_at"]
    assert client.post(f"/api/v1/projects/{project_id}/restore", headers=headers(user)).status_code == 200
    assert any(item["id"] == project_id for item in client.get(
        "/api/v1/projects", headers=headers(user)).json()["items"])
    assert client.get("/api/v1/projects/trash", headers=headers(user)).json() == []


def test_immediate_purge_removes_project_and_every_related_record(setup_accounts):
    engine, client, users = setup_accounts
    user = users["admin"]
    rows = graph(client, user)
    project_id = rows["projects"]["id"]
    assert client.delete(f"/api/v1/projects/{project_id}", headers=headers(user)).status_code == 204
    assert client.delete(f"/api/v1/projects/{project_id}/purge", headers=headers(user)).status_code == 200

    with engine.connect() as connection:
        for table_name in (
            "novel_project", "novel_design_session", "novel_design_message", "novel_world",
            "novel_region", "novel_power_system", "novel_power_rank", "novel_cheat_system",
            "novel_faction", "novel_character", "novel_character_relationship", "novel_outline",
            "novel_outline_volume", "novel_plot_beat", "novel_volume_character", "novel_chapter",
            "novel_character_progress",
        ):
            assert connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) == 0, table_name


def test_expired_cleanup_only_purges_due_projects(setup_accounts):
    engine, client, users = setup_accounts
    user = users["admin"]
    first = graph(client, user)["projects"]["id"]
    second = graph(client, user)["projects"]["id"]
    assert client.delete(f"/api/v1/projects/{first}", headers=headers(user)).status_code == 204
    assert client.delete(f"/api/v1/projects/{second}", headers=headers(user)).status_code == 204
    with engine.begin() as connection:
        connection.execute(update(tables["novel_project"]).where(
            tables["novel_project"].c.id == first
        ).values(purge_at=utc_now() - timedelta(seconds=1)))

    assert purge_expired_projects(engine) == 1
    with engine.connect() as connection:
        assert connection.scalar(select(tables["novel_project"].c.id).where(
            tables["novel_project"].c.id == first)) is None
        assert connection.scalar(select(tables["novel_project"].c.id).where(
            tables["novel_project"].c.id == second)) == second


def test_other_account_cannot_view_or_operate_another_accounts_trash(setup_accounts):
    engine, client, users = setup_accounts
    owner = users["admin"]
    other = users["writer"]
    project_id = graph(client, owner)["projects"]["id"]
    assert client.delete(f"/api/v1/projects/{project_id}", headers=headers(owner)).status_code == 204

    assert client.get("/api/v1/projects/trash", headers=headers(other)).json() == []
    assert client.post(f"/api/v1/projects/{project_id}/restore", headers=headers(other)).status_code == 404
    assert client.delete(f"/api/v1/projects/{project_id}/purge", headers=headers(other)).status_code == 404
