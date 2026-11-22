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
from api.writing_schemas import CharacterDraftList, ChapterDraft
from core.identity import AUTH_TOKEN
from main import app
from test_writing_workflow import create_test_db


def person(name, category="protagonist"):
    return dict(name=name, category=category, role="寻脉者", summary="寻脉卷中引领寻找灵脉",
                personality="谨慎但勇敢", background="山中长大", goal="守护家乡", motivation="报答乡亲",
                weakness="不愿求助", character_arc="学会信任伙伴", abilities="感知水脉，消耗灵气")


def volume_profile(context, actions="协助主角寻找渡口并渡河"):
    volume = context.get("target_volume") or context["volumes"][0]
    return dict(volume_id=volume["id"], plot_beat_ids=[row["id"] for row in volume["beats"]],
                actions=actions, outcome="成功引路并取得主角信任", progress_relation="推动渡河节点，为后续寻脉留下线索；未写部分仍为计划")


def cast(role, context, profile=None):
    request = context["request"]
    if role == agents.CORE_CHARACTERS:
        people = [person(f"主角{i}") for i in range(request["protagonist_count"])]
        people += [person(f"伙伴{i}", "important") for i in range(request["important_count"])]
    else:
        assert role == agents.SUPPORTING_CHARACTERS
        people = [person(f"舟客{i}", "supporting") for i in range(request["supporting_count"])]
    if "outline" in context:
        for item in people:
            item["volume_profiles"] = [volume_profile(context)]
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

    def generate(role, context, profile=None):
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

    def invalid(role, context, profile=None):
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
    def edit_and_generate(role, context, profile=None):
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

    def generate(role, context, profile=None):
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

    def waiting(role, context, profile=None):
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


def next_volume(novel, number=2):
    return novel["create"]("outline-volumes", outline_id=novel["outline"], volume_number=number,
                           title=f"远行卷{number}", summary="接续渡河后的旅程")


def seed_volume_cast(novel):
    response = novel["client"].post(novel["supporting"],
        json={"volume_id": novel["volume"], "supporting_count": 1}, headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    return response.json()["character_ids"][0]


def volume_cast(novel, volume_id):
    response = novel["client"].get("/api/v1/volume-characters", params={"volume_id": volume_id},
                                   headers=novel["headers"]["writer"])
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.parametrize("new_count", [0, 1])
def test_reuse_keeps_profile_and_previous_scenes_and_reaches_draft(novel, monkeypatch, new_count):
    character_id = seed_volume_cast(novel)
    original = rows(novel)[0]
    original_scenes = volume_cast(novel, novel["volume"])
    second = next_volume(novel)
    client, headers = novel["client"], novel["headers"]["writer"]
    choices = client.get(f"/api/v1/writing/volumes/{second}/reusable-characters", headers=headers)
    assert choices.status_code == 200, choices.text
    assert [row["id"] for row in choices.json()["items"]] == [character_id]
    assert choices.json()["items"][0]["previous_scenes"][0]["scenes"] == original_scenes[0]["scenes"]
    scenes = "舟客再次出场，带主角潜入渡舟盟，因立场变化引发争执。"

    def generate(role, context, profile=None):
        if role == agents.DRAFT:
            assignment = next(row for row in context["volume_characters"] if row["character_id"] == character_id)
            assert scenes in assignment["scenes"] and "阶段结果（计划）" in assignment["scenes"]
            assert any(row["id"] == character_id for row in context["characters"])
            return ChapterDraft(character_updates=[], content="舟客引领主角潜入渡舟盟，雨水沿旧船的木板流下。" * 10)
        assert context["target_volume"]["id"] == second
        assert context["reuse_characters"][0]["id"] == character_id
        assert context["reuse_characters"][0]["previous_scenes"][0]["volume_id"] == novel["volume"]
        newcomers = [dict(**person("驿使", "supporting"), volume_profiles=[volume_profile(context)])] if new_count else []
        return CharacterDraftList(characters=newcomers,
            reused_characters=[{"character_id": character_id, "scenes": scenes, "volume_profiles": [volume_profile(context, scenes)]}])

    monkeypatch.setattr(agents, "generate", generate)
    response = client.post(novel["supporting"], headers=headers,
        json={"volume_id": second, "supporting_count": new_count, "reuse_character_ids": [character_id]})
    assert response.status_code == 201, response.text
    assert response.json()["reused_character_ids"] == [character_id]
    assert response.json()["volume_id"] == second
    assert len(response.json()["character_ids"]) == new_count
    assert len(rows(novel)) == 1 + new_count
    assert next(row for row in rows(novel) if row["id"] == character_id) == original
    assert volume_cast(novel, novel["volume"]) == original_scenes
    assert scenes in next(row for row in volume_cast(novel, second) if row["character_id"] == character_id)["scenes"]
    third = next_volume(novel, 3)
    choices = client.get(f"/api/v1/writing/volumes/{third}/reusable-characters", headers=headers).json()["items"]
    history = next(row for row in choices if row["id"] == character_id)["previous_scenes"]
    assert [row["volume_number"] for row in history] == [1, 2]
    chapter = novel["create"]("chapters", project_id=novel["project"], volume_id=second,
                                chapter_number=1, title="夜入渡舟盟", summary="舟客协助潜入")
    draft = client.post(f"/api/v1/writing/chapters/{chapter}/draft", json={}, headers=headers)
    assert draft.status_code == 200, draft.text


@pytest.mark.parametrize("problem", ["first", "future", "core", "other-outline", "missing", "duplicate", "no-volume", "empty"])
def test_invalid_reuse_does_not_call_model(novel, monkeypatch, problem):
    character_id = seed_volume_cast(novel)
    second = next_volume(novel)
    payload = {"volume_id": second, "supporting_count": 0, "reuse_character_ids": [character_id]}
    if problem == "first":
        payload["volume_id"] = novel["volume"]
    elif problem == "future":
        future = novel["create"]("characters", project_id=novel["project"], name="后卷客", role="配角", summary="未来人物")
        novel["create"]("volume-characters", volume_id=next_volume(novel, 3), character_id=future, scenes="下一卷才登场")
        payload["reuse_character_ids"] = [future]
    elif problem == "core":
        core = novel["create"]("characters", project_id=novel["project"], name="主角", role="主角", summary="寻脉卷的主角")
        novel["create"]("volume-characters", volume_id=novel["volume"], character_id=core, scenes="主角")
        payload["reuse_character_ids"] = [core]
    elif problem == "other-outline":
        outline = novel["create"]("outlines", project_id=novel["project"], version_no=2, summary="另一个大纲", central_conflict="冲突")
        payload["volume_id"] = novel["create"]("outline-volumes", outline_id=outline, volume_number=2, title="另版卷", summary="另一版")
    elif problem == "missing":
        payload["reuse_character_ids"] = [99999]
    elif problem == "duplicate":
        payload["reuse_character_ids"] *= 2
    elif problem == "no-volume":
        payload.pop("volume_id")
    else:
        payload["reuse_character_ids"] = []
    calls = []
    monkeypatch.setattr(agents, "generate", lambda *args: calls.append(args))
    response = novel["client"].post(novel["supporting"], json=payload, headers=novel["headers"]["writer"])
    assert response.status_code in {404, 422}, response.text
    assert not calls and volume_cast(novel, second) == []


@pytest.mark.parametrize("problem", ["missing", "duplicate", "wrong-id"])
def test_model_must_return_exactly_selected_roles_before_any_save(novel, monkeypatch, problem):
    character_id = seed_volume_cast(novel)
    second = next_volume(novel)
    reuse = [{"character_id": character_id, "scenes": "本卷新戏份"}]
    if problem == "missing":
        reuse = []
    elif problem == "duplicate":
        reuse *= 2
    else:
        reuse[0]["character_id"] = 99999
    monkeypatch.setattr(agents, "generate", lambda *args: CharacterDraftList(
        characters=[person("新客", "supporting")], reused_characters=reuse))
    response = novel["client"].post(novel["supporting"], headers=novel["headers"]["writer"],
        json={"volume_id": second, "supporting_count": 1, "reuse_character_ids": [character_id]})
    assert response.status_code == 502, response.text
    assert len(rows(novel)) == 1 and volume_cast(novel, second) == []


def test_cast_save_failure_rolls_back_new_people_and_scenes(novel, monkeypatch):
    second = next_volume(novel)
    original = store.insert_row

    def fail_cast(connection, relation, **values):
        if relation.name == "novel_volume_character":
            raise HTTPException(409, "模拟戏份保存失败")
        return original(connection, relation, **values)

    monkeypatch.setattr(store, "insert_row", fail_cast)
    response = novel["client"].post(novel["supporting"], headers=novel["headers"]["writer"],
        json={"volume_id": second, "supporting_count": 1})
    assert response.status_code == 409, response.text
    assert rows(novel) == [] and volume_cast(novel, second) == []


def test_reuse_list_is_account_scoped_and_supports_legacy_volume_summaries(novel, monkeypatch):
    second = next_volume(novel)
    legacy = novel["create"]("characters", project_id=novel["project"], name="旧渡客", role="配角", summary="寻脉卷中帮助渡河")
    path = f"/api/v1/writing/volumes/{second}/reusable-characters"
    client = novel["client"]
    assert client.get(path).status_code == 401
    assert client.get(path, headers=novel["headers"]["other"]).status_code == 404
    response = client.get(path, headers=novel["headers"]["writer"])
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["items"]] == [legacy]
    monkeypatch.setattr(agents, "generate", lambda role, context, profile=None: CharacterDraftList(characters=[],
        reused_characters=[{"character_id": legacy, "scenes": "旧渡客在远行卷再次引路",
            "volume_profiles": [volume_profile(context, "旧渡客在远行卷再次引路")]}]))
    saved = client.post(novel["supporting"], headers=novel["headers"]["writer"],
        json={"volume_id": second, "supporting_count": 0, "reuse_character_ids": [legacy]})
    assert saved.status_code == 201, saved.text
    assert volume_cast(novel, novel["volume"])[0]["scenes"] == "寻脉卷中帮助渡河"
    third = next_volume(novel, 3)
    history = client.get(f"/api/v1/writing/volumes/{third}/reusable-characters", headers=novel["headers"]["writer"]).json()
    assert [row["volume_number"] for row in history["items"][0]["previous_scenes"]] == [1, 2]


def test_volume_cast_cannot_link_a_character_from_another_project(novel):
    project = novel["create"]("projects", title="另一部小说", genre="都市", setting="当代", tone="日常",
                                target_length="短篇", protagonist_goal="生活")
    character = novel["create"]("characters", project_id=project, name="邻居", role="配角", summary="另一部小说的人物")
    response = novel["client"].post("/api/v1/volume-characters", headers=novel["headers"]["writer"],
        json={"volume_id": novel["volume"], "character_id": character, "scenes": "不能跨小说安排戏份"})
    assert response.status_code == 409, response.text
    assert volume_cast(novel, novel["volume"]) == []


@pytest.mark.parametrize("suffix", ["", "/stream", "/tasks"])
def test_draft_without_characters_is_blocked_until_user_generates_them(novel, monkeypatch, suffix):
    chapter = novel["create"]("chapters", project_id=novel["project"], volume_id=novel["volume"],
                                chapter_number=1, title="渡河", summary="启程渡河")
    path = f"/api/v1/writing/chapters/{chapter}/draft{suffix}"
    client, headers = novel["client"], novel["headers"]["writer"]
    calls = []

    def generate(role, context, profile=None):
        calls.append(role)
        if role == agents.CORE_CHARACTERS:
            return cast(role, context)
        assert role == agents.DRAFT and context["characters"]
        return ChapterDraft(character_updates=[], content="舟客将小船撑离河岸，主角望向被雾气遮住的渡口。" * 10)

    monkeypatch.setattr(agents, "generate", generate)

    def post_draft():
        response = client.post(path, json={}, headers=headers)
        if suffix == "/tasks":
            assert response.status_code == 202, response.text
            return client.get(f'/api/v1/generation-jobs/{response.json()["id"]}/events', headers=headers)
        return response

    blocked = post_draft()
    assert "角色" in blocked.text
    assert blocked.status_code == (409 if not suffix else 200)
    if suffix:
        assert "event: error" in blocked.text
    assert calls == [] and rows(novel) == []
    saved = client.get(f"/api/v1/chapters/{chapter}", headers=headers).json()
    assert saved["status"] == "planned" and saved["content"] == ""
    generated = client.post(novel["core"], json={"protagonist_count": 1, "important_count": 1}, headers=headers)
    assert generated.status_code == 201, generated.text
    retry = post_draft()
    assert retry.status_code == 200 and ("event: result" in retry.text if suffix else retry.json()["status"] == "draft")
    assert calls == [agents.CORE_CHARACTERS, agents.DRAFT]
