import asyncio
import io
import time
import zipfile

from fastapi.testclient import TestClient

from api import document_topics_api as documents
from api.account_api import Account, get_current_account
from api.novel_crud_api import get_database
from api.writing_schemas import TrendIdea, TrendReport
from db import document_topics as topic_db
from db.migrate_accounts import create_account
from main import app
from test_writing_workflow import create_test_db


def report():
    return TrendReport(overview="两个文件选题方向", requirement_summary="", ideas=[
        TrendIdea(genre="都市", title="旧港新规矩", premise="旧港改造中的小人物",
                  discussion_hook="旧城改造该保留什么？", reference_topics=[]),
        TrendIdea(genre="科幻", title="文件之外", premise="从旧档案发现未来线索",
                  discussion_hook="如果档案能预测未来？", reference_topics=[]),
    ])


def docx_bytes(text):
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_parse_txt_gbk_and_docx():
    assert documents.parse_document("废稿.txt", "旧港的雨夜，主角决定留下来。".encode("gbk")) == "旧港的雨夜，主角决定留下来。"
    assert documents.parse_document("档案.docx", docx_bytes("第一章 旧港\n林川在码头等人。")) == "第一章 旧港\n林川在码头等人。"
    assert documents.parse_document("说明.md", b"# \xe6\xa0\x87\xe9\xa2\x98\n\n\xe6\xad\xa3\xe6\x96\x87") == "# 标题\n\n正文"


async def fake_generate(role, context, request):
    return report()


def test_analyze_documents_saves_result_to_database(monkeypatch):
    engine = create_test_db()
    account_id = create_account(engine, "uploader", "test-password")
    monkeypatch.setattr(documents, "generate", fake_generate)
    docs = [{"name": "废稿.txt", "chars": 12, "text": "旧港的雨夜，主角决定留下来。", "truncated": False}]
    receipt = asyncio.run(documents.analyze_documents(
        docs, "保留旧港设定", "都市", "普通读者", 2, None, account_id, engine))

    assert receipt.topics == ["废稿.txt"] and receipt.record_id is not None
    stored = topic_db.get_report(engine, account_id, receipt.record_id)
    assert stored["report"]["ideas"][0]["title"] == "旧港新规矩"
    listing = topic_db.list_reports(engine, account_id)
    assert listing["items"][0]["input_chars"] == 12


def _client(engine, account_id):
    app.dependency_overrides[get_database] = lambda: engine
    app.dependency_overrides[get_current_account] = lambda: Account(
        id=account_id, username="uploader", display_name="上传者")
    return TestClient(app)


def _wait_job(client, job_id):
    for _ in range(200):
        detail = client.get(f"/api/v1/generation-jobs/{job_id}").json()
        if detail["status"] != "running":
            return detail
        time.sleep(0.02)
    raise AssertionError("任务未完成")


def test_upload_file_and_list_history(monkeypatch):
    engine = create_test_db()
    account_id = create_account(engine, "uploader", "test-password")
    monkeypatch.setattr(documents, "generate", fake_generate)
    client = _client(engine, account_id)
    try:
        with client:
            response = client.post(
                "/api/v1/document-topics/analyze/tasks",
                files={"files": ("废稿.txt", "旧港的雨夜，主角决定留下来。".encode("utf-8"), "text/plain")},
                data={"instruction": "保留旧港设定", "genre_preference": "都市", "audience": "普通读者",
                      "idea_count": "2"})
            assert response.status_code == 202, response.text
            detail = _wait_job(client, response.json()["id"])
            assert detail["status"] == "completed", detail
            record_id = detail["result"]["record_id"]
            assert record_id
            history = client.get("/api/v1/document-topics").json()["items"]
            assert history[0]["id"] == record_id and history[0]["file_names"] == ["废稿.txt"]
            stored = client.get(f"/api/v1/document-topics/{record_id}").json()
            assert stored["report"]["topics"] == ["废稿.txt"]
    finally:
        app.dependency_overrides.pop(get_database, None)
        app.dependency_overrides.pop(get_current_account, None)


def test_pasted_text_is_accepted_and_limited(monkeypatch):
    engine = create_test_db()
    account_id = create_account(engine, "uploader", "test-password")
    captured = {}

    async def capture(role, context, request):
        captured["documents"] = context["documents"]
        return report()

    monkeypatch.setattr(documents, "generate", capture)
    client = _client(engine, account_id)
    try:
        with client:
            too_long = client.post("/api/v1/document-topics/analyze/tasks",
                                   data={"text": "字" * 25001})
            assert too_long.status_code == 422
            response = client.post("/api/v1/document-topics/analyze/tasks",
                                   data={"text": "旧港的雨夜，主角决定留下来。" * 100})
            assert response.status_code == 202, response.text
            detail = _wait_job(client, response.json()["id"])
            assert detail["status"] == "completed", detail
            assert any(row["name"] == "粘贴文本" for row in captured["documents"])
    finally:
        app.dependency_overrides.pop(get_database, None)
        app.dependency_overrides.pop(get_current_account, None)


def test_upload_rejects_unsupported_extension():
    engine = create_test_db()
    account_id = create_account(engine, "uploader", "test-password")
    client = _client(engine, account_id)
    try:
        with client:
            response = client.post(
                "/api/v1/document-topics/analyze/tasks",
                files={"files": ("恶意.exe", b"MZ", "application/octet-stream")})
            assert response.status_code == 415
            assert "不支持的文件格式" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_database, None)
        app.dependency_overrides.pop(get_current_account, None)
