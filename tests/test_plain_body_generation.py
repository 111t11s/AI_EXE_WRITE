"""Plain-text model output survives parsing, serial generation and real saves."""

import json

import pytest

from agents import writing_agents as agents
from agents.llm_streaming import IncompleteGenerationError
from core.config import Settings
from test_character_generation import novel  # noqa: F401 - real API/MCP/database fixture


generate = agents.generate


@pytest.mark.parametrize("suffix", ["", "/stream", "/tasks"])
def test_batch_saves_plain_bodies_before_analyzing_and_writing_next(novel, monkeypatch, suffix):
    monkeypatch.setattr(agents, "generate", generate)
    monkeypatch.setattr(agents, "get_settings", lambda: Settings(
        _env_file=None, API_KEY="test-key", PROXY_URL="https://model.test/v1", MODEL_NATIVE_TOOLS=False))
    novel["create"]("characters", project_id=novel["project"], name="舟客", role="配角", summary="渡河向导")
    chapters = [novel["create"]("chapters", project_id=novel["project"], volume_id=novel["volume"],
                               chapter_number=number, title=f"渡河{number}", summary="舟客帮助渡河")
                for number in (1, 2)]
    bodies = {chapter: f"舟客望着第{number}处渡口。\n\n" + "夜风吹过河面，舟客将缆绳牢牢系在船舷，等候远行的少年。" * 10
              for number, chapter in enumerate(chapters, 1)}
    calls = []
    def saved(chapter):
        return novel["client"].get(f"/api/v1/chapters/{chapter}", headers=novel["headers"]["writer"]).json()

    def respond(*, role, prompt, **kwargs):
        context = json.loads(prompt.split("资料和用户要求：", 1)[1])
        chapter = context["chapter"]["id"]
        calls.append((role, chapter))
        if role is agents.DRAFT:
            assert "直接返回完整的小说正文纯文本" in prompt
            if chapter == chapters[1]:
                assert saved(chapters[0])["content"] == bodies[chapters[0]]
                assert context["previous_chapter"]["ending"] == bodies[chapters[0]]
            return bodies[chapter]
        assert role is agents.CHARACTER_PROGRESS
        assert saved(chapter)["content"] == bodies[chapter]
        return '{"character_updates": []}'

    monkeypatch.setattr(agents, "_request_without_tools", respond)
    client, headers = novel["client"], novel["headers"]["writer"]
    response = client.post(f'/api/v1/writing/projects/{novel["project"]}/chapters/batch-draft{suffix}',
                           json={"chapter_ids": list(reversed(chapters))}, headers=headers)
    if suffix == "/tasks":
        assert response.status_code == 202, response.text
        response = client.get(f'/api/v1/generation-jobs/{response.json()["id"]}/events', headers=headers)
    assert response.status_code == 200, response.text
    if suffix:
        final = response.text.strip().split("\n\n")[-1]
        assert final.startswith("event: result"), response.text
        receipt = json.loads(final.split("data: ", 1)[1])
    else:
        receipt = response.json()
    assert receipt["completed_count"] == 2
    assert calls == [(role, chapter) for chapter in chapters
                     for role in (agents.DRAFT, agents.CHARACTER_PROGRESS)]
    for chapter in chapters:
        assert saved(chapter)["content"] == bodies[chapter]
        assert saved(chapter)["status"] == "draft"


@pytest.mark.parametrize("failure", ["short-body", "broken-json", "truncated"])
def test_batch_does_not_save_invalid_or_truncated_body(novel, monkeypatch, failure):
    monkeypatch.setattr(agents, "generate", generate)
    monkeypatch.setattr(agents, "get_settings", lambda: Settings(
        _env_file=None, API_KEY="test-key", PROXY_URL="https://model.test/v1", MODEL_NATIVE_TOOLS=False))
    novel["create"]("characters", project_id=novel["project"], name="舟客", role="配角", summary="渡河向导")
    chapters = [novel["create"]("chapters", project_id=novel["project"], volume_id=novel["volume"],
                               chapter_number=number, title=f"渡河{number}", summary="舟客帮助渡河")
                for number in (1, 2)]
    calls = []

    def respond(*, role, **kwargs):
        calls.append(role)
        if failure == "truncated":
            raise IncompleteGenerationError("模型输出被中途截断，本次内容未保存")
        return "正文过短" if failure == "short-body" else '{"content": "' + "正文" * 100

    monkeypatch.setattr(agents, "_request_without_tools", respond)
    client, headers = novel["client"], novel["headers"]["writer"]
    response = client.post(f'/api/v1/writing/projects/{novel["project"]}/chapters/batch-draft/tasks',
                           json={"chapter_ids": chapters}, headers=headers)
    assert response.status_code == 202, response.text
    response = client.get(f'/api/v1/generation-jobs/{response.json()["id"]}/events', headers=headers)
    final = response.text.strip().split("\n\n")[-1]
    assert final.startswith("event: error"), response.text
    assert calls == [agents.DRAFT]
    for chapter in chapters:
        saved = client.get(f"/api/v1/chapters/{chapter}", headers=headers).json()
        assert saved["content"] == "" and saved["status"] == "planned"
