import time

from sqlalchemy import select

from agents import writing_agents as agents
from api.writing_schemas import (ChapterContentDraft, ConsistencyReport, EntityDiscoveryReport,
                                 EntityEvidence, ForeshadowCandidate, MiscNoteCandidate,
                                 NewCharacterCandidate, NewFactionCandidate)
from db import entity_reviews as review_db
from db.accounts import account
from test_character_generation import novel  # noqa: F401 - pytest fixture


def account_id(engine):
    with engine.begin() as connection:
        return connection.scalar(select(account.c.id).where(account.c.username == "writer"))


def test_consistency_detects_new_entities_as_pending_reviews(novel, monkeypatch):
    project, engine, client = novel["project"], novel["engine"], novel["client"]
    headers = novel["headers"]["writer"]
    novel["create"]("characters", project_id=project, name="林川", role="主角", summary="寻脉者")
    chapter = novel["create"](
        "chapters", project_id=project, volume_id=novel["volume"], chapter_number=11,
        title="陌生来客", summary="新势力现身", status="draft",
        content="灰潮商会的人第一次出现在渡口。顾临舟自称商会账房，向林川递上一张旧船票。")

    def fake_generate(role, context, payload):
        if role is agents.ENTITY_DISCOVERY:
            return EntityDiscoveryReport(
                factions=[NewFactionCandidate(
                    name="灰潮商会", faction_type="商帮", summary="从下游来的商帮",
                    goal="控制渡口贸易", leader="顾临舟", territory="下游",
                    strengths="资金充足", weaknesses="根基不稳", relationships="与渡舟盟竞争",
                    evidence=[EntityEvidence(chapter_id=chapter, quote="灰潮商会的人第一次出现在渡口。")])],
                characters=[NewCharacterCandidate(
                    name="顾临舟", role="商会账房", summary="灰潮商会的账房",
                    faction_name="灰潮商会", goal="打通渡口",
                    evidence=[EntityEvidence(chapter_id=chapter, quote="顾临舟自称商会账房")])],
                foreshadows=[ForeshadowCandidate(
                    title="旧船票的日期", category="event", summary="旧船票背面写着一个日期",
                    details="日期与事故同一天。",
                    evidence=[EntityEvidence(chapter_id=chapter, quote="向林川递上一张旧船票")])],
                misc_notes=[MiscNoteCandidate(
                    note_type="item", title="旧船票", summary="灰潮商会递来的旧船票",
                    evidence=[EntityEvidence(chapter_id=chapter, quote="向林川递上一张旧船票")])])
        return ConsistencyReport(verdict="consistent", summary="所选范围未发现明确偏移", issues=[])

    monkeypatch.setattr(agents, "generate", fake_generate)
    response = client.post(
        f"/api/v1/writing/projects/{project}/consistency",
        json={"outline_id": novel["outline"], "chapter_ids": [chapter]},
        headers=headers)
    assert response.status_code == 200, response.text
    reviews = client.get(f"/api/v1/writing/projects/{project}/entity-reviews", headers=headers).json()
    assert {item["name"] for item in reviews["items"]} == {"灰潮商会", "顾临舟"}
    assert all(item["status"] == "pending" for item in reviews["items"])
    foreshadows = client.get(f"/api/v1/writing/projects/{project}/foreshadows", headers=headers).json()
    assert foreshadows["items"][0]["title"] == "旧船票的日期"
    misc = client.get(f"/api/v1/writing/projects/{project}/misc-notes", headers=headers).json()
    assert misc["items"][0]["title"] == "旧船票" and misc["items"][0]["type_label"] == "独特物品"


def test_entity_review_approve_creates_records_and_replace_rewrites_chapter(novel, monkeypatch):
    project, engine, client = novel["project"], novel["engine"], novel["client"]
    headers = novel["headers"]["writer"]
    chapter = novel["create"](
        "chapters", project_id=project, volume_id=novel["volume"], chapter_number=10,
        title="渡口夜战", summary="血鸦帮夜袭渡口", status="draft",
        content="血鸦帮的人堵住渡口。沈舟报出渡舟盟的名号，血鸦帮仍不退。")

    saved = review_db.save_reviews(engine, account_id(engine), project, [
        {"entity_type": "faction", "chapter_id": chapter, "name": "血鸦帮",
         "details": {"faction_type": "水匪", "summary": "盘踞下游的水匪", "goal": "控制渡口",
                     "leader": "鸦七", "territory": "下游", "strengths": "熟悉水路",
                     "weaknesses": "不敌官军", "relationships": "敌视渡舟盟"},
         "evidence": [{"chapter_id": chapter, "chapter_number": 10, "quote": "血鸦帮的人堵住渡口。"}]},
        {"entity_type": "character", "chapter_id": chapter, "name": "沈舟",
         "details": {"role": "渡舟盟信使", "summary": "报出渡舟盟名号的年轻人",
                     "faction_name": "渡舟盟", "goal": "守住渡口"},
         "evidence": [{"chapter_id": chapter, "chapter_number": 10, "quote": "沈舟报出渡舟盟的名号"}]},
        {"entity_type": "faction", "chapter_id": chapter, "name": "黑水会",
         "details": {"faction_type": "商帮", "summary": "另一个新势力", "goal": "控制渡口"},
         "evidence": [{"chapter_id": chapter, "chapter_number": 10, "quote": "血鸦帮的人堵住渡口。"}]},
    ])
    assert len(saved) == 3

    listed = client.get(f"/api/v1/writing/projects/{project}/entity-reviews", headers=headers)
    assert listed.status_code == 200, listed.text
    data = listed.json()
    assert {item["name"] for item in data["items"]} == {"血鸦帮", "沈舟", "黑水会"}
    assert any(item["name"] == "渡舟盟" for item in data["factions"])

    for name in ("血鸦帮", "沈舟"):
        review = next(item for item in data["items"] if item["name"] == name)
        result = client.post(
            f"/api/v1/writing/projects/{project}/entity-reviews/{review['id']}/approve",
            headers=headers)
        assert result.status_code == 200, result.text

    characters = client.get("/api/v1/characters", params={"project_id": project, "limit": 100},
                            headers=headers).json()["items"]
    assert any(row["name"] == "沈舟" and row["role"] == "渡舟盟信使" for row in characters)
    factions = client.get("/api/v1/factions", params={"world_id": novel["world"]},
                          headers=headers).json()["items"]
    assert any(row["name"] == "血鸦帮" for row in factions)
    pending = client.get(f"/api/v1/writing/projects/{project}/entity-reviews", headers=headers).json()["items"]
    assert [item["name"] for item in pending] == ["黑水会"]

    def fake_generate(role, context, payload):
        return ChapterContentDraft(content=(
            "渡舟盟的人堵住渡口。沈舟报出渡舟盟的名号，渡舟盟仍不退。"
            "雨声压在船篷上，双方都没有先动手；沈舟把通行木牌放在船头，"
            "让渡舟盟的旗号在风里展开，直到对面的领队终于抬手让开一条水道。") * 2)

    monkeypatch.setattr(agents, "generate", fake_generate)
    replace = client.post(
        f"/api/v1/writing/projects/{project}/entity-reviews/{pending[0]['id']}/replace/tasks",
        json={"existing_id": next(row["id"] for row in factions if row["name"] == "渡舟盟")},
        headers=headers)
    assert replace.status_code == 202, replace.text
    job_id = replace.json()["id"]
    for _ in range(200):
        detail = client.get(f"/api/v1/generation-jobs/{job_id}", headers=headers).json()
        if detail["status"] != "running":
            break
        time.sleep(0.02)
    assert detail["status"] == "completed", detail
    saved_chapter = client.get(f"/api/v1/chapters/{chapter}", headers=headers).json()
    assert "渡舟盟" in saved_chapter["content"]
    assert "黑水会" not in saved_chapter["content"]
    replaced = client.get(f"/api/v1/writing/projects/{project}/entity-reviews", headers=headers).json()["items"]
    assert replaced == []
