from sqlalchemy import func, select

from agents import writing_agents as agents
from api.novel_tables import tables
from api.writing_schemas import BeatDraft, OutlineDraft, VolumeDraft
from test_character_generation import novel  # noqa: F401 - pytest fixture


def artifact(volume_count=1):
    return OutlineDraft(
        summary="单卷主线", central_conflict="线索与代价", themes="信任",
        volumes=[VolumeDraft(title=f"第 {index + 1} 卷", summary="本卷摘要",
                             protagonist_goal="夺回线索", main_conflict="对手拦截",
                             climax="公开证据", ending_hook="新的追兵",
                             beats=[BeatDraft(title="追查", summary="主角追查旧票")])
                 for index in range(volume_count)])


def test_outline_generates_one_volume_with_user_theme(novel, monkeypatch):
    contexts = []

    def fake_generate(role, context, profile=None):
        assert role is agents.OUTLINE
        contexts.append(context)
        return artifact()

    monkeypatch.setattr(agents, "generate", fake_generate)
    response = novel["client"].post(
        f"/api/v1/writing/projects/{novel['project']}/outline",
        json={"volume_number": 2, "volume_theme": "旧船票指向旧港地下拍卖会"},
        headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert len(receipt["volume_ids"]) == 1
    assert contexts[0]["volume_plan"] == {"volume_number": 2, "theme": "旧船票指向旧港地下拍卖会"}
    with novel["engine"].begin() as connection:
        rows = connection.execute(select(tables["novel_outline_volume"]).where(
            tables["novel_outline_volume"].c.outline_id == receipt["outline_id"])).mappings().all()
    assert len(rows) == 1
    assert rows[0]["volume_number"] == 2
    assert rows[0]["title"] == "第 1 卷"


def test_outline_rejects_multiple_volumes(novel, monkeypatch):
    monkeypatch.setattr(agents, "generate", lambda role, context, profile=None: artifact(2))

    def count():
        with novel["engine"].begin() as connection:
            return connection.scalar(select(func.count()).select_from(tables["novel_outline"]).where(
                tables["novel_outline"].c.project_id == novel["project"]))

    before = count()
    response = novel["client"].post(
        f"/api/v1/writing/projects/{novel['project']}/outline",
        json={"volume_number": 2, "volume_theme": "本卷主题"},
        headers=novel["headers"]["writer"])
    assert response.status_code == 502, response.text
    assert count() == before
