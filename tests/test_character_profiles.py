"""Outline-based dossiers persist actions, results and progress without new tables."""

import json
import threading

import pytest
from sqlalchemy import text

from agents import writing_agents as agents
from api.writing_schemas import CharacterDraftList, CharacterProfileList
from api import novel_character_profile_api as profile_api
from test_character_generation import novel, person, rows, next_volume, volume_cast, volume_profile


def existing(novel, name="林川", role="主角"):
    return novel["create"]("characters", project_id=novel["project"], name=name, role=role,
                           summary="寻脉者", personality="谨慎", abilities="只能感知水流")


def profile_result(context, ids):
    return CharacterProfileList(profiles=[dict(character_id=record_id, volume_profiles=[volume_profile(context)]) for record_id in ids])


def path(novel, volume_id=None):
    return f'/api/v1/writing/volumes/{volume_id or novel["volume"]}/character-profiles'


def directory(novel, **kwargs):
    return novel["client"].get(f'/api/v1/writing/projects/{novel["project"]}/character-profiles',
        params={"outline_id": novel["outline"], **kwargs}, headers=novel["headers"]["writer"])


def test_supporting_generation_saves_stage_dossier_with_outline_and_real_node(novel):
    response = novel["client"].post(novel["supporting"], json={"volume_id": novel["volume"], "supporting_count": 1},
                                     headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    assert response.json()["profile_character_ids"] == response.json()["character_ids"]
    cast = volume_cast(novel, novel["volume"])
    assert len(cast) == 1
    for value in ("大纲计划 · v1 · 第1卷", "渡河", "本部分行动（计划）", "阶段结果（计划）", "与小说进度的关系", "不代表正文已发生"):
        assert value in cast[0]["scenes"]
    assert "成功引路并取得主角信任" in cast[0]["scenes"]
    assert "volume_profiles" not in rows(novel)[0]
    assert directory(novel).json()["items"][0]["progress"] == {
        "planned_chapters": 0, "written_chapters": 0, "last_written_chapter": None}


def test_core_generation_can_follow_selected_outline_and_save_dossiers(novel):
    response = novel["client"].post(novel["core"], json={"outline_id": novel["outline"], "volume_id": novel["volume"],
        "protagonist_count": 1, "important_count": 1}, headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    assert response.json()["outline_id"] == novel["outline"]
    assert response.json()["profile_character_ids"] == response.json()["character_ids"]
    assert len(volume_cast(novel, novel["volume"])) == 2


@pytest.mark.parametrize("suffix", ["", "/tasks"])
def test_core_generation_rejects_foreign_outline_and_volume_without_outline(novel, monkeypatch, suffix):
    other = novel["create"]("projects", genre="都市", setting="现代", tone="轻松", target_length="短篇", protagonist_goal="生活")
    outline = novel["create"]("outlines", project_id=other, summary="外部大纲", central_conflict="冲突")
    calls = []
    monkeypatch.setattr(agents, "generate", lambda *args: calls.append(args))
    response = novel["client"].post(novel["core"] + suffix, json={"outline_id": outline}, headers=novel["headers"]["writer"])
    assert response.status_code == 404, response.text
    response = novel["client"].post(novel["core"] + suffix, json={"volume_id": novel["volume"]}, headers=novel["headers"]["writer"])
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize("problem", ["missing", "wrong_volume", "foreign_beat", "duplicate_volume", "duplicate_beat", "no_node"])
def test_invalid_dossiers_rejected_before_saving_new_people(novel, monkeypatch, problem):
    def invalid(role, context):
        profile = volume_profile(context)
        profiles = [profile]
        if problem == "missing": profiles = []
        elif problem == "wrong_volume": profile["volume_id"] = 99999
        elif problem == "foreign_beat": profile["plot_beat_ids"] = [99999]
        elif problem == "duplicate_volume": profiles = [profile, profile]
        elif problem == "duplicate_beat": profile["plot_beat_ids"] *= 2
        else: profile["plot_beat_ids"] = []
        return CharacterDraftList(characters=[dict(**person("舟客", "supporting"), volume_profiles=profiles)])

    monkeypatch.setattr(agents, "generate", invalid)
    response = novel["client"].post(novel["supporting"], json={"volume_id": novel["volume"], "supporting_count": 1},
                                     headers=novel["headers"]["writer"])
    assert response.status_code == 502, response.text
    assert rows(novel) == [] and volume_cast(novel, novel["volume"]) == []


def test_outline_wide_generation_persists_each_volume_instead_of_losing_dossiers(novel, monkeypatch):
    second = next_volume(novel)

    def generate(role, context):
        profiles = [volume_profile({**context, "target_volume": volume}) for volume in context["volumes"]]
        return CharacterDraftList(characters=[dict(**person("舟客", "supporting"), volume_profiles=profiles)])

    monkeypatch.setattr(agents, "generate", generate)
    response = novel["client"].post(novel["supporting"], json={"supporting_count": 1}, headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    first = volume_cast(novel, novel["volume"])[0]
    later = volume_cast(novel, second)[0]
    assert first["character_id"] == later["character_id"] == response.json()["character_ids"][0]
    assert "第1卷" in first["scenes"] and "第2卷" in later["scenes"]


def test_existing_core_and_supporting_profiles_keep_base_and_previous_volume(novel, monkeypatch):
    ids = [existing(novel), existing(novel, "舟客", "配角")]
    before = rows(novel)
    old = novel["create"]("volume-characters", volume_id=novel["volume"], character_id=ids[0], scenes="第一卷旧档案")
    second = next_volume(novel)

    def generate(role, context):
        assert role == agents.CHARACTER_PROFILES
        assert [row["id"] for row in context["selected_characters"]] == ids
        assert context["volumes"][0]["cast"][0]["id"] == old
        return profile_result(context, ids)

    monkeypatch.setattr(agents, "generate", generate)
    response = novel["client"].post(path(novel, second), json={"character_ids": ids}, headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    assert response.json()["profile_character_ids"] == ids
    assert response.json()["character_ids"] == []
    assert rows(novel) == before
    assert volume_cast(novel, novel["volume"])[0]["scenes"] == "第一卷旧档案"
    assert len(volume_cast(novel, second)) == 2
    repeated = novel["client"].post(path(novel, second), json={"character_ids": ids}, headers=novel["headers"]["writer"])
    assert repeated.status_code == 201 and len(volume_cast(novel, second)) == 2


def test_maximum_profile_selection_can_be_saved_as_one_batch(novel, monkeypatch):
    ids = [existing(novel, f"人物{number}", "配角") for number in range(20)]
    monkeypatch.setattr(agents, "generate", lambda role, context: profile_result(context, ids))
    response = novel["client"].post(path(novel), json={"character_ids": ids}, headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    assert response.json()["profile_character_ids"] == ids
    assert len(volume_cast(novel, novel["volume"])) == 20


def test_generation_uses_progress_snapshot_and_directory_tracks_later_drafts(novel, monkeypatch):
    character = existing(novel)
    written = novel["create"]("chapters", project_id=novel["project"], volume_id=novel["volume"], chapter_number=1,
                              title="渡河", summary="渡河的章节计划", content="正文尚未确认角色的最终结局", status="draft")
    blank = novel["create"]("chapters", project_id=novel["project"], volume_id=novel["volume"], chapter_number=3,
                            title="未来", content="\n\t　 ")

    def generate(role, context):
        progress = context["writing_progress"][0]
        assert progress["planned_chapters"] == 2 and progress["written_chapters"] == 1
        assert progress["last_written_chapter"] == 1
        assert "正文尚未确认角色的最终结局" not in json.dumps(context, ensure_ascii=False)
        assert "volume_profiles" in role.output.model_json_schema()["$defs"]["ExistingCharacterProfile"]["properties"]
        return profile_result(context, [character])

    monkeypatch.setattr(agents, "generate", generate)
    response = novel["client"].post(path(novel), json={"character_ids": [character]}, headers=novel["headers"]["writer"])
    assert response.status_code == 201, response.text
    first = directory(novel).json()["items"][0]
    assert first["progress"] == {"planned_chapters": 2, "written_chapters": 1, "last_written_chapter": 1}
    novel["client"].patch(f"/api/v1/chapters/{blank}", json={"content": "现在保存第三章正文", "status": "draft"}, headers=novel["headers"]["writer"]).raise_for_status()
    last = directory(novel).json()["items"][0]
    assert last["progress"] == {"planned_chapters": 2, "written_chapters": 2, "last_written_chapter": 3}
    assert last["scenes"] == first["scenes"]  # Progress does not convert planned events into facts.


def test_progress_edit_during_generation_preserves_existing_dossier(novel, monkeypatch):
    character = existing(novel)
    novel["create"]("volume-characters", volume_id=novel["volume"], character_id=character, scenes="原档案")
    chapter = novel["create"]("chapters", project_id=novel["project"], volume_id=novel["volume"], chapter_number=1, title="渡河")

    def changed(role, context):
        with novel["engine"].begin() as connection:
            connection.execute(text("UPDATE novel_chapter SET content='新正文' WHERE id=:id"), {"id": chapter})
        return profile_result(context, [character])

    monkeypatch.setattr(agents, "generate", changed)
    response = novel["client"].post(path(novel), json={"character_ids": [character]}, headers=novel["headers"]["writer"])
    assert response.status_code == 409, response.text
    assert volume_cast(novel, novel["volume"])[0]["scenes"] == "原档案"


@pytest.mark.parametrize("problem", ["missing", "duplicate", "unknown", "wrong_volume"])
def test_existing_profile_output_must_match_selection(novel, monkeypatch, problem):
    character = existing(novel)

    def invalid(role, context):
        ids = [character]
        if problem == "duplicate": ids *= 2
        elif problem == "unknown": ids = [99999]
        elif problem == "missing": ids = [existing(novel, "多余角色", "配角")]
        result = profile_result(context, ids)
        if problem == "wrong_volume": result.profiles[0].volume_profiles[0].volume_id = 99999
        return result

    monkeypatch.setattr(agents, "generate", invalid)
    response = novel["client"].post(path(novel), json={"character_ids": [character]}, headers=novel["headers"]["writer"])
    assert response.status_code == 502, response.text
    assert volume_cast(novel, novel["volume"]) == []


@pytest.mark.parametrize("suffix", ["", "/tasks"])
def test_profile_requests_enforce_account_project_and_selection_before_model(novel, monkeypatch, suffix):
    character = existing(novel)
    calls = []
    monkeypatch.setattr(agents, "generate", lambda *args: calls.append(args))
    for headers, payload, status in [({}, {"character_ids": [character]}, 401),
        (novel["headers"]["other"], {"character_ids": [character]}, 404),
        (novel["headers"]["writer"], {"character_ids": [99999]}, 404),
        (novel["headers"]["writer"], {"character_ids": []}, 422),
        (novel["headers"]["writer"], {"character_ids": list(range(1, 22))}, 422),
        (novel["headers"]["writer"], {"character_ids": [character, character]}, 422)]:
        response = novel["client"].post(path(novel) + suffix, json=payload, headers=headers)
        assert response.status_code == status, response.text
    other = novel["create"]("projects", genre="都市", setting="现代", tone="轻松", target_length="短篇", protagonist_goal="生活")
    foreign = novel["create"]("characters", project_id=other, name="外部角色", role="配角", summary="其他小说")
    assert novel["client"].post(path(novel) + suffix, json={"character_ids": [character, foreign]}, headers=novel["headers"]["writer"]).status_code == 404
    assert calls == []


def test_profile_directory_is_account_and_outline_scoped(novel):
    character = existing(novel)
    novel["create"]("volume-characters", volume_id=novel["volume"], character_id=character, scenes="第一版档案")
    outline = novel["create"]("outlines", project_id=novel["project"], version_no=2, summary="另一版", central_conflict="冲突")
    volume = novel["create"]("outline-volumes", outline_id=outline, volume_number=1, title="新卷", summary="另一版分卷")
    novel["create"]("volume-characters", volume_id=volume, character_id=character, scenes="第二版档案")
    assert [row["scenes"] for row in directory(novel).json()["items"]] == ["第一版档案"]
    assert [row["scenes"] for row in directory(novel, outline_id=outline).json()["items"]] == ["第二版档案"]
    url = f'/api/v1/writing/projects/{novel["project"]}/character-profiles?outline_id={novel["outline"]}'
    assert novel["client"].get(url).status_code == 401
    assert novel["client"].get(url, headers=novel["headers"]["other"]).status_code == 404


def test_profile_directory_decodes_legacy_json_and_does_not_count_blank_json_bodies(novel, monkeypatch):
    character = existing(novel)
    cast = novel["create"]("volume-characters", volume_id=novel["volume"], character_id=character, scenes="旧档案")
    chapter = novel["create"]("chapters", project_id=novel["project"], volume_id=novel["volume"], chapter_number=1, title="渡河")
    with novel["engine"].begin() as connection:
        connection.execute(text("UPDATE novel_volume_character SET scenes=:scenes WHERE id=:id"),
            {"id": cast, "scenes": json.dumps("旧 JSON 档案", ensure_ascii=False)})
        connection.execute(text("UPDATE novel_chapter SET content=:content WHERE id=:id"), {"id": chapter, "content": json.dumps("\n\t ")})
    monkeypatch.setattr(profile_api, "legacy_json_columns", lambda connection, table: frozenset(
        {"novel_volume_character": ["scenes"], "novel_chapter": ["content"]}.get(table, [])))
    response = directory(novel)
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["scenes"] == "旧 JSON 档案" and row["progress"]["written_chapters"] == 0


def test_background_profile_job_returns_saved_dossier(novel, monkeypatch):
    character = existing(novel)
    entered, release = threading.Event(), threading.Event()

    def waiting(role, context):
        entered.set()
        assert release.wait(10)
        return profile_result(context, [character])

    monkeypatch.setattr(agents, "generate", waiting)
    try:
        response = novel["client"].post(path(novel) + "/tasks", json={"character_ids": [character]}, headers=novel["headers"]["writer"])
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["kind"] == "characters" and entered.wait(5)
        release.set()
        events = novel["client"].get(f'/api/v1/generation-jobs/{job["id"]}/events', headers=novel["headers"]["writer"])
        assert "event: result" in events.text and "event: error" not in events.text
        assert "阶段结果（计划）" in volume_cast(novel, novel["volume"])[0]["scenes"]
    finally:
        release.set()
