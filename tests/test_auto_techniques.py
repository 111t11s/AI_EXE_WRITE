from agents import writing_agents as agents
from api import novel_writing_api as writing
from api.writing_schemas import ChapterContentDraft, TechniqueSelection
from test_character_generation import novel  # noqa: F401 - pytest fixture


TECHNIQUE = {
    "id": 1, "category": "prose", "category_label": "正文",
    "dimension": "language", "dimension_label": "文笔写法",
    "name": "短句与沉默", "summary": "把对话压到一两句，用动作和停顿承接",
    "mechanism": "短句制造压力", "reuse": "在对峙场景中交替短句和沉默",
    "evidence": [{"quote": "示例引文"}], "analysis_id": 1,
}


def test_first_draft_uses_ai_selected_techniques(novel, monkeypatch):
    project = novel["project"]
    character = novel["create"]("characters", project_id=project, name="林川", role="主角", summary="寻脉者")
    chapter = novel["create"](
        "chapters", project_id=project, volume_id=novel["volume"], chapter_number=1,
        title="旧票", summary="林川在渡口发现旧票", content="", status="planned")
    calls = []

    def fake_list(*args, **kwargs):
        return [TECHNIQUE]

    def fake_get(*args, **kwargs):
        return [TECHNIQUE]

    monkeypatch.setattr(writing.analysis_db, "list_techniques", fake_list)
    monkeypatch.setattr(writing.analysis_db, "get_techniques", fake_get)

    def fake_generate(role, context, profile=None):
        calls.append(role)
        if role is agents.TECHNIQUE_SELECTOR:
            assert context["available_techniques"][0]["id"] == 1
            assert context["chapter"]["summary"] == "林川在渡口发现旧票"
            return TechniqueSelection(technique_ids=[1])
        assert role is agents.DRAFT
        assert "短句与沉默" in context["technique_prompt"]
        return ChapterContentDraft(content="林川在渡口停下，看着手里的旧票。" * 10)

    monkeypatch.setattr(agents, "generate", fake_generate)
    response = novel["client"].post(
        f"/api/v1/writing/chapters/{chapter}/draft", json={"target_characters": 300},
        headers=novel["headers"]["writer"])
    assert response.status_code == 200, response.text
    assert calls == [agents.TECHNIQUE_SELECTOR, agents.DRAFT]
    assert character is not None
