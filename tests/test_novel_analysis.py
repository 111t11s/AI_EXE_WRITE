import asyncio
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import novel_analysis_api as analysis
from api import novel_writing_api as writing
from api.account_api import Account, get_current_account
from api.novel_crud_api import get_database
from api.writing_schemas import (NovelAnalysisCandidate, NovelAnalysisDimension, NovelAnalysisEvidence,
                                 NovelAnalysisReport, NovelAnalysisTechnique, WritingInput)
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


def report_with_extra():
    data = report()
    data.extra_techniques = [NovelAnalysisCandidate(
        dimension="rhythm", dimension_label="节奏构造",
        dimension_reason="五维之外，专门研究段落长短和场景切换的节奏",
        name="三拍递进", summary="用三个越来越短的动作推进紧张感",
        mechanism="长句铺垫、中句转折、短句落锤", reuse="把新故事的高潮拆成三拍",
        evidence=[NovelAnalysisEvidence(quote="旧港的维修铺只剩他一个人。", note="短句收束")],
        confidence="high", applicable_genres=["都市"])]
    return data


def test_extra_dimension_requires_review_then_enters_technique_table():
    engine = create_test_db()
    account_id = create_account(engine, "analyst", "test-password")
    cleaned = analysis.clean_analysis(report_with_extra(), [{"name": "第一章.txt", "text": SOURCE}])
    assert len(cleaned.extra_techniques) == 1
    record_id = analysis_db.save_analysis(
        engine, account_id, ["第一章.txt"], len(SOURCE), "重点看节奏", cleaned.model_dump(mode="json"),
        set(analysis.FIXED_DIMENSIONS), analysis.DIMENSION_LABELS)

    assert {item["name"] for item in analysis_db.list_techniques(engine, account_id)} == {
        "工具动作露底", "短判断压危机"}
    candidates = analysis_db.list_candidates(engine, account_id)
    assert [item["name"] for item in candidates] == ["三拍递进"]
    result = analysis_db.approve_candidate(engine, account_id, candidates[0]["id"], account_id)
    assert result["dimension"] == "rhythm"
    assert {item["name"] for item in analysis_db.list_techniques(engine, account_id)} == {
        "工具动作露底", "短判断压危机", "三拍递进"}
    assert analysis_db.list_candidates(engine, account_id) == []
    dimensions = analysis_db.list_dimensions(engine, account_id)
    assert [row["dimension_key"] for row in dimensions] == ["rhythm"]
    assert dimensions[0]["category"] == "outline"
    assert {item["name"]: item["category"] for item in analysis_db.list_techniques(engine, account_id)}["三拍递进"] == "outline"

    # 已批准维度下一次作为正常维度处理，不再进入候选。
    next_report = NovelAnalysisReport(
        overview="节奏是这段文本最突出的写法。", source_summary="同一章。", limitations=[],
        dimensions=[NovelAnalysisDimension(
            dimension="rhythm", label="节奏构造", available=True, summary="三拍推进",
            techniques=[technique("rhythm", "三拍递进", "旧港的维修铺只剩他一个人。")])],
        extra_techniques=[])
    cleaned_next = analysis.clean_analysis(
        next_report, [{"name": "第一章.txt", "text": SOURCE}],
        analysis_db.list_dimensions(engine, account_id))
    assert cleaned_next.extra_techniques == []
    rhythm = next(item for item in cleaned_next.dimensions if item.dimension == "rhythm")
    assert rhythm.available and rhythm.label == "节奏构造"


def test_selected_techniques_are_injected_into_generation_context():
    engine = create_test_db()
    account_id = create_account(engine, "analyst", "test-password")
    cleaned = analysis.clean_analysis(report(), [{"name": "第一章.txt", "text": SOURCE}])
    analysis_db.save_analysis(engine, account_id, ["第一章.txt"], len(SOURCE), "",
                              cleaned.model_dump(mode="json"), set(analysis.FIXED_DIMENSIONS),
                              analysis.DIMENSION_LABELS)
    technique = next(item for item in analysis_db.list_techniques(engine, account_id)
                     if item["name"] == "工具动作露底")
    payload = WritingInput(technique_ids=[technique["id"]])
    context = {}
    result = asyncio.run(writing.apply_techniques(
        context, payload, Account(id=account_id, username="analyst", display_name="拆解者"),
        engine, writing.OUTLINE_TECHNIQUE_CATEGORIES))
    assert result["techniques"][0]["name"] == "工具动作露底"
    assert "工具动作露底" in result["technique_prompt"]
    assert "不照搬原文" in result["technique_prompt"]
    with pytest.raises(HTTPException) as error:
        asyncio.run(writing.apply_techniques(
            {}, payload, Account(id=account_id, username="analyst", display_name="拆解者"),
            engine, writing.PROSE_TECHNIQUE_CATEGORIES))
    assert error.value.status_code == 422


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


def test_pasted_chapter_is_accepted_and_limited(monkeypatch):
    engine = create_test_db()
    account_id = create_account(engine, "analyst", "test-password")
    captured = {}

    async def capture(role, context, request):
        captured["documents"] = context["documents"]
        return report()

    monkeypatch.setattr(analysis, "generate", capture)
    app.dependency_overrides[get_database] = lambda: engine
    app.dependency_overrides[get_current_account] = lambda: Account(
        id=account_id, username="analyst", display_name="拆解者")
    try:
        with TestClient(app) as client:
            too_long = client.post("/api/v1/novel-analysis/analyze/tasks",
                                   data={"text": "字" * 25001})
            assert too_long.status_code == 422
            response = client.post("/api/v1/novel-analysis/analyze/tasks",
                                   data={"text": SOURCE, "instruction": "重点看人物"})
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            for _ in range(200):
                detail = client.get(f"/api/v1/generation-jobs/{job_id}").json()
                if detail["status"] != "running":
                    break
                time.sleep(0.02)
            assert detail["status"] == "completed", detail
            assert detail["title"] == "小说拆解 · 粘贴文本"
            assert [row["name"] for row in captured["documents"]] == ["粘贴文本"]
            assert captured["documents"][0]["text"] == SOURCE
    finally:
        app.dependency_overrides.pop(get_database, None)
        app.dependency_overrides.pop(get_current_account, None)


def test_approve_candidate_endpoint(monkeypatch):
    engine = create_test_db()
    account_id = create_account(engine, "analyst", "test-password")

    async def fake_generate(role, context, request):
        return report_with_extra()

    monkeypatch.setattr(analysis, "generate", fake_generate)
    app.dependency_overrides[get_database] = lambda: engine
    app.dependency_overrides[get_current_account] = lambda: Account(
        id=account_id, username="analyst", display_name="拆解者")
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/novel-analysis/analyze/tasks",
                files={"files": ("第一章.txt", SOURCE.encode("utf-8"), "text/plain")})
            assert response.status_code == 202, response.text
            job_id = response.json()["id"]
            for _ in range(200):
                detail = client.get(f"/api/v1/generation-jobs/{job_id}").json()
                if detail["status"] != "running":
                    break
                time.sleep(0.02)
            candidates = client.get("/api/v1/novel-analysis/candidates").json()["items"]
            assert [item["name"] for item in candidates] == ["三拍递进"]
            approved = client.post(f"/api/v1/novel-analysis/candidates/{candidates[0]['id']}/approve?category=prose")
            assert approved.status_code == 200, approved.text
            assert approved.json()["category"] == "prose"
            assert client.get("/api/v1/novel-analysis/candidates").json()["items"] == []
            library = client.get("/api/v1/novel-analysis/techniques").json()
            extra = next(item for item in library["items"] if item["name"] == "三拍递进")
            assert extra["category"] == "prose" and extra["category_label"] == "正文"
    finally:
        app.dependency_overrides.pop(get_database, None)
        app.dependency_overrides.pop(get_current_account, None)


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
