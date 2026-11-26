import asyncio
import time

from fastapi.testclient import TestClient

from api import novel_analysis_api as analysis
from api.account_api import Account, get_current_account
from api.novel_crud_api import get_database
from api.writing_schemas import (NovelAnalysisDimension, NovelAnalysisEvidence, NovelAnalysisReport,
                                 NovelAnalysisTechnique)
from db import novel_analysis as analysis_db
from db.migrate_accounts import create_account
from main import app
from test_writing_workflow import create_test_db


SOURCE = "林川把扳手放回工具箱，先看了看门外的雨。他知道今晚不会太平。\n旧港的维修铺只剩他一个人。"


def technique(dimension, name, quote, confidence="high"):
    return NovelAnalysisTechnique(
        dimension=dimension, name=name, summary="用一个具体写法推进场景",
        mechanism="先给动作，再给判断，最后落到处境", reuse="换成新故事里的职业动作和风险判断",
        evidence=[NovelAnalysisEvidence(quote=quote, note="证明这个写法")],
        confidence=confidence, applicable_genres=["都市"])


def report():
    return NovelAnalysisReport(
        overview="用职业动作建立人物，用短判断压出危机。",
        source_summary="第一章片段，第三人称限知。",
        limitations=["只有一章，无法判断长线伏笔"],
        dimensions=[
            NovelAnalysisDimension(dimension="character", available=True, summary="人物由动作和处境建立",
                                   techniques=[technique("character", "工具动作露底",
                                                         "林川把扳手放回工具箱，先看了看门外的雨。")]),
            NovelAnalysisDimension(dimension="profession", available=False,
                                   missing="文本没有展开职业等级、职责和晋升条件"),
            NovelAnalysisDimension(dimension="world", available=False, missing="只有旧港和维修铺，缺少世界规则"),
            NovelAnalysisDimension(dimension="plot", available=True, summary="用一句判断制造危机",
                                   techniques=[technique("plot", "短判断压危机", "他知道今晚不会太平。")]),
            NovelAnalysisDimension(dimension="prose", available=False, missing="样本太短，无法判断语言风格"),
        ])


def test_clean_analysis_drops_unverified_and_fills_missing_dimensions():
    data = report()
    data.dimensions[0].techniques.append(technique("character", "编造的技法", "这段原文根本不存在。"))
    data.dimensions[2].available = True
    data.dimensions[2].techniques = [technique("world", "无证据世界", "没有这条引文。")]
    cleaned = analysis.clean_analysis(data, [{"name": "第一章.txt", "text": SOURCE}])
    assert cleaned is not None
    dimensions = {item.dimension: item for item in cleaned.dimensions}
    assert len(dimensions) == 5
    assert [item.name for item in dimensions["character"].techniques] == ["工具动作露底"]
    assert dimensions["world"].available is False
    assert dimensions["profession"].missing
    assert sum(len(item.techniques) for item in cleaned.dimensions) == 2


def test_clean_analysis_returns_none_without_evidence():
    data = report()
    for dimension in data.dimensions:
        for item in dimension.techniques:
            item.evidence[0].quote = "完全不存在的引文"
    assert analysis.clean_analysis(data, [{"name": "第一章.txt", "text": SOURCE}]) is None


def test_analyze_documents_saves_report(monkeypatch):
    engine = create_test_db()
    account_id = create_account(engine, "analyst", "test-password")

    async def fake_generate(role, context, request):
        return report()

    monkeypatch.setattr(analysis, "generate", fake_generate)
    docs = [{"name": "第一章.txt", "chars": len(SOURCE), "text": SOURCE, "truncated": False}]
    receipt = asyncio.run(analysis.analyze_documents(docs, "重点看人物", None, account_id, engine))
    assert receipt.record_id is not None
    stored = analysis_db.get_report(engine, account_id, receipt.record_id)
    assert stored["technique_count"] == 2
    assert stored["report"]["dimensions"][0]["dimension"] == "character"
    techniques = analysis_db.list_techniques(engine, account_id)
    assert {item["name"] for item in techniques} == {"工具动作露底", "短判断压危机"}


def test_upload_endpoint_and_library(monkeypatch):
    engine = create_test_db()
    account_id = create_account(engine, "analyst", "test-password")

    async def fake_generate(role, context, request):
        return report()

    monkeypatch.setattr(analysis, "generate", fake_generate)
    app.dependency_overrides[get_database] = lambda: engine
    app.dependency_overrides[get_current_account] = lambda: Account(
        id=account_id, username="analyst", display_name="拆解者")
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/novel-analysis/analyze/tasks",
                files={"files": ("第一章.txt", SOURCE.encode("utf-8"), "text/plain")},
                data={"instruction": "重点看人物"})
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            for _ in range(200):
                detail = client.get(f"/api/v1/generation-jobs/{job_id}").json()
                if detail["status"] != "running":
                    break
                time.sleep(0.02)
            assert detail["status"] == "completed", detail
            record_id = detail["result"]["record_id"]
            assert record_id
            history = client.get("/api/v1/novel-analysis").json()["items"]
            assert history[0]["id"] == record_id
            report_data = client.get(f"/api/v1/novel-analysis/{record_id}").json()
            assert report_data["report"]["overview"].startswith("用职业动作")
            library = client.get("/api/v1/novel-analysis/techniques").json()
            assert len(library["items"]) == 2
    finally:
        app.dependency_overrides.pop(get_database, None)
        app.dependency_overrides.pop(get_current_account, None)
