import time

from fastapi.testclient import TestClient

from api import document_topics_api as documents
from api.account_api import Account, get_current_account
from api.novel_crud_api import get_database
from api.writing_schemas import SettingExpansionReport
from db import setting_expansion as setting_db
from db.migrate_accounts import create_account
from main import app
from test_writing_workflow import create_test_db


SOURCE = "天地养我，我养天地。修士靠灵脉修炼，灵脉正在枯竭，宗门靠控制灵脉维持秩序。"


def report():
    return SettingExpansionReport(
        title="灵脉枯竭纪",
        core_concept="天地养我，我养天地；修士以灵脉修行，灵脉衰竭迫使宗门重新分配资源。",
        world_overview="世界由若干灵脉节点维系，旧港与山区依赖驿路运输灵石。",
        power_system="引灵入体，分为引灵、凝脉、开府、化罡四境；晋升需要灵脉共鸣，失败会损伤经脉。",
        profession_system="修士、灵脉师、巡脉人、灵石商；宗门执事掌握配额。",
        society="灵石是货币和能源，普通人靠灵脉余波取暖；宗门控制配给。",
        factions="三大宗门争夺主脉，散修联盟走私灵石，地方官府维持表面秩序。",
        story_hooks=["主角发现主脉衰竭是人为抽灵", "巡脉人隐瞒一条支脉的存在"],
        preserved_elements=["天地养我，我养天地", "灵脉修炼", "灵脉枯竭"],
        additions=["四境划分", "巡脉人职业", "灵石配给制度"],
        open_questions=["灵脉衰竭是否可逆"],
    )


def test_setting_expansion_persists_and_lists(monkeypatch):
    engine = create_test_db()
    account_id = create_account(engine, "expander", "test-password")

    async def fake_generate(role, context, request):
        return report()

    monkeypatch.setattr(documents, "generate", fake_generate)
    app.dependency_overrides[get_database] = lambda: engine
    app.dependency_overrides[get_current_account] = lambda: Account(
        id=account_id, username="expander", display_name="补全者")
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/document-topics/expand/tasks",
                                   data={"text": SOURCE, "instruction": "保留既有名称"})
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            for _ in range(200):
                detail = client.get(f"/api/v1/generation-jobs/{job_id}").json()
                if detail["status"] != "running":
                    break
                time.sleep(0.02)
            assert detail["status"] == "completed", detail
            assert detail["title"] == "设定补全 · 粘贴文本"
            record_id = detail["result"]["record_id"]
            assert record_id
            history = client.get("/api/v1/document-topics/expansions").json()["items"]
            assert history[0]["title"] == "灵脉枯竭纪"
            stored = client.get(f"/api/v1/document-topics/expansions/{record_id}").json()
            assert stored["report"]["preserved_elements"] == ["天地养我，我养天地", "灵脉修炼", "灵脉枯竭"]
            assert stored["report"]["power_system"].startswith("引灵入体")
    finally:
        app.dependency_overrides.pop(get_database, None)
        app.dependency_overrides.pop(get_current_account, None)


def test_setting_expansion_requires_material_and_limits_text():
    engine = create_test_db()
    account_id = create_account(engine, "expander", "test-password")
    app.dependency_overrides[get_database] = lambda: engine
    app.dependency_overrides[get_current_account] = lambda: Account(
        id=account_id, username="expander", display_name="补全者")
    try:
        with TestClient(app) as client:
            empty = client.post("/api/v1/document-topics/expand/tasks", data={})
            assert empty.status_code == 422
            too_long = client.post("/api/v1/document-topics/expand/tasks",
                                   data={"text": "字" * 25001})
            assert too_long.status_code == 422
    finally:
        app.dependency_overrides.pop(get_database, None)
        app.dependency_overrides.pop(get_current_account, None)


def test_setting_expansion_db_roundtrip():
    engine = create_test_db()
    account_id = create_account(engine, "expander", "test-password")
    record_id = setting_db.save_report(
        engine, account_id, ["设定.txt"], len(SOURCE), "补充", report().model_dump(mode="json"))
    stored = setting_db.get_report(engine, account_id, record_id)
    assert stored["title"] == "灵脉枯竭纪"
    assert setting_db.list_reports(engine, account_id)["items"][0]["id"] == record_id
