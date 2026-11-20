"""Opening prompts are portable and apply only to the first three global chapters."""

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from openai import InternalServerError, OpenAI

from agents import prose_skills as skills, writing_agents as writing
from api import novel_writing_api as api
from api.writing_schemas import DraftRequest
from core.config import Settings
from tools.package_release import files as release_files


BODY = "船边的少年将信藏入袖中。\n\n" + "老人一言不发，解下缆绳，又将那盏灯举得高了些。" * 8


@pytest.fixture
def independent_opening(monkeypatch, tmp_path):
    root = tmp_path / "opening-chapters"
    shutil.copytree(skills.OPENING_SKILL_ROOT, root)
    monkeypatch.setattr(skills, "OPENING_SKILL_ROOT", root)
    return root


def test_opening_skill_runs_with_only_its_own_directory(independent_opening, monkeypatch):
    root = independent_opening
    original = Path.read_text
    accessed = set()

    def isolated_read(path, *args, **kwargs):
        assert path.resolve().is_relative_to(root.resolve())
        accessed.add(path.relative_to(root).as_posix())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", isolated_read)
    manifest = json.loads((root / "distillation.json").read_text(encoding="utf-8"))
    assert manifest["source_dependency"] is False
    assert manifest["reviewed_materials"] == len(manifest["materials"]) == 4
    assert {item["number"] for item in manifest["materials"]} == {1, 2, 3, 4}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        assert not any(value in content for value in ("meterials", "http://", "https://"))
        for target in re.findall(r"\]\(([^)]+)\)", content):
            linked = (path.parent / target).resolve()
            assert linked.is_relative_to(root.resolve()) and linked.is_file()
    for item in manifest["materials"]:
        assert all((root / name).is_file() for name in item["files"])
    assert skills.opening_skill_prompt(chapter_number=1)
    assert skills.opening_skill_prompt(chapter_number=3, rewrite=True)
    assert set(manifest["runtime_files"]) <= accessed


def test_server_package_contains_complete_opening_skill():
    packaged = set(release_files(["agents"]))
    assert {path for path in skills.OPENING_SKILL_ROOT.rglob("*") if path.is_file()} <= packaged


@pytest.mark.parametrize("chapter_number", [None, 0, -1, 4, 30, "1", True, 1.0])
def test_unknown_and_later_chapters_do_not_read_the_opening_skill(monkeypatch, chapter_number):
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=AssertionError("Unexpected skill read")))
    assert skills.opening_skill_prompt(chapter_number=chapter_number, rewrite=True) == ""


@pytest.mark.parametrize("chapter_number", [1, 2, 3])
@pytest.mark.parametrize("rewrite", [False, True])
def test_first_three_chapters_reach_the_streaming_model(independent_opening, monkeypatch, chapter_number, rewrite):
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(
        _env_file=None, API_KEY="test", PROXY_URL="https://model.test/v1", MODEL_NATIVE_TOOLS=False))
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        event = {"choices": [{"delta": {"content": BODY}, "finish_reason": "stop"}]}
        return httpx.Response(200, content=("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode(),
                              headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(writing, "OpenAI", lambda **kwargs: OpenAI(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    context = {"chapter": {"id": 70 + chapter_number, "chapter_number": chapter_number,
                           "content": BODY if rewrite else ""},
               "request": {"instruction": "慢热群像，不改变登场顺序", "target_characters": 1600}}
    result = writing.generate(writing.DRAFT_REWRITE if rewrite else writing.DRAFT, context)
    assert result.content == BODY and len(requests) == 1
    prompt = requests[0]["messages"][-1]["content"]
    assert skills.opening_skill_prompt(chapter_number=chapter_number, rewrite=rewrite) in prompt
    assert skills.prose_skill_prompt(rewrite=rewrite) in prompt
    assert json.loads(prompt.split("资料和用户要求：", 1)[1]) == context
    revision = (independent_opening / skills.REVISION_FILE).read_text(encoding="utf-8").strip()
    assert (revision in prompt) is rewrite
    assert '"reviewed_materials"' not in prompt


@pytest.mark.parametrize("chapter", [
    {"id": 1, "chapter_number": 4},
    {"id": 2, "chapter_number": 60, "volume_id": 1},
    {"id": 3, "title": "第一章"},
])
def test_database_ids_and_volume_starts_do_not_activate_opening(monkeypatch, chapter):
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    direct = Mock(return_value=BODY)
    monkeypatch.setattr(writing, "_request_without_tools", direct)
    writing.generate(writing.DRAFT, {"chapter": chapter})
    assert "# 小说黄金三章" not in direct.call_args.kwargs["prompt"]


@pytest.mark.parametrize("rewrite", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
def test_opening_prompt_is_kept_in_native_tools_and_fallback(independent_opening, monkeypatch, rewrite, fallback):
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=True))
    monkeypatch.setattr(writing, "has_generation_listener", lambda: False)
    crew = Mock()
    if fallback:
        crew.kickoff.side_effect = InternalServerError("unavailable", response=httpx.Response(
            500, request=httpx.Request("POST", "https://model.test/v1/chat/completions")), body=None)
    else:
        crew.kickoff.return_value = BODY
    task = Mock(return_value=object())
    monkeypatch.setitem(sys.modules, "crewai", SimpleNamespace(Crew=Mock(return_value=crew), LLM=Mock(),
                        Process=SimpleNamespace(sequential="sequential"), Task=task))
    monkeypatch.setattr(writing, "create_agent", lambda role, llm: object())
    direct = Mock(return_value=BODY)
    monkeypatch.setattr(writing, "_request_without_tools", direct)
    writing.generate(writing.DRAFT_REWRITE if rewrite else writing.DRAFT, {"chapter": {"chapter_number": 2}})
    prompt = task.call_args.kwargs["description"]
    assert skills.opening_skill_prompt(chapter_number=2, rewrite=rewrite) in prompt
    assert direct.call_count == int(fallback)
    if fallback:
        assert prompt.endswith(direct.call_args.kwargs["prompt"])


@pytest.mark.parametrize("role", [writing.PLAN, writing.OUTLINE, writing.OUTLINE_REWRITE,
    writing.CORE_CHARACTERS, writing.SUPPORTING_CHARACTERS, writing.CHARACTER_PROFILES,
    writing.CHARACTER_PROGRESS, writing.REVIEW, writing.CONSISTENCY, writing.CONSISTENCY_REPAIR])
def test_non_body_tasks_do_not_apply_opening_techniques(monkeypatch, role):
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    monkeypatch.setattr(writing, "opening_skill_prompt", Mock(side_effect=AssertionError("Wrong task")))

    class Requested(Exception):
        pass

    monkeypatch.setattr(writing, "_request_without_tools", Mock(side_effect=Requested))
    with pytest.raises(Requested):
        writing.generate(role, {"chapter": {"chapter_number": 1}})


@pytest.mark.parametrize("failure", ["entry", "methods", "revision", "empty", "invalid-name"])
def test_broken_opening_skill_prevents_model_requests(independent_opening, monkeypatch, failure):
    if failure in ("entry", "methods", "revision"):
        name = {"entry": "SKILL.md", "methods": "references/opening-methods.md", "revision": skills.REVISION_FILE}[failure]
        (independent_opening / name).unlink()
    elif failure == "empty":
        (independent_opening / "references/opening-methods.md").write_text("\n", encoding="utf-8")
    else:
        (independent_opening / "SKILL.md").write_text("---\nname: other\n---\nWrong skill", encoding="utf-8")
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    direct = Mock()
    monkeypatch.setattr(writing, "_request_without_tools", direct)
    with pytest.raises(skills.ProseSkillError, match="黄金三章"):
        writing.generate(writing.DRAFT_REWRITE, {"chapter": {"chapter_number": 1}})
    direct.assert_not_called()


def test_missing_opening_skill_returns_503_and_does_not_break_later_chapters(independent_opening, monkeypatch):
    (independent_opening / "SKILL.md").unlink()
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    direct = Mock(return_value=BODY)
    monkeypatch.setattr(writing, "_request_without_tools", direct)
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.generate(writing.DRAFT, {"chapter": {"chapter_number": 3}}, DraftRequest()))
    assert error.value.status_code == 503 and "黄金三章" in error.value.detail
    direct.assert_not_called()
    assert asyncio.run(api.generate(writing.DRAFT, {"chapter": {"chapter_number": 4}}, DraftRequest())).content == BODY
