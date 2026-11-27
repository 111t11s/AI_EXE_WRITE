from db import foreshadows as foreshadow_db
from db import misc_notes as misc_db
from test_character_generation import novel  # noqa: F401 - pytest fixture


def test_manual_foreshadow_misc_and_pagination(novel):
    project, client, headers = novel["project"], novel["client"], novel["headers"]["writer"]
    chapter = novel["create"](
        "chapters", project_id=project, volume_id=novel["volume"], chapter_number=20,
        title="旧票", summary="一张旧船票", status="draft", content="旧船票背面写着一个日期。")

    left = client.post(f"/api/v1/writing/projects/{project}/foreshadows", headers=headers,
                       json={"title": "旧船票的日期", "category": "event", "summary": "指向事故当天",
                             "details": "背面日期与父亲事故同一天。"})
    assert left.status_code == 201, left.text
    right = client.post(f"/api/v1/writing/projects/{project}/foreshadows", headers=headers,
                        json={"title": "顾临舟的姓氏", "category": "identity", "summary": "与旧案有关"})
    assert right.status_code == 201, right.text

    listed = client.get(f"/api/v1/writing/projects/{project}/foreshadows?page=1&page_size=1",
                        headers=headers)
    assert listed.status_code == 200, listed.text
    data = listed.json()
    assert data["total"] == 2 and len(data["items"]) == 1 and data["has_more"] is True
    assert data["items"][0]["category_label"]

    available = client.get(f"/api/v1/writing/projects/{project}/foreshadows/available", headers=headers)
    assert len(available.json()["items"]) == 2

    note = client.post(f"/api/v1/writing/projects/{project}/misc-notes", headers=headers,
                       json={"note_type": "item", "title": "旧船票", "summary": "一张旧船票",
                             "details": "背面有日期。"})
    assert note.status_code == 201, note.text
    notes = client.get(f"/api/v1/writing/projects/{project}/misc-notes?page_size=8", headers=headers)
    assert notes.json()["items"][0]["title"] == "旧船票"
    assert notes.json()["items"][0]["type_label"] == "独特物品"


def test_analysis_saves_and_outline_consumes_foreshadows(novel):
    project, engine = novel["project"], novel["engine"]
    chapter = novel["create"](
        "chapters", project_id=project, volume_id=novel["volume"], chapter_number=21,
        title="暗线", summary="一条暗线", status="draft", content="旧船票背面还有第二行小字。")
    chapter_row = {"id": chapter, "volume_id": novel["volume"], "outline_id": novel["outline"]}

    saved = foreshadow_db.save_analysis_candidates(engine, project, chapter_row, [
        {"title": "船票第二行小字", "category": "event", "summary": "还有第二行",
         "details": "第二行是坐标。"},
        {"title": "船票第二行小字", "category": "event", "summary": "重复"},
    ])
    assert saved == 1
    item = foreshadow_db.available(engine, project)[0]
    assert item["status"] == "planted" and item["planted_chapter_id"] == chapter

    consumed = foreshadow_db.create(engine, project, {"title": "将消费的伏笔", "category": "treasure",
                                                       "summary": "将在新大纲回收", "source": "preset"})
    planted = foreshadow_db.create(engine, project, {"title": "将预埋的伏笔", "category": "prophecy",
                                                      "summary": "将在新大纲埋下", "source": "preset"})
    foreshadow_db.apply_outline_selection(engine, project, [consumed], [planted],
                                          novel["outline"], novel["volume"])
    consumed_row = foreshadow_db.get(engine, project, consumed)
    planted_row = foreshadow_db.get(engine, project, planted)
    assert consumed_row["status"] == "consumed" and consumed_row["consumed_outline_id"] == novel["outline"]
    assert planted_row["status"] == "planted" and planted_row["planted_outline_id"] == novel["outline"]
    assert consumed not in {row["id"] for row in foreshadow_db.available(engine, project)}

    misc_db.save_analysis_candidates(engine, project, chapter_row, [
        {"note_type": "beast", "title": "灰羽", "summary": "渡口的灵兽"},
    ])
    misc_db.save_analysis_candidates(engine, project, chapter_row, [
        {"note_type": "beast", "title": "灰羽", "summary": "再次出现"},
    ])
    notes = misc_db.list_page(engine, project)
    assert notes["total"] == 1 and notes["items"][0]["last_chapter_id"] == chapter
