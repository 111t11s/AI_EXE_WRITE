"""Rewriting reads original text, preserves outline versions and guards overwrites."""

import json

import pytest
from sqlalchemy import select, update

from agents import writing_agents as agents
from api.novel_tables import tables
from api import novel_writing_store as store
from api.writing_schemas import ChapterDraft
from test_character_generation import novel  # noqa: F401 - shared real API/MCP fixture
from test_writing_workflow import outline as outline_artifact


ORIGINAL = "舟客将小船撑离河岸。" * 20
REWRITTEN = "舟客收起缆绳，望着少年问起寻脉的缘由。" * 20


def prepare(novel, *, content=ORIGINAL, status="draft", characters=True):
    if characters:
        novel["create"]("characters", project_id=novel["project"], name="舟客", role="配角", summary="渡河向导")
    return novel["create"]("chapters", project_id=novel["project"], volume_id=novel["volume"],
                           chapter_number=1, title="渡河", summary="舟客帮助渡河", content=content, status=status)


def get(novel, path):
    response = novel["client"].get("/api/v1/" + path, headers=novel["headers"]["writer"])
    assert response.status_code == 200, response.text
    return response.json()


def result(novel, path, payload, suffix):
    response = novel["client"].post(path + suffix, json=payload, headers=novel["headers"]["writer"])
    if suffix == "/tasks":
        assert response.status_code == 202, response.text
        job = response.json()
        response = novel["client"].get(f'/api/v1/generation-jobs/{job["id"]}/events',
                                        headers=novel["headers"]["writer"])
    if suffix:
        assert response.status_code == 200 and "event: error" not in response.text, response.text
        event = response.text.strip().split("\n\n")[-1]
        assert event.startswith("event: result"), event
        return json.loads(event.split("data: ", 1)[1])
    assert response.status_code in (200, 201), response.text
    return response.json()


@pytest.mark.parametrize("suffix", ["", "/stream", "/tasks"])
def test_outline_rewrite_reads_selected_version_and_keeps_old_story(novel, monkeypatch, suffix):
    chapter_id = prepare(novel)
    cast_id = novel["create"]("volume-characters", volume_id=novel["volume"], character_id=1, scenes="帮助渡河")
    old_outline = get(novel, f'outlines/{novel["outline"]}')
    old_volume = get(novel, f'outline-volumes/{novel["volume"]}')
    old_chapter = get(novel, f"chapters/{chapter_id}")

    def generate(role, context, profile=None):
        assert role is agents.OUTLINE_REWRITE
        assert context["outline"]["summary"] == "寻找灵脉"
        assert context["volumes"][0]["beats"][0]["title"] == "渡河"
        assert context["volumes"][0]["cast"][0]["scenes"] == "帮助渡河"
        assert context["characters"][0]["name"] == "舟客"
        assert context["request"]["instruction"] == "加强冲突"
        return outline_artifact()

    monkeypatch.setattr(agents, "generate", generate)
    receipt = result(novel, f'/api/v1/writing/outlines/{novel["outline"]}/rewrite',
                     {"volume_count": 1, "instruction": "加强冲突"}, suffix)
    assert receipt["outline_id"] != novel["outline"] and receipt["version_no"] == 2
    assert receipt["volume_ids"] != [novel["volume"]] and receipt["plot_beat_count"] == 1
    assert get(novel, f'outlines/{novel["outline"]}') == old_outline
    assert get(novel, f'outline-volumes/{novel["volume"]}') == old_volume
    assert get(novel, f"chapters/{chapter_id}") == old_chapter
    assert get(novel, f"volume-characters/{cast_id}")["scenes"] == "帮助渡河"


@pytest.mark.parametrize("suffix", ["", "/stream", "/tasks"])
def test_body_rewrite_reads_original_and_saves_complete_draft(novel, monkeypatch, suffix):
    chapter_id = prepare(novel, status="reviewed")

    def generate(role, context, profile=None):
        assert role is agents.DRAFT_REWRITE
        assert context["chapter"]["content"] == ORIGINAL
        assert context["chapter"]["title"] == "渡河"
        assert context["characters"][0]["name"] == "舟客"
        assert context["plot_beats"][0]["title"] == "渡河"
        assert context["request"] == {"target_characters": 1500, "instruction": "加强对话",
                                      "technique_ids": []}
        return ChapterDraft(character_updates=[], content=REWRITTEN)

    monkeypatch.setattr(agents, "generate", generate)
    receipt = result(novel, f"/api/v1/writing/chapters/{chapter_id}/rewrite",
                     {"target_characters": 1500, "instruction": "加强对话"}, suffix)
    saved = get(novel, f"chapters/{chapter_id}")
    assert saved["content"] == REWRITTEN and saved["status"] == "draft"
    assert saved["title"] == "渡河" and saved["summary"] == "舟客帮助渡河"
    assert receipt["word_count"] == len(REWRITTEN) == saved["word_count"]


@pytest.mark.parametrize("resource", ["outlines", "chapters"])
@pytest.mark.parametrize("suffix", ["", "/stream", "/tasks"])
def test_rewrites_are_account_scoped(novel, monkeypatch, resource, suffix):
    record_id = prepare(novel) if resource == "chapters" else novel["outline"]
    monkeypatch.setattr(agents, "generate", lambda *args: pytest.fail("Unauthorized model invocation"))
    path = f"/api/v1/writing/{resource}/{record_id}/rewrite{suffix}"
    assert novel["client"].post(path, json={}).status_code == 401
    assert novel["client"].post(path, json={}, headers=novel["headers"]["other"]).status_code == 404


@pytest.mark.parametrize("content,characters", [("", True), (ORIGINAL, False), ("长" * 30001, True)],
                         ids=["empty-body", "missing-characters", "oversized-body"])
def test_body_rewrite_requires_original_characters_and_bounded_context(novel, monkeypatch, content, characters):
    chapter_id = prepare(novel, content=content, characters=characters, status="planned" if not content else "draft")
    monkeypatch.setattr(agents, "generate", lambda *args: pytest.fail("Invalid context reached model"))
    response = novel["client"].post(f"/api/v1/writing/chapters/{chapter_id}/rewrite", json={},
                                     headers=novel["headers"]["writer"])
    assert response.status_code == (422 if len(content) > 30000 else 409), response.text
    assert get(novel, f"chapters/{chapter_id}")["content"] == content


@pytest.mark.parametrize("field,value", [("content", "手工修改的正文"), ("title", "新标题"),
    ("summary", "新的章节计划"), ("status", "published"), ("volume_id", None), ("chapter_number", 2)])
def test_changed_chapter_is_never_overwritten(novel, monkeypatch, field, value):
    chapter_id = prepare(novel)

    def generate(role, context, profile=None):
        with novel["engine"].begin() as connection:
            connection.execute(update(tables["novel_chapter"]).where(
                tables["novel_chapter"].c.id == chapter_id).values(**{field: value}))
        return ChapterDraft(character_updates=[], content=REWRITTEN)

    monkeypatch.setattr(agents, "generate", generate)
    response = novel["client"].post(f"/api/v1/writing/chapters/{chapter_id}/rewrite", json={},
                                     headers=novel["headers"]["writer"])
    assert response.status_code == 409 and "未覆盖" in response.text
    saved = get(novel, f"chapters/{chapter_id}")
    assert saved[field] == value
    if field != "content":
        assert saved["content"] == ORIGINAL


def test_rewrite_supports_legacy_json_text_columns(novel, monkeypatch):
    chapter_id = prepare(novel)
    original = get(novel, f"chapters/{chapter_id}")
    chapter = tables["novel_chapter"]
    with novel["engine"].begin() as connection:
        connection.execute(update(chapter).where(chapter.c.id == chapter_id).values(
            summary=json.dumps(original["summary"]), content=json.dumps(ORIGINAL)))
    monkeypatch.setattr(store, "legacy_json_columns", lambda *args: frozenset({"summary", "content"}))
    receipt = store.save_rewritten_draft(novel["engine"], chapter_id, REWRITTEN, original)
    assert receipt.word_count == len(REWRITTEN)
    with novel["engine"].connect() as connection:
        assert json.loads(connection.scalar(select(chapter.c.content).where(chapter.c.id == chapter_id))) == REWRITTEN


@pytest.mark.parametrize("mode,suffix", [(mode, suffix) for mode in ("polish", "continue")
                                           for suffix in ("", "/stream", "/tasks")])
def test_polish_and_continue_keep_their_save_semantics(novel, monkeypatch, mode, suffix):
    chapter_id = prepare(novel, status="reviewed")

    def generate(role, context, profile=None):
        assert role is (agents.DRAFT_POLISH if mode == "polish" else agents.DRAFT_CONTINUE)
        assert context["chapter"]["content"] == ORIGINAL
        return ChapterDraft(character_updates=[], content=REWRITTEN)

    monkeypatch.setattr(agents, "generate", generate)
    receipt = result(novel, f"/api/v1/writing/chapters/{chapter_id}/{mode}",
                     {"target_characters": 1500, "instruction": "保留人物语气"}, suffix)
    saved = get(novel, f"chapters/{chapter_id}")
    expected = REWRITTEN if mode == "polish" else f"{ORIGINAL}\n\n{REWRITTEN}"
    assert saved["content"] == expected and saved["status"] == "draft"
    assert receipt["word_count"] == sum(not char.isspace() for char in expected)


def test_continue_rejects_stale_chapter_without_appending(novel, monkeypatch):
    chapter_id = prepare(novel)

    def generate(role, context, profile=None):
        with novel["engine"].begin() as connection:
            connection.execute(update(tables["novel_chapter"]).where(
                tables["novel_chapter"].c.id == chapter_id).values(content="作者刚刚修改的内容"))
        return ChapterDraft(character_updates=[], content=REWRITTEN)

    monkeypatch.setattr(agents, "generate", generate)
    response = novel["client"].post(f"/api/v1/writing/chapters/{chapter_id}/continue", json={},
                                     headers=novel["headers"]["writer"])
    assert response.status_code == 409 and "未追加" in response.text
    assert get(novel, f"chapters/{chapter_id}")["content"] == "作者刚刚修改的内容"


@pytest.mark.parametrize("resource", ["outlines", "chapters"])
def test_model_failure_preserves_original(novel, monkeypatch, resource):
    record_id = prepare(novel) if resource == "chapters" else novel["outline"]
    original = get(novel, f"{resource}/{record_id}")

    def fail(*args):
        raise RuntimeError("invalid model output")

    monkeypatch.setattr(agents, "generate", fail)
    response = novel["client"].post(f"/api/v1/writing/{resource}/{record_id}/rewrite", json={},
                                     headers=novel["headers"]["writer"])
    assert response.status_code == 502
    assert get(novel, f"{resource}/{record_id}") == original
    assert len(get(novel, "outlines")["items"]) == 1


@pytest.mark.parametrize("problem", ["count", "changed_outline", "changed_beat"])
def test_invalid_or_stale_outline_rewrite_has_no_partial_version(novel, monkeypatch, problem):
    def generate(role, context, profile=None):
        if problem == "count":
            artifact = outline_artifact()
            artifact.volumes.append(artifact.volumes[0].model_copy())
            return artifact
        table = tables["novel_outline" if problem == "changed_outline" else "novel_plot_beat"]
        with novel["engine"].begin() as connection:
            connection.execute(update(table).where(table.c.id == 1).values(summary="手工更新"))
        return outline_artifact()

    monkeypatch.setattr(agents, "generate", generate)
    response = novel["client"].post(f'/api/v1/writing/outlines/{novel["outline"]}/rewrite',
        json={"volume_count": 2 if problem == "count" else 1}, headers=novel["headers"]["writer"])
    assert response.status_code == (502 if problem == "count" else 409), response.text
    assert len(get(novel, "outlines")["items"]) == 1
    assert len(get(novel, "outline-volumes")["items"]) == 1
