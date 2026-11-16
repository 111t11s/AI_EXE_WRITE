"""Exercise real authenticated downloads, chapter ordering and complete exports."""

import json
from io import BytesIO
from zipfile import ZipFile
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from api.database import get_database
from api.novel_tables import tables
from api import novel_export_api as exports
from main import app
from test_writing_workflow import create_test_db


@pytest.fixture
def book(monkeypatch):
    engine = create_test_db()
    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: engine)
    with TestClient(app) as client:
        headers = {}
        for name in ("writer", "other"):
            response = client.post("/api/v1/auth/register", json={"username": name, "password": "test-password"})
            assert response.status_code == 201, response.text
            headers[name] = {"Authorization": "Bearer " + response.json()["access_token"]}

        def create(path, **values):
            response = client.post("/api/v1/" + path, json=values, headers=headers["writer"])
            assert response.status_code == 201, response.text
            return response.json()["id"]

        project = create("projects", title="寻脉", premise="少年寻找失落灵脉。", genre="仙侠", setting="山海",
                         tone="热血", target_length="短篇", protagonist_goal="守护家乡")
        outline = create("outlines", project_id=project, version_no=1, summary="旧版大纲摘要", central_conflict="宗门垄断", themes="信任")
        volume = create("outline-volumes", outline_id=outline, volume_number=1, title="寻脉卷", summary="渡河寻脉",
                        protagonist_goal="寻找向导", main_conflict="大河阻隔", climax="渡河脱险", ending_hook="古印苏醒")
        create("plot-beats", volume_id=volume, sort_order=2, title="脱险", summary="抵达对岸", foreshadowing="古印发光")
        create("plot-beats", volume_id=volume, sort_order=1, title="遇舟客", summary="遇到渡河向导")
        newer = create("outlines", project_id=project, version_no=2, summary="新版大纲摘要", central_conflict="灵气枯竭")
        newer_volume = create("outline-volumes", outline_id=newer, volume_number=1, title="远行卷", summary="继续远行")
        yield dict(client=client, headers=headers, engine=engine, create=create, project=project,
                   outline=outline, volume=volume, newer=newer, newer_volume=newer_volume,
                   path=f"/api/v1/projects/{project}/export")
    engine.dispose()


def chapter(book, number, content, volume=None):
    return book["create"]("chapters", project_id=book["project"], volume_id=volume, chapter_number=number,
                          title=f"章节{number}", summary="不应当代替正文的章节计划", content=content,
                          status="draft" if content.strip() else "planned")


def download(book, **params):
    return book["client"].get(book["path"], params=params, headers=book["headers"]["writer"])


def archive_files(response):
    assert response.status_code == 200, response.text if response.status_code != 200 else ""
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def archive_text(response):
    return "\n".join(content.decode("utf-8-sig") for content in archive_files(response).values())


@pytest.mark.parametrize("format", ["txt", "md"])
def test_export_all_saved_bodies_in_chapter_order_across_outline_versions(book, format):
    chapter(book, 3, "第三章正文。", book["newer_volume"])
    chapter(book, 1, "  第一章首段。\n\n第一章次段。", book["volume"])
    chapter(book, 2, " \n ")
    chapter(book, 4, "无分卷正文。")
    response = download(book, format=format)
    assert response.status_code == 200, response.text
    files = archive_files(response)
    text = "\n".join(content.decode("utf-8-sig") for content in files.values())
    assert text.index("第一章首段") < text.index("第三章正文") < text.index("无分卷正文")
    assert "章节2" not in text and "不应当代替正文" not in text
    assert "寻脉卷" in text and "远行卷" in text
    assert "旧版大纲摘要" not in text
    assert list(files) == [f"第{number:05d}章_章节{number}.{format}" for number in (1, 3, 4)]
    assert "第三章正文" not in next(iter(files.values())).decode("utf-8-sig")
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert f'filename="novel-{book["project"]}.zip"' in response.headers["content-disposition"]
    assert "寻脉.zip" in unquote(response.headers["content-disposition"])
    if format == "txt":
        assert all(body.startswith(b"\xef\xbb\xbf") for body in files.values())
        assert "  第一章首段。\r\n\r\n第一章次段。" in text
        assert "\r\n" in text
    else:
        assert text.startswith("# 寻脉\n")
        assert "## 第 1 卷 · 寻脉卷" in text and "### 第 1 章 · 章节1" in text
        assert "\r\n" not in text


@pytest.mark.parametrize("format", ["txt", "md"])
def test_selected_outline_is_complete_and_does_not_filter_old_chapters(book, format):
    chapter(book, 1, "原版章节正文", book["volume"])
    response = download(book, format=format, outline_id=book["newer"])
    assert response.status_code == 200, response.text
    files = archive_files(response)
    text = "\n".join(content.decode("utf-8-sig") for content in files.values())
    assert "新版大纲摘要" in text and "旧版大纲摘要" not in text
    assert "原版章节正文" in text
    old = download(book, format=format, outline_id=book["outline"])
    text = archive_text(old)
    assert "旧版大纲摘要" in text and "新版大纲摘要" not in text
    assert "宗门垄断" in text and "寻找向导" in text and "古印苏醒" in text
    assert text.index("遇到渡河向导") < text.index("抵达对岸")
    assert "古印发光" in text


def test_no_body_shows_actionable_error_but_can_export_outline_only(book):
    chapter(book, 1, "")
    response = download(book)
    assert response.status_code == 409 and "暂无可导出的正文" in response.json()["detail"]
    response = download(book, outline_id=book["outline"])
    assert response.status_code == 200 and "旧版大纲摘要" in archive_text(response)


def test_export_has_no_pagination_limit_for_long_novels(book):
    with book["engine"].begin() as connection:
        connection.execute(insert(tables["novel_chapter"]), [dict(
            project_id=book["project"], volume_id=None, chapter_number=number, title=f"第{number}章标题",
            summary="", content=f"章节正文编号{number}：" + "远行" * 2500, status="draft", word_count=5000,
        ) for number in range(1, 106)])
    response = download(book)
    assert response.status_code == 200
    assert sum(len(content) for content in archive_files(response).values()) > 1024 * 1024
    files = archive_files(response)
    text = "\n".join(content.decode("utf-8-sig") for content in files.values())
    assert text.index("章节正文编号1：") < text.index("章节正文编号105：")
    assert text.count("章节正文编号") == 105
    assert len(response.content) == int(response.headers["content-length"])


def test_export_decodes_legacy_json_text_fields(book, monkeypatch):
    chapter(book, 1, "正文原文\n下一段", book["volume"])
    columns = {"novel_project": {"premise"}, "novel_chapter": {"content", "summary"},
               "novel_outline": {"summary", "themes"}, "novel_outline_volume": {"summary"},
               "novel_plot_beat": {"summary", "foreshadowing"}}
    with book["engine"].begin() as connection:
        for name, fields in columns.items():
            relation = tables[name]
            for row in connection.execute(select(relation)).mappings().all():
                connection.execute(update(relation).where(relation.c.id == row["id"]).values(
                    **{field: json.dumps(row[field], ensure_ascii=False) for field in fields}))
    monkeypatch.setattr(exports, "legacy_json_columns", lambda connection, name: frozenset(columns.get(name, ())))
    response = download(book, format="md", outline_id=book["outline"])
    assert response.status_code == 200
    text = archive_text(response)
    assert "正文原文\n下一段" in text
    assert '"正文原文' not in text
    assert "旧版大纲摘要" in text and "古印发光" in text


def test_export_does_not_allow_other_accounts_or_projects(book):
    chapter(book, 1, "私人正文")
    assert book["client"].get(book["path"]).status_code == 401
    assert book["client"].get(book["path"], headers=book["headers"]["other"]).status_code == 404
    assert book["client"].get("/api/v1/projects/99999/export", headers=book["headers"]["writer"]).status_code == 404
    unrelated = book["create"]("projects", title="另一本小说", genre="都市", setting="城市", tone="轻松",
                                target_length="短篇", protagonist_goal="成长")
    wrong_outline = book["create"]("outlines", project_id=unrelated, summary="不可泄露的大纲", central_conflict="分歧")
    assert download(book, outline_id=wrong_outline).status_code == 404
    assert download(book, outline_id=99999).status_code == 404
    assert "不可泄露的大纲" not in archive_text(download(book))


@pytest.mark.parametrize("params", [{"format": "pdf"}, {"outline_id": "abc"}, {"outline_id": 0}])
def test_invalid_export_options_are_rejected(book, params):
    assert download(book, **params).status_code == 422


@pytest.mark.parametrize("title", ['寻脉/卷一:启程?\r\n".txt', "CON", "...", "中文📖书名"])
def test_download_filename_handles_chinese_and_unsafe_title_characters(book, title):
    with book["engine"].begin() as connection:
        connection.execute(update(tables["novel_project"]).where(
            tables["novel_project"].c.id == book["project"]).values(title=title))
    chapter(book, 1, "正文")
    response = download(book)
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    filename = unquote(disposition.split("filename*=UTF-8''")[1])
    assert not any(character in filename for character in '/\\:\r\n"?')
    assert filename.endswith(".zip") and filename != "CON.zip"


@pytest.mark.parametrize("method", ["get", "post"])
def test_selected_chapters_export_only_requested_bodies_in_order_without_duplicates(book, method):
    first = chapter(book, 1, "第一章正文", book["volume"])
    second = chapter(book, 2, "第二章不应导出")
    third = chapter(book, 3, "第三章正文", book["newer_volume"])
    empty = chapter(book, 4, "")
    options = dict(format="md", chapter_ids=[third, first, first, empty])
    response = (download(book, **options) if method == "get" else book["client"].post(
        book["path"], json=options, headers=book["headers"]["writer"]))
    files = archive_files(response)
    assert list(files) == ["第00001章_章节1.md", "第00003章_章节3.md"]
    assert "第二章不应导出" not in archive_text(response)
    with book["engine"].connect() as connection:
        assert connection.scalar(select(tables["novel_chapter"].c.content).where(
            tables["novel_chapter"].c.id == second)) == "第二章不应导出"


def test_selected_export_can_include_a_separate_outline_file(book):
    first = chapter(book, 1, "正文")
    response = book["client"].post(book["path"], json=dict(
        format="txt", chapter_ids=[first], outline_id=book["outline"]), headers=book["headers"]["writer"])
    files = archive_files(response)
    assert list(files) == ["大纲-v1.txt", "第00001章_章节1.txt"]
    assert "旧版大纲摘要" in files["大纲-v1.txt"].decode("utf-8-sig")
    assert "旧版大纲摘要" not in files["第00001章_章节1.txt"].decode("utf-8-sig")


@pytest.mark.parametrize("method", ["get", "post"])
def test_selection_rejects_missing_and_foreign_chapters_without_partial_downloads(book, method):
    first = chapter(book, 1, "当前小说正文")
    unrelated = book["create"]("projects", title="别的小说", genre="都市", setting="城市", tone="轻松",
                               target_length="短篇", protagonist_goal="成长")
    foreign = book["create"]("chapters", project_id=unrelated, chapter_number=1, content="别的小说正文")
    for ids in ([first, foreign], [first, 999999]):
        options = dict(chapter_ids=ids)
        response = (download(book, **options) if method == "get" else book["client"].post(
            book["path"], json=options, headers=book["headers"]["writer"]))
        assert response.status_code == 404
        assert "content-disposition" not in response.headers
    assert book["client"].post(book["path"], json={"chapter_ids": [first]}).status_code == 401
    assert book["client"].post(book["path"], json={"chapter_ids": [first]},
                               headers=book["headers"]["other"]).status_code == 404


@pytest.mark.parametrize("options", [{"chapter_ids": []}, {"chapter_ids": [0]},
    {"chapter_ids": ["abc"]}, {"format": "zip"}, {"outline_id": -1}])
def test_invalid_bulk_export_options_are_rejected(book, options):
    assert book["client"].post(book["path"], json=options, headers=book["headers"]["writer"]).status_code == 422


def test_only_empty_selected_chapters_does_not_fall_back_to_exporting_everything(book):
    empty = chapter(book, 1, " \n")
    chapter(book, 2, "不应导出")
    response = download(book, chapter_ids=[empty])
    assert response.status_code == 409


def test_zip_entry_names_are_safe_and_remain_unique_for_duplicate_titles(book):
    first = chapter(book, 1, "正文一")
    second = chapter(book, 2, "正文二")
    with book["engine"].begin() as connection:
        connection.execute(update(tables["novel_chapter"]).where(
            tables["novel_chapter"].c.id.in_([first, second])).values(title="../../CON:相同标题?"))
    names = list(archive_files(download(book)))
    assert len(names) == len(set(names)) == 2
    assert all("/" not in name and "\\" not in name and ":" not in name and "?" not in name for name in names)


def test_long_unicode_chapter_titles_fit_filesystem_filename_limits(book):
    chapter_id = chapter(book, 1, "正文")
    with book["engine"].begin() as connection:
        connection.execute(update(tables["novel_chapter"]).where(
            tables["novel_chapter"].c.id == chapter_id).values(title="📖中文" * 60))
    name, = archive_files(download(book))
    assert len(name.encode("utf-8")) <= 255
    assert name.startswith("第00001章_") and name.endswith(".txt")
