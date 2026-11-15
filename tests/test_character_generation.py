"""Character agents use scoped MCP data and save a complete cast atomically."""

import asyncio
import threading
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from agents.mcp.server import create_server
from api import generation_jobs, novel_writing_api as writing, novel_writing_store as store
from api.database import get_database
from api.writing_schemas import CharacterDraftList
from core.identity import AUTH_TOKEN
from main import app
from test_writing_workflow import create_test_db


def person(name, category="protagonist"):
    return dict(name=name, category=category, role="寻脉者", summary="寻脉卷中引领寻找灵脉",
                personality="谨慎但勇敢", background="山中长大", goal="守护家乡", motivation="报答乡亲",
                weakness="不愿求助", character_arc="学会信任伙伴", abilities="感知水脉，消耗灵气")


def cast(role, context):
    request = context["request"]
    if role == agents.CORE_CHARACTERS:
        people = [person(f"主角{i}") for i in range(request["protagonist_count"])]
        people += [person(f"伙伴{i}", "important") for i in range(request["important_count"])]
    else:
        assert role == agents.SUPPORTING_CHARACTERS
        people = [person(f"舟客{i}", "supporting") for i in range(request["supporting_count"])]
    return CharacterDraftList(characters=people)


@pytest.fixture
def novel(monkeypatch):
    engine = create_test_db()
    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: engine)
    monkeypatch.setattr(generation_jobs, "store", generation_jobs.JobStore())

    @asynccontextmanager
    async def session():
        server = create_server("http://novel.test/api/v1", transport=httpx.ASGITransport(app=app),
                               auth_token=AUTH_TOKEN.get())
        async with create_connected_server_and_client_session(server) as client:
            yield client

    monkeypatch.setattr(writing, "novel_session", session)
    monkeypatch.setattr(agents, "generate", cast)
    with TestClient(app) as client:
        tokens = {}
        for name in ("writer", "other"):
            result = client.post("/api/v1/auth/register", json={"username": name, "password": "test-password"})
            assert result.status_code == 201, result.text
            tokens[name] = {"Authorization": "Bearer " + result.json()["access_token"]}

        def create(path, **values):
            result = client.post("/api/v1/" + path, json=values, headers=tokens["writer"])
            assert result.status_code == 201, result.text
            return result.json()["id"]

        project = create("projects", title="寻脉", genre="仙侠", setting="山海", tone="热血",
                         target_length="短篇", protagonist_goal="守护家乡")
        world = create("worlds", project_id=project, summary="灵气衰竭", era="古代", rules="施法消耗灵气")
        power = create("power-systems", world_id=world, name="御水", source="灵脉", limitations="不可无中生水")
        create("power-ranks", power_system_id=power, rank_order=1, name="感脉", capabilities="感知水流")
        create("factions", world_id=world, name="渡舟盟", summary="护送旅人")
        outline = create("outlines", project_id=project, summary="寻找灵脉", central_conflict="宗门垄断", themes="信任")
        volume = create("outline-volumes", outline_id=outline, volume_number=1, title="寻脉卷", summary="渡河寻脉")
        create("plot-beats", volume_id=volume, sort_order=1, title="渡河", summary="舟客帮助渡河")
        yield dict(client=client, headers=tokens, engine=engine, create=create, project=project, world=world,
                   outline=outline, volume=volume, core=f"/api/v1/writing/projects/{project}/characters/core",
                   supporting=f"/api/v1/writing/outlines/{outline}/characters/supporting")
    engine.dispose()


def rows(novel):
    result = novel["client"].get("/api/v1/characters", params={"project_id": novel["project"], "limit": 100},
                                 headers=novel["headers"]["writer"])
    assert result.status_code == 200, result.text
    return result.json()["items"]


def test_two_stages_read_real_scoped_mcp_context_and_save(novel, monkeypatch):
    contexts = []

    def generate(role, context):
        contexts.append(context)
        assert context["world"]["rules"] == "施法消耗灵气"
        assert context["power_system"][0]["limitations"] == "不可无中生水"
        assert context["power_ranks"][0]["name"] == "感脉"
        assert context["faction"][0]["name"] == "渡舟盟"
        if role == agents.SUPPORTING_CHARACTERS:
            assert context["outline"]["id"] == novel["outline"]
            assert context["volumes"][0]["beats"][0]["title"] == "渡河"
            assert len(context["characters"]) == 4
            assert any(row["role"].startswith("主角") for row in context["characters"])
        return cast(role, context)

    monkeypatch.setattr(agents, "generate", generate)
    client, headers = novel["client"], novel["headers"]["writer"]
    core = client.post(novel["core"], json={}, headers=headers)
    assert core.status_code == 201, core.text
    assert core.json()["counts"] == {"protagonist": 1, "important": 3}
    supporting = client.post(novel["supporting"], json={"supporting_count": 2}, headers=headers)
    assert supporting.status_code == 201, supporting.text
    assert supporting.json()["counts"] == {"supporting": 2}
    saved = rows(novel)
    assert len(saved) == 6 and len(contexts) == 2
    assert all(row["personality"] and row["character_arc"] and row["faction_id"] is None for row in saved)


@pytest.mark.parametrize("endpoint", ["core", "supporting"])
@pytest.mark.parametrize("suffix", ["", "/tasks"])
def test_character_generation_is_account_scoped(novel, monkeypatch, endpoint, suffix):
    def forbidden(*args):
        raise AssertionError("Must not call model for unauthorized records")

    monkeypatch.setattr(agents, "generate", forbidden)
    client = novel["client"]
    assert client.post(novel[endpoint] + suffix, json={}).status_code == 401
    assert client.post(novel[endpoint] + suffix, json={}, headers=novel["headers"]["other"]).status_code == 404
    assert rows(novel) == []


def test_missing_world_and_outline_and_invalid_count_do_not_generate(novel, monkeypatch):
    calls = []
    monkeypatch.setattr(agents, "generate", lambda *args: calls.append(args))
    client, headers = novel["client"], novel["headers"]["writer"]
    assert client.post(novel["core"], json={"protagonist_count": 0}, headers=headers).status_code == 422
    assert client.post(novel["supporting"], json={"supporting_count": 13}, headers=headers).status_code == 422
    assert client.post("/api/v1/writing/outlines/99999/characters/supporting", json={}, headers=headers).status_code == 404
    assert client.patch(f'/api/v1/worlds/{novel["world"]}', json={"is_current": False}, headers=headers).status_code == 200
    assert client.post(novel["core"], json={}, headers=headers).status_code == 409
    assert client.post(novel["supporting"], json={}, headers=headers).status_code == 409
    assert calls == [] and rows(novel) == []


@pytest.mark.parametrize("problem,status", [("count", 502), ("category", 502), ("duplicate", 502), ("existing", 409)])
def test_invalid_cast_is_not_partially_saved(novel, monkeypatch, problem, status):
    if problem == "existing":
        novel["create"]("characters", project_id=novel["project"], name="主角0", role="已有角色", summary="手写档案")

    def invalid(role, context):
        artifact = cast(role, context)
        if problem == "count":
            artifact.characters.pop()
        elif problem == "category":
            artifact.characters[-1].category = "supporting"
        elif problem == "duplicate":
            artifact.characters[-1].name = artifact.characters[0].name
        return artifact

    monkeypatch.setattr(agents, "generate", invalid)
    result = novel["client"].post(novel["core"], json={}, headers=novel["headers"]["writer"])
    assert result.status_code == status, result.text
    saved = rows(novel)
    assert len(saved) == int(problem == "existing")
    if saved:
        assert saved[0]["summary"] == "手写档案"


def test_save_failure_rolls_back_whole_batch(novel, monkeypatch):
    insert = store.insert_row
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise HTTPException(409, "模拟第二位角色保存失败")
        return insert(*args, **kwargs)

    monkeypatch.setattr(store, "insert_row", fail_second)
    result = novel["client"].post(novel["core"], json={}, headers=novel["headers"]["writer"])
    assert result.status_code == 409 and len(calls) == 2
    assert rows(novel) == []


@pytest.mark.parametrize("source", ["world", "volume"])
def test_changed_source_rejects_stale_cast(novel, monkeypatch, source):
    def edit_and_generate(role, context):
        table = store.tables["novel_world" if source == "world" else "novel_outline_volume"]
        with novel["engine"].begin() as connection:
            connection.execute(table.update().where(table.c.id == novel[source]).values(summary="编辑后的新资料"))
        return cast(role, context)

    monkeypatch.setattr(agents, "generate", edit_and_generate)
    result = novel["client"].post(novel["supporting"], json={}, headers=novel["headers"]["writer"])
    assert result.status_code == 409, result.text
    assert rows(novel) == []


def test_existing_characters_are_paginated_without_missing_names(novel, monkeypatch):
    relation = store.tables["novel_character"]
    with novel["engine"].begin() as connection:
        for number in range(105):
            values = person(f"旧角色{number}")
            values.pop("category")
            store.insert_row(connection, relation, project_id=novel["project"], faction_id=None,
                             first_appearance_chapter=None, status="active", **values)
    seen = []

    def generate(role, context):
        seen.extend(row["name"] for row in context["characters"])
        return cast(role, context)

    monkeypatch.setattr(agents, "generate", generate)
    result = novel["client"].post(novel["core"], json={}, headers=novel["headers"]["writer"])
    assert result.status_code == 201, result.text
    assert len(seen) == 105 and "旧角色104" in seen


@pytest.mark.parametrize("endpoint", ["core", "supporting"])
def test_background_character_task_streams_before_saving(novel, monkeypatch, endpoint):
    entered, release = threading.Event(), threading.Event()
    original = cast

    def waiting(role, context):
        emit_generation_event("thinking", text="核对人物动机")
        emit_generation_event("delta", text='{"characters":[')
        entered.set()
        assert release.wait(10)
        return original(role, context)

    monkeypatch.setattr(agents, "generate", waiting)
    client, headers = novel["client"], novel["headers"]["writer"]
    try:
        result = client.post(novel[endpoint] + "/tasks", json={}, headers=headers)
        assert result.status_code == 202, result.text
        job_id = result.json()["id"]
        assert entered.wait(5) and rows(novel) == []
        detail = client.get(f"/api/v1/generation-jobs/{job_id}", headers=headers).json()
        assert detail["status"] == "running" and detail["thinking"] == "核对人物动机"
        assert detail["text"] == '{"characters":['
        assert client.post(novel[endpoint] + "/tasks", json={}, headers=headers).json()["id"] == job_id
        release.set()
        replay = client.get(f"/api/v1/generation-jobs/{job_id}/events", headers=headers)
        assert "event: result" in replay.text and "event: error" not in replay.text, replay.text
        assert len(rows(novel)) == (4 if endpoint == "core" else 6)
        assert client.get(f"/api/v1/generation-jobs/{job_id}", headers=novel["headers"]["other"]).status_code == 404
    finally:
        release.set()


def test_new_mcp_workflows_save_and_return_character_ids(novel):
    async def run():
        server = create_server("http://novel.test/api/v1", transport=httpx.ASGITransport(app=app),
                               auth_token=novel["headers"]["writer"]["Authorization"].split()[1])
        async with create_connected_server_and_client_session(server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            for name, args, count in [
                ("generate_novel_core_characters", {"project_id": novel["project"], "payload": {"important_count": 1}}, 2),
                ("generate_novel_supporting_characters", {"outline_id": novel["outline"], "payload": {"supporting_count": 2}}, 2),
            ]:
                assert tools[name].annotations.destructiveHint is False
                result = await client.call_tool(name, args)
                assert not result.isError, result
                assert len(result.structuredContent["character_ids"]) == count
    asyncio.run(run())
    assert len(rows(novel)) == 4
