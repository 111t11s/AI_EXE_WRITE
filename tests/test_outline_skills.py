"""Verify genre routing, source traceability and actual generation prompts offline."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import OpenAI

from agents import outline_skills as skills, writing_agents as writing, worldbuilding_agent as world
from api import novel_assistant_api as assistant
from api.account_api import Account, get_current_account


@pytest.mark.parametrize("genre,expected", [
    ("玄幻", ["outline-xuanhuan"]), ("东方玄幻", ["outline-xuanhuan"]),
    ("都市异能", ["outline-urban"]), ("武侠", ["outline-wuxia"]),
    ("仙侠", ["outline-xianxia"]), ("修真修仙", ["outline-xianxia"]),
    ("都市修仙", ["outline-urban", "outline-xianxia"]),
    ("仙侠融合玄幻", ["outline-xianxia", "outline-xuanhuan"]),
    ("科幻", []), ("奇幻", []), ("蒸汽朋克探险", []), ("../../README.md", []),
])
def test_genre_selection_loads_only_relevant_skills(genre, expected):
    selected = skills.outline_skill_selection(genre)
    assert [item["id"] for item in selected["skills"]] == ["outline-general", *expected]
    assert selected["specialized"] is bool(expected)
    assert all(item["name"] in selected["notice"] for item in selected["skills"])
    if not expected:
        assert "暂无专项" in selected["notice"]


def test_missing_skill_is_not_reported_as_loaded(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "SKILL_ROOT", tmp_path)
    assert skills.outline_skill_selection(None)["skills"] == []
    with pytest.raises(skills.OutlineSkillError, match="通用小说大纲"):
        skills.outline_skill_selection("玄幻")


def test_skill_files_and_source_manifest_are_consistent():
    catalog = (skills.GENERAL, *skills.SPECIALISTS)
    ids = {skill.id for skill in catalog}
    for skill in catalog:
        path = skills.SKILL_ROOT / skill.id / "SKILL.md"
        header = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
        assert header["name"] == skill.id and header["description"]
        assert skill.read()
    manifest = json.loads((skills.SKILL_ROOT / "sources.json").read_text(encoding="utf-8"))
    root = skills.SKILL_ROOT.parents[1]
    unique = set()
    for item in manifest["sources"]:
        assert hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["sha256"]
        assert set(item["skills"]) <= ids
        if duplicate := item.get("duplicate_of"):
            assert hashlib.sha256((root / duplicate).read_bytes()).hexdigest() == item["sha256"]
        unique.add(item["sha256"])
    assert len(manifest["sources"]) == manifest["source_count"] == 13
    assert len(unique) == manifest["unique_source_count"] == 11


def test_questionnaire_switches_skills_and_keeps_sessions_separate(monkeypatch):
    monkeypatch.setattr(assistant, "_sessions", {})
    app = FastAPI()
    app.dependency_overrides[get_current_account] = lambda: Account(id=1, username="writer", display_name="作者")
    app.include_router(assistant.router)
    with TestClient(app) as client:
        one = client.post("/novel-assistant/sessions").json()
        two = client.post("/novel-assistant/sessions").json()
        assert one["outline_skill"]["skills"] == []
        path = f"/novel-assistant/sessions/{one['session_id']}"
        first = client.post(path + "/messages", json={"content": "玄幻"}).json()
        assert first["outline_skill"]["skills"][-1]["id"] == "outline-xuanhuan"
        second = client.post(path + "/messages", json={"content": "小说类型：都市"}).json()
        assert second["outline_skill"]["skills"][-1]["id"] == "outline-urban"
        assert client.get(path).json()["outline_skill"] == second["outline_skill"]
        assert client.get(f"/novel-assistant/sessions/{two['session_id']}").json()["outline_skill"]["skills"] == []
        assert client.get("/novel-assistant/outline-skills", params={"genre": "都市"}).json() == second["outline_skill"]
        app.dependency_overrides.clear()
        assert client.get("/novel-assistant/outline-skills", params={"genre": "都市"}).status_code == 401


def mock_model(monkeypatch, module, output):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["stream"] is True
        content = json.dumps(output, ensure_ascii=False)
        frames = [{"choices": [{"delta": {"content": content[:20]}}]},
                  {"choices": [{"delta": {"content": content[20:]}, "finish_reason": "stop"}]}]
        wire = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, content=wire.encode(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(API_KEY="test", PROXY_URL="https://model.test/v1",
        REQUEST_TIMEOUT=5, DEEPSEEK_MODEL_NAME="test", MODEL_NATIVE_TOOLS=False))
    monkeypatch.setattr(module, "OpenAI", lambda **kwargs: OpenAI(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    return requests


@pytest.mark.parametrize("genre,skill_id,excluded", [("玄幻", "outline-xuanhuan", "outline-urban"),
    ("都市", "outline-urban", "outline-xianxia"), ("修真", "outline-xianxia", "outline-urban")])
def test_existing_novel_outline_sends_skill_body_to_streaming_model(monkeypatch, genre, skill_id, excluded):
    output = {"summary": "主线", "central_conflict": "冲突", "themes": "主题", "volumes": [{
        "title": "第一卷", "summary": "摘要", "protagonist_goal": "目标", "main_conflict": "冲突",
        "climax": "高潮", "ending_hook": "后果", "beats": [{"title": "行动", "summary": "因果"}]}]}
    requests = mock_model(monkeypatch, writing, output)
    context = {"project": {"genre": genre}, "request": {"volume_count": 1, "instruction": "第一人称，无金手指"}}
    assert writing.generate(writing.OUTLINE, context).model_dump()["summary"] == "主线"
    prompt = requests[0]["messages"][-1]["content"]
    wanted = next(item for item in skills.SPECIALISTS if item.id == skill_id)
    other = next(item for item in skills.SPECIALISTS if item.id == excluded)
    assert wanted.read() in prompt and other.read() not in prompt
    assert '"volume_count": 1' in prompt and "第一人称，无金手指" in prompt and "Schema" in prompt


def test_new_blueprint_sends_current_genre_skill_to_streaming_model(monkeypatch):
    output = {"project": {"title": "新书", "genre": "玄幻", "setting": "大陆", "tone": "温暖",
                         "target_length": "一卷短篇", "protagonist_goal": "归乡"},
        "world": {"summary": "世界", "era": "架空", "history": "历史", "rules": "规则"},
        "power_system": {"name": "力量", "source": "来源"}, "cheat": {"name": "天赋", "cheat_type": "天赋"},
        "outline": {"summary": "故事", "central_conflict": "归途受阻",
                    "volumes": [{"volume_number": 1, "title": "一卷", "summary": "转折"}]}}
    requests = mock_model(monkeypatch, world, output)
    result = world.WorldbuildingAssistant._generate_with_crewai({"genre": "玄幻", "length": "一卷短篇"})
    assert result.outline.volumes[0].title == "一卷"
    prompt = requests[0]["messages"][-1]["content"]
    assert skills.SPECIALISTS[0].read() in prompt
    assert skills.SPECIALISTS[1].read() not in prompt
    assert "一卷短篇" in prompt and "JSON Schema" in prompt


def test_outline_skills_are_not_applied_to_review(monkeypatch):
    requests = mock_model(monkeypatch, writing, {"verdict": "ready", "summary": "正常", "issues": []})
    with patch.object(writing, "outline_skill_prompt", side_effect=AssertionError("unrelated role")):
        writing.generate(writing.REVIEW, {"project": {"genre": "玄幻"}})
    assert "大纲设计技能（创作参考）" not in requests[0]["messages"][-1]["content"]
