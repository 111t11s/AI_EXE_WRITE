"""Explicit selection, verified citations, isolation and read-only review."""

import asyncio
import json
import threading

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from openai import OpenAI
from sqlalchemy import text

from agents import writing_agents as agents
from agents.llm_streaming import emit_generation_event
from agents.mcp.server import create_server
from agents.mcp.worldbuilding import NovelToolError
from api import novel_writing_api as writing
from api.writing_schemas import ConsistencyReport
from core.config import Settings
from test_character_generation import novel
from main import app


generate_agent = agents.generate


@pytest.fixture
def selection(novel, monkeypatch):
    create = novel["create"]
    character = create("characters", project_id=novel["project"], name="林川", role="主角",
                       summary="谨慎的寻脉者", personality="谨慎，不伤害无辜", abilities="只能感知水脉，不能凭空造水")
    ids = []
    for number in (1, 2, 3, 4):
        ids.append(create("chapters", project_id=novel["project"], volume_id=novel["volume"], chapter_number=number,
                          title=f"行舟{number}", summary="渡河寻脉", status="draft",
                          content="林川凭空造出河流。" if number == 1 else f"第{number}章正文，仅选中时可读取。"))
    novel.update(character=character, chapter_ids=ids, path=f'/api/v1/writing/projects/{novel["project"]}/consistency')
    novel["payload"] = {"outline_id": novel["outline"], "chapter_ids": [ids[3], ids[0]]}
    monkeypatch.setattr(agents, "generate", lambda role, context: report(novel))
    return novel


def report(data):
    return ConsistencyReport(verdict="drifted", summary="主角能力偏离人物档案", limitations=[], issues=[{
        "severity": "major", "category": "character", "title": "凭空造水超出能力边界",
        "evidence": [{"chapter_id": data["chapter_ids"][0], "quote": "林川凭空造出河流。"}],
        "basis": {"kind": "character", "record_id": data["character"], "field": "abilities", "quote": "不能凭空造水"},
        "explanation": "人物档案明确禁止凭空造水，正文未给出能力成长依据。", "suggestion": "改为感知并引导已有河水。",
    }])


def snapshot(data):
    with data["engine"].connect() as connection:
        return {name: [tuple(row) for row in connection.execute(text(f"SELECT * FROM {name} ORDER BY id"))]
                for name in ("novel_project", "novel_world", "novel_character", "novel_outline", "novel_outline_volume", "novel_chapter")}


def test_review_reads_only_selected_bodies_and_all_reference_material(selection, monkeypatch):
    data, seen = selection, []
    before = snapshot(data)

    def generate(role, context):
        assert role == agents.CONSISTENCY and role.tools == ()
        assert [row["chapter_number"] for row in context["selected_chapters"]] == [1, 4]
        assert context["outline"]["id"] == data["outline"]
        assert context["volumes"][0]["beats"][0]["title"] == "渡河"
        assert context["characters"][0]["abilities"] == "只能感知水脉，不能凭空造水"
        assert "第2章正文" not in json.dumps(context, ensure_ascii=False)
        assert "第3章正文" not in json.dumps(context, ensure_ascii=False)
        seen.append(context)
        return report(data)

    monkeypatch.setattr(agents, "generate", generate)
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["verdict"] == "drifted" and result["outline_version"] == 1
    assert [row["chapter_number"] for row in result["chapters"]] == [1, 4]
    assert any("不连续" in note for note in result["limitations"])
    assert result["references"] == [{"kind": "character", "record_id": data["character"], "label": "林川"}]
    assert len(seen) == 1 and snapshot(data) == before
    materials = {(row["kind"], row["record_id"]) for row in result["reference_materials"]}
    assert {("outline", data["outline"]), ("volume", data["volume"]), ("character", data["character"])} <= materials
    assert {row["kind"] for row in result["reference_materials"]} >= {"plot_beat", "world", "power_system", "power_rank", "faction"}
    assert not any(row["kind"] == "chapter" for row in result["reference_materials"])


def test_full_archives_relationship_pagination_and_other_project_isolation(selection, monkeypatch):
    data = selection
    long_background = "原始经历" * 400 + "不能泄露渡口秘密"
    data["client"].patch(f'/api/v1/characters/{data["character"]}', json={"background": long_background},
                         headers=data["headers"]["writer"]).raise_for_status()
    companion = data["create"]("characters", project_id=data["project"], name="舟客", role="向导", summary="渡河的伙伴")
    for number in range(101):
        data["create"]("character-relationships", source_character_id=companion, target_character_id=data["character"],
                       relationship_type=f"关系{number}", description=f"第{number}次同行", changed_at_chapter=1)
    cast = data["create"]("volume-characters", volume_id=data["volume"], character_id=companion, scenes="引导林川渡河")
    foreign = data["create"]("projects", title="其他小说", genre="都市", setting="现代", tone="轻松", target_length="短篇", protagonist_goal="生活")
    foreign_ids = [data["create"]("characters", project_id=foreign, name=name, role="配角", summary="不应读取") for name in ("外部甲", "外部乙")]
    data["create"]("character-relationships", source_character_id=foreign_ids[0], target_character_id=foreign_ids[1],
                   relationship_type="秘密关系", description="不应出现在核对资料中")

    def generate(role, context):
        assert next(row for row in context["characters"] if row["id"] == data["character"])["background"] == long_background
        assert len(context["character_relationships"]) == 101
        assert all(row["source_character_id"] == companion for row in context["character_relationships"])
        assert context["volumes"][0]["cast"][0]["id"] == cast
        assert "不应出现在核对资料中" not in json.dumps(context, ensure_ascii=False)
        return report(data)

    monkeypatch.setattr(agents, "generate", generate)
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 200, response.text
    materials = response.json()["reference_materials"]
    assert len([row for row in materials if row["kind"] == "character_relationship"]) == 101
    assert any(row["kind"] == "volume_character" and "舟客" in row["label"] for row in materials)


@pytest.mark.parametrize("kind,field,quote", [
    ("power_system", "limitations", "不可无中生水"),
    ("power_rank", "capabilities", "感知水流"),
    ("faction", "summary", "护送旅人"),
    ("region", "description", "渡口禁止建坝"),
    ("cheat_system", "costs", "每次耗尽灵气"),
    ("character_relationship", "description", "两人是同行伙伴"),
    ("volume_character", "scenes", "本卷负责引路"),
])
def test_related_archives_are_validated_as_real_citation_sources(selection, monkeypatch, kind, field, quote):
    data = selection
    data["create"]("regions", world_id=data["world"], name="渡口", description="渡口禁止建坝")
    data["create"]("cheat-systems", world_id=data["world"], name="听水", cheat_type="感知", costs="每次耗尽灵气")
    companion = data["create"]("characters", project_id=data["project"], name="舟客", role="向导", summary="渡河的伙伴")
    data["create"]("character-relationships", source_character_id=data["character"], target_character_id=companion,
                   relationship_type="伙伴", description="两人是同行伙伴")
    data["create"]("volume-characters", volume_id=data["volume"], character_id=companion, scenes="本卷负责引路")

    def generate(role, context):
        row = (context["character_relationships"][0] if kind == "character_relationship" else
               context["volumes"][0]["cast"][0] if kind == "volume_character" else
               context["power_ranks" if kind == "power_rank" else kind][0])
        value = report(data).model_dump()
        value["issues"][0]["basis"] = {"kind": kind, "record_id": row["id"], "field": field, "quote": quote}
        return ConsistencyReport.model_validate(value)

    monkeypatch.setattr(agents, "generate", generate)
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 200, response.text
    assert response.json()["references"][0]["kind"] == kind


def test_archive_read_failure_stops_before_model_instead_of_guessing(selection, monkeypatch):
    data, calls = selection, []
    original = writing.call_novel_tool

    async def unavailable(session, name, arguments):
        if name == "list_novel_character_relationship":
            raise NovelToolError(name)
        return await original(session, name, arguments)

    monkeypatch.setattr(writing, "call_novel_tool", unavailable)
    monkeypatch.setattr(agents, "generate", lambda *args: calls.append(args))
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 503, response.text
    assert calls == []


@pytest.mark.parametrize("problem", ["same_chapter", "workflow_status", "character_without_archive"])
def test_self_citations_and_workflow_metadata_cannot_substitute_for_archives(selection, monkeypatch, problem):
    data = selection
    value = report(data).model_dump()
    issue = value["issues"][0]
    if problem == "workflow_status":
        issue["basis"] = {"kind": "outline", "record_id": data["outline"], "field": "status", "quote": "draft"}
    else:
        issue["category"] = "continuity" if problem == "same_chapter" else "character"
        chapter_index = 0 if problem == "same_chapter" else 3
        issue["basis"] = {"kind": "chapter", "record_id": data["chapter_ids"][chapter_index], "field": "content",
                          "quote": "林川凭空造出河流。" if chapter_index == 0 else "第4章正文"}
    monkeypatch.setattr(agents, "generate", lambda *args: ConsistencyReport.model_validate(value))
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 502, response.text


def test_continuity_can_cite_another_selected_chapter(selection, monkeypatch):
    data = selection
    value = report(data).model_dump()
    value["issues"][0].update(category="continuity", basis={"kind": "chapter", "record_id": data["chapter_ids"][3],
        "field": "content", "quote": "第4章正文"})
    monkeypatch.setattr(agents, "generate", lambda *args: ConsistencyReport.model_validate(value))
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("suffix", ["", "/tasks"])
def test_foreign_accounts_and_mixed_projects_are_rejected_before_model(selection, monkeypatch, suffix):
    data = selection
    calls = []
    monkeypatch.setattr(agents, "generate", lambda *args: calls.append(args))
    assert data["client"].post(data["path"] + suffix, json=data["payload"]).status_code == 401
    assert data["client"].post(data["path"] + suffix, json=data["payload"], headers=data["headers"]["other"]).status_code == 404
    foreign = data["create"]("projects", title="另一本", genre="都市", setting="现代", tone="轻松", target_length="短篇", protagonist_goal="生活")
    foreign_chapter = data["create"]("chapters", project_id=foreign, chapter_number=1, title="另一本", content="其他小说正文")
    foreign_outline = data["create"]("outlines", project_id=foreign, summary="其他大纲", central_conflict="其他冲突")
    for payload in ({**data["payload"], "chapter_ids": [data["chapter_ids"][0], foreign_chapter]},
                    {**data["payload"], "outline_id": foreign_outline},
                    {**data["payload"], "chapter_ids": [999999]}):
        assert data["client"].post(data["path"] + suffix, json=payload, headers=data["headers"]["writer"]).status_code == 404
    assert calls == []


@pytest.mark.parametrize("ids", [[], [1, 1], list(range(1, 22))])
def test_empty_duplicate_and_excessive_selection_rejected(selection, ids):
    response = selection["client"].post(selection["path"], json={**selection["payload"], "chapter_ids": ids},
                                          headers=selection["headers"]["writer"])
    assert response.status_code == 422


@pytest.mark.parametrize("problem,status", [("empty", 409), ("large", 422), ("characters", 409), ("world", 409)])
def test_missing_material_and_large_input_do_not_call_model(selection, monkeypatch, problem, status):
    data = selection
    calls = []
    monkeypatch.setattr(agents, "generate", lambda *args: calls.append(args))
    with data["engine"].begin() as connection:
        if problem == "characters":
            connection.execute(text("DELETE FROM novel_character"))
        elif problem == "world":
            connection.execute(text("UPDATE novel_world SET is_current=0"))
        else:
            connection.execute(text("UPDATE novel_chapter SET content=:content WHERE id=:id"),
                               {"content": " " if problem == "empty" else "字" * 120001, "id": data["chapter_ids"][0]})
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == status, response.text
    assert calls == []


@pytest.mark.parametrize("problem", ["chapter", "quote", "basis_id", "basis_quote", "basis_field", "verdict", "empty_drift"])
def test_hallucinated_citations_and_contradictory_conclusions_are_rejected(selection, monkeypatch, problem):
    data = selection
    value = report(data).model_dump()
    issue = value["issues"][0]
    if problem == "chapter": issue["evidence"][0]["chapter_id"] = data["chapter_ids"][1]
    elif problem == "quote": issue["evidence"][0]["quote"] = "虚构原文"
    elif problem == "basis_id": issue["basis"]["record_id"] = 999999
    elif problem == "basis_quote": issue["basis"]["quote"] = "虚构人物能力"
    elif problem == "basis_field": issue["basis"]["field"] = "password"
    elif problem == "verdict": value["verdict"] = "consistent"
    else: value["issues"] = []
    monkeypatch.setattr(agents, "generate", lambda *args: ConsistencyReport.model_validate(value))
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 502, response.text


@pytest.mark.parametrize("verdict", ["consistent", "insufficient"])
def test_no_drift_and_insufficient_are_distinct_results(selection, monkeypatch, verdict):
    data = selection
    monkeypatch.setattr(agents, "generate", lambda *args: ConsistencyReport(
        verdict=verdict, summary="所选范围内未发现明确偏移" if verdict == "consistent" else "缺失中间章节，无法完整判断", issues=[]))
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 200 and response.json()["verdict"] == verdict


def test_scope_notes_and_model_limitations_are_all_preserved(selection, monkeypatch):
    data = selection
    with data["engine"].begin() as connection:
        connection.execute(text("UPDATE novel_chapter SET volume_id=NULL WHERE id=:id"), {"id": data["chapter_ids"][0]})
    limitations = [f"待核实资料{number}" for number in range(12)]
    monkeypatch.setattr(agents, "generate", lambda *args: ConsistencyReport(
        verdict="insufficient", summary="对应时期资料不足", issues=[], limitations=limitations))
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 200, response.text
    assert len(response.json()["limitations"]) == 17
    assert set(limitations) <= set(response.json()["limitations"])


def test_background_review_is_explicit_streamed_deduplicated_and_account_scoped(selection, monkeypatch):
    data = selection
    entered, release = threading.Event(), threading.Event()
    calls = []

    def waiting(role, context):
        calls.append(1)
        emit_generation_event("thinking", text="核对能力与剧情方向")
        emit_generation_event("delta", text='{"summary":"发现能力偏移')
        entered.set()
        assert release.wait(15)
        return report(data)

    monkeypatch.setattr(agents, "generate", waiting)
    client, headers = data["client"], data["headers"]["writer"]
    before = snapshot(data)
    assert client.get("/api/v1/chapters", headers=headers).status_code == 200
    assert calls == []
    try:
        response = client.post(data["path"] + "/tasks", json=data["payload"], headers=headers)
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert entered.wait(5)
        reverse = {**data["payload"], "chapter_ids": list(reversed(data["payload"]["chapter_ids"]))}
        assert client.post(data["path"] + "/tasks", json=reverse, headers=headers).json()["id"] == job_id
        detail = client.get(f"/api/v1/generation-jobs/{job_id}", headers=headers).json()
        assert detail["status"] == "running" and detail["thinking"] == "核对能力与剧情方向"
        assert "发现能力偏移" in detail["text"]
        assert client.get(f"/api/v1/generation-jobs/{job_id}/events", headers=data["headers"]["other"]).status_code == 404
        release.set()
        replay = client.get(f"/api/v1/generation-jobs/{job_id}/events", headers=headers)
        assert "event: result" in replay.text and "event: error" not in replay.text, replay.text
        assert calls == [1] and snapshot(data) == before
    finally:
        release.set()


def test_mcp_consistency_tool_is_read_only_and_returns_verified_report(selection):
    data = selection

    async def run():
        server = create_server("http://novel.test/api/v1", transport=httpx.ASGITransport(app=app),
                               auth_token=data["headers"]["writer"]["Authorization"].split()[1])
        async with create_connected_server_and_client_session(server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools["check_novel_consistency"].annotations.readOnlyHint is True
            response = await client.call_tool("check_novel_consistency", {"project_id": data["project"], "payload": data["payload"]})
            assert not response.isError, response
            assert response.structuredContent["verdict"] == "drifted"
    asyncio.run(run())


def test_consistency_agent_streams_via_sdk_without_browsing_other_chapters(selection, monkeypatch):
    data, requests = selection, []
    settings = Settings(_env_file=None, API_KEY="test", PROXY_URL="https://model.test/v1", MODEL_NATIVE_TOOLS=True)
    monkeypatch.setattr(agents, "get_settings", lambda: settings)
    def respond(request):
        requests.append(json.loads(request.content))
        raw = report(data).model_dump_json()
        frames = ''.join('data: ' + json.dumps({"choices": [{"delta": {"content": raw[index:index+20]}}]}) + '\n\n'
                         for index in range(0, len(raw), 20)) + 'data: [DONE]\n\n'
        return httpx.Response(200, content=frames.encode(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(agents, "get_settings", lambda: settings)
    monkeypatch.setattr(agents, "OpenAI", lambda **kwargs: OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    monkeypatch.setattr(agents, "generate", generate_agent)
    response = data["client"].post(data["path"], json=data["payload"], headers=data["headers"]["writer"])
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "drifted" and requests[0]["stream"] is True
    assert "tools" not in requests[0] and len(requests) == 1
    prompt = requests[0]["messages"][-1]["content"]
    context = json.loads(prompt.split("资料和用户要求：", 1)[1])
    assert context["outline"]["id"] == data["outline"]
    assert context["characters"][0]["abilities"] == "只能感知水脉，不能凭空造水"
    assert context["power_system"][0]["limitations"] == "不可无中生水"
    assert [row["chapter_number"] for row in context["selected_chapters"]] == [1, 4]
    assert "第2章正文" not in prompt and "第3章正文" not in prompt
