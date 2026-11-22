"""Body-derived character facts survive reloads and respect chapter chronology."""

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update

from agents import writing_agents as agents
from api import character_progress as facts, novel_writing_store as store
from api.novel_tables import tables
from api.writing_schemas import ChapterContentDraft, ChapterDraft, CharacterProgressAnalysis
from db.character_progress import character_progress
from db.manage_admin import set_admin
from db.migrate_character_progress import migrate_schema
from test_character_generation import novel


@pytest.fixture
def story(novel):
    person = novel["create"]("characters", project_id=novel["project"], name="林川", role="主角",
                             summary="守护家乡", personality="谨慎", abilities="感知水流")
    cast = novel["create"]("volume-characters", volume_id=novel["volume"], character_id=person, scenes="未来计划：到卷末才完成突破")
    return {**novel, "person": person, "cast": cast, "progress": f'/api/v1/writing/projects/{novel["project"]}/character-progress'}


def planned(story, number, volume=True):
    return story["create"]("chapters", project_id=story["project"], volume_id=story["volume"] if volume else None,
                           chapter_number=number, title=f"行程{number}", summary="继续前行")


def artifact(story, action="林川在渡口救起落水的舟客。", state="仍在渡口，右臂受伤，尚未突破"):
    body = action + "河面的风吹动衣袖，他将湿透的绳索收在船舷，望着远处尚未消散的雾气。" * 8
    return ChapterDraft(content=body, character_updates=[dict(character_id=story["person"], actions=action,
        current_state=state, relationships="与舟客建立互信", evidence=[action])])


def write(story, monkeypatch, chapter, result=None, rewrite=False, suffix=""):
    monkeypatch.setattr(agents, "generate", lambda role, context, profile=None: result or artifact(story))
    return story["client"].post(f'/api/v1/writing/chapters/{chapter}/{"rewrite" if rewrite else "draft"}{suffix}',
                                json={}, headers=story["headers"]["writer"])


def directory(story, **params):
    response = story["client"].get(story["progress"], params=params, headers=story["headers"]["writer"])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("suffix", ["", "/stream", "/tasks"])
def test_generated_body_and_verified_character_facts_are_saved_together(story, monkeypatch, suffix):
    chapter = planned(story, 1)
    original = story["client"].get(f'/api/v1/characters/{story["person"]}', headers=story["headers"]["writer"]).json()
    result = write(story, monkeypatch, chapter, suffix=suffix)
    if suffix == "/tasks":
        assert result.status_code == 202
        result = story["client"].get(f'/api/v1/generation-jobs/{result.json()["id"]}/events', headers=story["headers"]["writer"])
    assert result.status_code == 200 and ("event: result" in result.text if suffix else result.json()["status"] == "draft")
    entries = directory(story)["items"]
    assert len(entries) == 1 and entries[0]["character_id"] == story["person"]
    assert entries[0]["chapter_id"] == chapter and entries[0]["evidence"] == ["林川在渡口救起落水的舟客。"]
    assert "右臂受伤" in entries[0]["current_state"]
    assert story["client"].get(f'/api/v1/characters/{story["person"]}', headers=story["headers"]["writer"]).json() == original
    assert story["client"].get(f'/api/v1/volume-characters/{story["cast"]}', headers=story["headers"]["writer"]).json()["scenes"].startswith("未来计划")


def test_following_draft_reads_saved_state_and_excludes_future_facts(story, monkeypatch):
    first, second, future = (planned(story, number) for number in (1, 2, 9))
    write(story, monkeypatch, first).raise_for_status()
    write(story, monkeypatch, future, artifact(story, "林川终于完成突破。", "九章之后已突破")).raise_for_status()

    def generate(role, context, profile=None):
        assert role is agents.DRAFT
        assert [row["chapter_number"] for row in context["character_progress"]] == [1]
        assert "右臂受伤" in context["character_progress"][0]["current_state"]
        assert "九章之后" not in json.dumps(context, ensure_ascii=False)
        assert context["characters"][0]["abilities"] == "感知水流"
        return artifact(story, "林川包扎右臂后继续前行。", "右臂已包扎，尚未突破")

    monkeypatch.setattr(agents, "generate", generate)
    response = story["client"].post(f'/api/v1/writing/chapters/{second}/draft', json={}, headers=story["headers"]["writer"])
    assert response.status_code == 200, response.text
    assert directory(story, latest=True)["items"][0]["chapter_number"] == 9


@pytest.mark.parametrize("suffix", ["", "/stream", "/tasks"])
def test_rewrite_replaces_only_its_chapter_facts(story, monkeypatch, suffix):
    first, later = planned(story, 1), planned(story, 2)
    write(story, monkeypatch, first).raise_for_status()
    write(story, monkeypatch, later).raise_for_status()
    result = write(story, monkeypatch, first, artifact(story, "林川在渡口放下长剑。", "仍在渡口，已经放下长剑"), rewrite=True, suffix=suffix)
    if suffix == "/tasks":
        result = story["client"].get(f'/api/v1/generation-jobs/{result.json()["id"]}/events', headers=story["headers"]["writer"])
    assert result.status_code == 200 and ("event: result" in result.text if suffix else result.json()["status"] == "draft")
    entries = directory(story)["items"]
    assert [row["chapter_number"] for row in entries] == [2, 1]
    assert entries[0]["actions"] == "林川在渡口救起落水的舟客。"
    assert entries[1]["actions"] == "林川在渡口放下长剑。"
    assert directory(story, latest=True)["items"][0]["chapter_number"] == 2
    assert directory(story, latest=True, before_chapter_number=2)["items"][0]["actions"] == "林川在渡口放下长剑。"


def test_rewrite_without_character_events_clears_obsolete_chapter_facts(story, monkeypatch):
    chapter = planned(story, 1, volume=False)
    write(story, monkeypatch, chapter).raise_for_status()
    response = write(story, monkeypatch, chapter, ChapterDraft(content="河面安静，远山低垂。" * 20, character_updates=[]), rewrite=True)
    assert response.status_code == 200 and directory(story)["items"] == []


@pytest.mark.parametrize("problem", ["foreign", "missing", "duplicate", "fabricated-quote", "wrong-character-quote"])
def test_invalid_facts_never_save_either_body_or_archive(story, monkeypatch, problem):
    chapter = planned(story, 1)
    data = artifact(story).model_dump()
    if problem == "foreign":
        project = story["create"]("projects", genre="都市", setting="当代", tone="日常", target_length="短篇", protagonist_goal="生活")
        data["character_updates"][0]["character_id"] = story["create"]("characters", project_id=project, name="林川", role="配角", summary="别的小说")
    elif problem == "missing":
        data["character_updates"][0]["character_id"] = 999999
    elif problem == "duplicate":
        data["character_updates"].append(data["character_updates"][0])
    elif problem == "fabricated-quote":
        data["character_updates"][0]["evidence"] = ["林川已经突破成为神明。"]
    else:
        data["character_updates"][0]["evidence"] = ["河面的风吹动衣袖"]
    response = write(story, monkeypatch, chapter, ChapterDraft.model_validate(data))
    assert response.status_code == 502, response.text
    assert directory(story)["items"] == []
    saved = story["client"].get(f'/api/v1/chapters/{chapter}', headers=story["headers"]["writer"]).json()
    assert saved["content"] == "" and saved["status"] == "planned"


@pytest.mark.parametrize("rewrite", [False, True])
def test_failure_after_archive_writes_rolls_back_body_and_archive(story, monkeypatch, rewrite):
    chapter = planned(story, 1)
    if rewrite:
        write(story, monkeypatch, chapter).raise_for_status()
    previous = directory(story)["items"]
    previous_body = story["client"].get(f'/api/v1/chapters/{chapter}', headers=story["headers"]["writer"]).json()
    original = store.replace_progress

    def fail(*args):
        original(*args)
        raise HTTPException(409, "模拟保存故障")

    monkeypatch.setattr(store, "replace_progress", fail)
    response = write(story, monkeypatch, chapter, artifact(story, "林川在岸边停下。"), rewrite=rewrite)
    assert response.status_code == 409
    assert directory(story)["items"] == previous
    assert story["client"].get(f'/api/v1/chapters/{chapter}', headers=story["headers"]["writer"]).json() == previous_body


def test_manual_body_edits_invalidate_only_that_chapters_progress(story, monkeypatch):
    first, second = planned(story, 1), planned(story, 2)
    for chapter in (first, second):
        write(story, monkeypatch, chapter).raise_for_status()
    client, headers = story["client"], story["headers"]["writer"]
    client.patch(f'/api/v1/chapters/{first}', json={"summary": "只改摘要"}, headers=headers).raise_for_status()
    assert len(directory(story)["items"]) == 2
    client.patch(f'/api/v1/chapters/{first}', json={"content": "用户重新写的正文"}, headers=headers).raise_for_status()
    assert [row["chapter_id"] for row in directory(story)["items"]] == [second]
    client.delete(f'/api/v1/chapters/{second}', headers=headers).raise_for_status()
    assert directory(story)["items"] == []


def test_changed_body_hash_cannot_be_used_as_character_memory(story, monkeypatch):
    chapter = planned(story, 1)
    write(story, monkeypatch, chapter).raise_for_status()
    with story["engine"].begin() as connection:
        connection.execute(update(tables["novel_chapter"]).where(tables["novel_chapter"].c.id == chapter).values(content="数据库中直接改写的正文"))
    assert directory(story)["items"] == []


def test_progress_is_account_scoped_read_only_and_paginated(story, monkeypatch):
    for number in (1, 2, 3):
        write(story, monkeypatch, planned(story, number)).raise_for_status()
    client = story["client"]
    assert client.get(story["progress"]).status_code == 401
    assert client.get(story["progress"], headers=story["headers"]["other"]).status_code == 404
    assert client.post(story["progress"], json={}, headers=story["headers"]["writer"]).status_code == 405
    set_admin(story["engine"], "other")
    assert client.get(story["progress"], headers=story["headers"]["other"]).status_code == 200
    first, second = directory(story, limit=2), directory(story, limit=2, offset=2)
    assert first["has_more"] and [row["chapter_number"] for row in first["items"]] == [3, 2]
    assert not second["has_more"] and [row["chapter_number"] for row in second["items"]] == [1]
    assert client.get(story["progress"], params={"before_chapter_number": 0}, headers=story["headers"]["writer"]).status_code == 422


def test_migration_is_additive_and_idempotent(story, monkeypatch):
    character_progress.drop(story["engine"])
    migrate_schema(story["engine"])
    migrate_schema(story["engine"])
    write(story, monkeypatch, planned(story, 1)).raise_for_status()
    migrate_schema(story["engine"])
    assert len(directory(story)["items"]) == 1


def test_generated_schema_requires_explicit_character_updates():
    with pytest.raises(ValidationError):
        ChapterDraft(content="正文" * 100)

def test_analysis_skips_unverifiable_character_and_keeps_others(story, monkeypatch):
    chapter = planned(story, 1)
    companion = story["create"]("characters", project_id=story["project"], name="舟客", role="配角", summary="同行")
    body = "林川在渡口救起落水的舟客。" + "河面的风吹动衣袖，他将湿透的绳索收在船舷。" * 10
    write(story, monkeypatch, chapter, ChapterDraft(content=body, character_updates=[])).raise_for_status()

    def generate(role, context, profile=None):
        assert role is agents.CHARACTER_PROGRESS
        return CharacterProgressAnalysis(character_updates=[
            dict(character_id=story["person"], actions="救起舟客", current_state="右臂受伤",
                 evidence=["林川在渡口救起落水的舟客。"]),
            dict(character_id=companion, actions="收起绳索", current_state="安全",
                 evidence=["河面的风吹动衣袖"]),
        ])
    monkeypatch.setattr(agents, "generate", generate)
    response = story["client"].post(f'/api/v1/writing/chapters/{chapter}/character-progress', json={},
                                     headers=story["headers"]["writer"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated_count"] == 1 and result["skipped_count"] == 1
    assert "舟客" in result["warning"]
    assert [row["character_id"] for row in directory(story)["items"]] == [story["person"]]


def test_analysis_with_no_verifiable_updates_keeps_previous_facts(story, monkeypatch):
    chapter = planned(story, 1)
    write(story, monkeypatch, chapter).raise_for_status()
    before = directory(story)["items"]

    def generate(role, context, profile=None):
        return CharacterProgressAnalysis(character_updates=[
            dict(character_id=story["person"], actions="突破", current_state="成神",
                 evidence=["林川已经突破成为神明。"])])

    monkeypatch.setattr(agents, "generate", generate)
    response = story["client"].post(f'/api/v1/writing/chapters/{chapter}/character-progress', json={},
                                     headers=story["headers"]["writer"])
    assert response.status_code == 502, response.text
    assert "林川" in response.json()["detail"]
    assert directory(story)["items"] == before


def test_batch_keeps_saved_body_when_one_progress_analysis_fails(story, monkeypatch):
    first, second = planned(story, 1), planned(story, 2)
    body = artifact(story).content

    def generate(role, context, profile=None):
        if role is agents.DRAFT:
            return ChapterContentDraft(content=body)
        assert role is agents.CHARACTER_PROGRESS
        if context["chapter"]["chapter_number"] == 1:
            raise HTTPException(502, "原文依据不足")
        return CharacterProgressAnalysis(character_updates=[])

    monkeypatch.setattr(agents, "generate", generate)
    response = story["client"].post(
        f'/api/v1/writing/projects/{story["project"]}/chapters/batch-draft',
        json={"chapter_ids": [first, second]}, headers=story["headers"]["writer"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["completed_count"] == 2 and "第 1 章" in result["warning"]
    for chapter in (first, second):
        saved = story["client"].get(f'/api/v1/chapters/{chapter}', headers=story["headers"]["writer"]).json()
        assert saved["status"] == "draft" and saved["content"]

