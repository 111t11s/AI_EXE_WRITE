"""Self-contained prose prompts reach both model paths without source materials."""

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


BODY = "渡口的最后一盏灯亮了。\n\n" + "舟客解开缆绳，让少年先登船，目光却一直落在对岸的枯树上。" * 8


@pytest.fixture
def independent_skill(monkeypatch, tmp_path):
    # The temporary installation contains only the packaged skill, no other repo data.
    root = tmp_path / "prose-writing"
    shutil.copytree(skills.SKILL_ROOT, root)
    monkeypatch.setattr(skills, "SKILL_ROOT", root)
    return root


def test_skill_is_portable_with_only_internal_references(independent_skill, monkeypatch):
    root = independent_skill
    read_text = Path.read_text
    accessed = []

    def read_only_skill(path, *args, **kwargs):
        assert path.resolve().is_relative_to(root.resolve()), f"Unexpected external dependency: {path}"
        accessed.append(path.relative_to(root).as_posix())
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_only_skill)
    manifest = json.loads((root / "distillation.json").read_text(encoding="utf-8"))
    assert manifest["source_dependency"] is False
    assert manifest["reviewed_lessons"] == len(manifest["lessons"]) == 20
    assert {item["number"] for item in manifest["lessons"]} == set(range(1, 21))
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "meterials" not in text and "http://" not in text and "https://" not in text
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", text):
                resolved = (path.parent / target).resolve()
                assert resolved.is_relative_to(root.resolve()) and resolved.is_file()
    for item in manifest["lessons"]:
        assert all((root / name).is_file() for name in item["files"])
    assert skills.prose_skill_prompt()
    assert skills.prose_skill_prompt(rewrite=True)
    assert set(manifest["runtime_files"]) <= set(accessed)


def test_server_release_includes_entire_skill():
    included = set(release_files(["agents"]))
    assert {path for path in skills.SKILL_ROOT.rglob("*") if path.is_file()} <= included


@pytest.mark.parametrize("rewrite", [False, True])
def test_streaming_model_receives_full_skill_and_original_context(independent_skill, monkeypatch, rewrite):
    settings = Settings(_env_file=None, API_KEY="test-key", PROXY_URL="https://model.test/v1", MODEL_NATIVE_TOOLS=False)
    monkeypatch.setattr(writing, "get_settings", lambda: settings)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        wire = "data: " + json.dumps({"choices": [{"delta": {"content": BODY}, "finish_reason": "stop"}]})
        return httpx.Response(200, content=(wire + "\n\ndata: [DONE]\n\n").encode(),
                              headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(writing, "OpenAI", lambda **kwargs: OpenAI(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    context = {"project": {"genre": "科幻"}, "chapter": {"id": 12, "content": BODY if rewrite else ""},
               "previous_chapter": {"ending": "舟客将灯递给少年。"},
               "request": {"target_characters": 1500, "instruction": "第一人称，克制，不新增战斗"}}
    result = writing.generate(writing.DRAFT_REWRITE if rewrite else writing.DRAFT, context)
    assert result.content == BODY and len(requests) == 1
    assert requests[0]["stream"] is True and "tools" not in requests[0]
    prompt = requests[0]["messages"][-1]["content"]
    assert skills.prose_skill_prompt(rewrite=rewrite) in prompt
    assert json.loads(prompt.split("资料和用户要求：", 1)[1]) == context
    revision = (independent_skill / skills.REVISION_FILE).read_text(encoding="utf-8").strip()
    assert (revision in prompt) is rewrite
    assert "直接返回完整的小说正文纯文本" in prompt


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("rewrite", [False, True])
def test_native_tools_and_gateway_fallback_keep_the_same_skill(independent_skill, monkeypatch, fallback, rewrite):
    settings = Settings(_env_file=None, API_KEY="test-key", PROXY_URL="https://model.test/v1", MODEL_NATIVE_TOOLS=True)
    monkeypatch.setattr(writing, "get_settings", lambda: settings)
    monkeypatch.setattr(writing, "has_generation_listener", lambda: False)
    crew = Mock()
    if fallback:
        crew.kickoff.side_effect = InternalServerError("unavailable", response=httpx.Response(
            500, request=httpx.Request("POST", "https://model.test/v1/chat/completions")), body=None)
    else:
        crew.kickoff.return_value = BODY
    task = Mock(return_value=object())
    monkeypatch.setitem(sys.modules, "crewai", SimpleNamespace(
        Crew=Mock(return_value=crew), LLM=Mock(), Process=SimpleNamespace(sequential="sequential"), Task=task))
    monkeypatch.setattr(writing, "create_agent", lambda role, llm: object())
    direct = Mock(return_value=BODY)
    monkeypatch.setattr(writing, "_request_without_tools", direct)
    role = writing.DRAFT_REWRITE if rewrite else writing.DRAFT
    assert writing.generate(role, {"chapter": {"id": 12}}).content == BODY
    native_prompt = task.call_args.kwargs["description"]
    assert skills.prose_skill_prompt(rewrite=rewrite) in native_prompt
    assert direct.call_count == int(fallback)
    if fallback:
        assert native_prompt.endswith(direct.call_args.kwargs["prompt"])


@pytest.mark.parametrize("role", [writing.PLAN, writing.OUTLINE, writing.OUTLINE_REWRITE,
    writing.CORE_CHARACTERS, writing.SUPPORTING_CHARACTERS, writing.CHARACTER_PROFILES,
    writing.CHARACTER_PROGRESS, writing.REVIEW, writing.CONSISTENCY, writing.CONSISTENCY_REPAIR])
def test_non_prose_tasks_do_not_load_prose_skill(monkeypatch, role):
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    monkeypatch.setattr(writing, "prose_skill_prompt", Mock(side_effect=AssertionError("Must not load prose skill")))
    class ModelRequested(Exception):
        pass
    monkeypatch.setattr(writing, "_request_without_tools", Mock(side_effect=ModelRequested))
    with pytest.raises(ModelRequested):
        writing.generate(role, {})


@pytest.mark.parametrize("failure", ["missing-entry", "missing-reference", "empty-reference", "wrong-name", "missing-header"])
def test_incomplete_skill_fails_before_model_call(independent_skill, monkeypatch, failure):
    root = independent_skill
    if failure == "missing-entry":
        (root / "SKILL.md").unlink()
    elif failure == "missing-reference":
        (root / skills.REVISION_FILE).unlink()
    elif failure == "empty-reference":
        (root / "references/scene-craft.md").write_text("\n", encoding="utf-8")
    elif failure == "wrong-name":
        (root / "SKILL.md").write_text("---\nname: other\n---\nContent", encoding="utf-8")
    else:
        (root / "SKILL.md").write_text("Content only", encoding="utf-8")
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    model = Mock(side_effect=AssertionError("Must not call model"))
    monkeypatch.setattr(writing, "_request_without_tools", model)
    with pytest.raises(skills.ProseSkillError):
        writing.generate(writing.DRAFT_REWRITE, {})
    model.assert_not_called()


def test_missing_skill_returns_actionable_503(independent_skill, monkeypatch):
    (independent_skill / "SKILL.md").unlink()
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.generate(writing.DRAFT, {}, DraftRequest()))
    assert error.value.status_code == 503 and "正文写作技能" in error.value.detail
