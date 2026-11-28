from sqlalchemy import func, insert, select, update

from agents import writing_agents as agents
from api.novel_tables import tables
from api.writing_schemas import (BeatDraft, ChapterPlan, ChapterPlanDraft, OutlineDraft,
                                 OutlineForeshadow, VolumeDraft)
from db.foreshadows import foreshadow
from db.accounts import utcnow
from test_character_generation import novel  # noqa: F401 - pytest fixture


def artifact(volume_count=1):
    return OutlineDraft(
        summary="单卷主线", central_conflict="线索与代价", themes="信任",
        volumes=[VolumeDraft(title=f"第 {index + 1} 卷", summary="本卷摘要",
                             protagonist_goal="夺回线索", main_conflict="对手拦截",
                             climax="公开证据", ending_hook="新的追兵",
                             beats=[BeatDraft(title="追查", summary="主角追查旧票")])
                 for index in range(volume_count)])


def foreshadow_artifact():
    return OutlineDraft(
        summary="单卷主线", central_conflict="线索与代价", themes="信任",
        volumes=[VolumeDraft(title="第 1 卷", summary="本卷摘要",
                             protagonist_goal="夺回线索", main_conflict="对手拦截",
                             climax="公开证据", ending_hook="新的追兵",
                             beats=[BeatDraft(title="追查", summary="主角追查旧票")],
                             foreshadows=[
                                 OutlineForeshadow(title="旧船票日期", category="event", summary="日期指向事故"),
                                 OutlineForeshadow(title="顾临舟的姓氏", category="identity", summary="与旧案有关"),
                             ])])


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


def test_outline_saves_foreshadow_requirements_and_records(novel, monkeypatch):
    contexts = []

    def fake_generate(role, context, profile=None):
        contexts.append(context)
        return foreshadow_artifact()

    monkeypatch.setattr(agents, "generate", fake_generate)
    response = novel["client"].post(
        f"/api/v1/writing/projects/{novel['project']}/outline",
        json={"volume_number": 3, "volume_theme": "拍卖会", "plot_progression": "追查→拍卖会→暴露",
              "target_word_min": 20000, "target_word_max": 30000,
              "min_foreshadows": 2, "foreshadow_categories": ["event", "identity"]},
        headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["foreshadow_count"] == 2
    assert contexts[0]["outline_plan"]["min_foreshadows"] == 2
    with novel["engine"].begin() as connection:
        volume = connection.execute(select(tables["novel_outline_volume"]).where(
            tables["novel_outline_volume"].c.id == receipt["volume_ids"][0])).mappings().one()
        rows = connection.execute(select(foreshadow).where(
            foreshadow.c.planted_volume_id == receipt["volume_ids"][0])).mappings().all()
    assert volume["target_word_min"] == 20000
    assert volume["target_word_max"] == 30000
    assert volume["min_foreshadows"] == 2
    assert volume["plot_progression"] == "追查→拍卖会→暴露"
    assert {row["category"] for row in rows} == {"event", "identity"}
    assert all(row["status"] == "planned" and row["source"] == "outline" for row in rows)


def test_outline_rejects_insufficient_foreshadows(novel, monkeypatch):
    monkeypatch.setattr(agents, "generate", lambda role, context, profile=None: foreshadow_artifact())
    response = novel["client"].post(
        f"/api/v1/writing/projects/{novel['project']}/outline",
        json={"volume_number": 3, "volume_theme": "拍卖会", "min_foreshadows": 3,
              "foreshadow_categories": ["event"]},
        headers=novel["headers"]["writer"])
    assert response.status_code == 502, response.text


def test_chapter_plan_assigns_volume_foreshadows(novel, monkeypatch):
    volume_id, outline_id, project = novel["volume"], novel["outline"], novel["project"]
    with novel["engine"].begin() as connection:
        connection.execute(update(tables["novel_outline_volume"]).where(
            tables["novel_outline_volume"].c.id == volume_id).values(min_foreshadows=1))
        foreshadow_id = connection.execute(insert(foreshadow).values(
            project_id=project, title="旧船票日期", category="event", status="planned", source="outline",
            summary="日期指向事故", details="", planted_outline_id=outline_id, planted_volume_id=volume_id,
            created_at=utcnow(), updated_at=utcnow())).inserted_primary_key[0]

    def fake_generate(role, context, profile=None):
        assert context["volume"]["min_foreshadows"] == 1
        assert context["foreshadows"][0]["id"] == foreshadow_id
        return ChapterPlanDraft(chapters=[ChapterPlan(
            title="旧票", summary="主角发现日期", foreshadow_ids=[foreshadow_id], new_foreshadows=[])])

    monkeypatch.setattr(agents, "generate", fake_generate)
    response = novel["client"].post(
        f"/api/v1/writing/volumes/{volume_id}/chapter-plan",
        json={"start_chapter": 1, "chapter_count": 1}, headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    assert response.json()["foreshadow_count"] == 1
    with novel["engine"].begin() as connection:
        row = connection.execute(select(foreshadow).where(foreshadow.c.id == foreshadow_id)).mappings().one()
    assert row["status"] == "planted" and row["planted_chapter_id"] == response.json()["chapters"][0]["chapter_id"]


def test_chapter_plan_rejects_missing_volume_foreshadows(novel, monkeypatch):
    volume_id, outline_id, project = novel["volume"], novel["outline"], novel["project"]
    with novel["engine"].begin() as connection:
        connection.execute(update(tables["novel_outline_volume"]).where(
            tables["novel_outline_volume"].c.id == volume_id).values(min_foreshadows=1))
        connection.execute(insert(foreshadow).values(
            project_id=project, title="旧船票日期", category="event", status="planned", source="outline",
            summary="日期指向事故", details="", planted_outline_id=outline_id, planted_volume_id=volume_id,
            created_at=utcnow(), updated_at=utcnow()))
    monkeypatch.setattr(agents, "generate", lambda role, context, profile=None: ChapterPlanDraft(
        chapters=[ChapterPlan(title="旧票", summary="没有安排伏笔")]))
    response = novel["client"].post(
        f"/api/v1/writing/volumes/{volume_id}/chapter-plan",
        json={"start_chapter": 1, "chapter_count": 1}, headers=novel["headers"]["writer"])
    assert response.status_code == 502, response.text
