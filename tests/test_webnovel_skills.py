"""Task routing and isolated operation of the distilled webnovel foundations."""

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

from agents import webnovel_skills as skills, writing_agents as writing, worldbuilding_agent as world
from agents.prose_skills import ProseSkillError
from api import novel_writing_api as api
from api.writing_schemas import DraftRequest
from core.config import Settings
from tools.package_release import files as release_files


BODY = "窗外的潮水退去了。\n\n" + "她把地图展开，核对着昨日留下的记号，再将药箱背到肩上。" * 8


@pytest.fixture
def independent_skill(monkeypatch, tmp_path):
    root = tmp_path / "webnovel-basics"
    shutil.copytree(skills.SKILL_ROOT, root)
    monkeypatch.setattr(skills, "SKILL_ROOT", root)
    return root


def test_all_modes_work_from_the_packaged_directory_alone(independent_skill, monkeypatch):
    root = independent_skill.resolve()
    read_text = Path.read_text
    accessed = set()

    def isolated_read(path, *args, **kwargs):
        assert path.resolve().is_relative_to(root), f"External dependency: {path}"
        accessed.add(path.relative_to(root).as_posix())
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", isolated_read)
    manifest = json.loads((root / "distillation.json").read_text(encoding="utf-8"))
    assert manifest["source_dependency"] is False
    assert manifest["reviewed_materials"] == len(manifest["materials"]) == 14
    assert {item["number"] for item in manifest["materials"]} == set(manifest["available_numbers"])
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        assert not any(value in content for value in ("meterials", "http://", "https://"))
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", content):
                linked = (path.parent / target).resolve()
                assert linked.is_relative_to(root) and linked.is_file()
    for item in manifest["materials"]:
        assert all((root / name).is_file() for name in item["files"])
    for mode in skills.MODE_FILES:
        accessed.clear()
        prompt = skills.webnovel_skill_prompt(mode)
        expected = {"SKILL.md", *skills.MODE_FILES[mode]}
        assert set(manifest["automatic_modes"][mode]) == expected == accessed
        assert '"reviewed_materials"' not in prompt
    assert set(manifest["runtime_files"]) == {"SKILL.md", *(name for files in skills.MODE_FILES.values() for name in files)}


def test_release_contains_loader_and_the_entire_skill():
    included = set(release_files(["agents"]))
    assert Path(skills.__file__) in included
    assert {path for path in skills.SKILL_ROOT.rglob("*") if path.is_file()} <= included
    serial_root = skills.SKILL_ROOT.parent / "serial-writing-workflow"
    assert {path for path in serial_root.rglob("*") if path.is_file()} <= included


def test_full_course_review_accounts_for_every_document_and_its_destinations():
    root = skills.SKILL_ROOT
    entry = json.loads((root / "distillation.json").read_text(encoding="utf-8"))
    assert "full-course-distillation.json" in entry["additional_reviews"]
    review = json.loads((root / "full-course-distillation.json").read_text(encoding="utf-8"))
    materials = review["materials"]
    assert review["source_dependency"] is False
    assert review["reviewed_materials"] == len(materials) == 56
    assert len({item["id"] for item in materials}) == 56
    assert len({(item["category"], item["title"]) for item in materials}) == 56
    assert review["categories"] == len({item["category"] for item in materials}) == 19
    for item in materials:
        assert item["retained"] and item["excluded"]
        for name, files in item["outputs"].items():
            assert name in {"webnovel-basics", "serial-writing-workflow"}
            destination = root.parent / name
            for filename in files:
                path = (destination / filename).resolve()
                assert path.is_relative_to(destination.resolve()) and path.is_file()


def test_serial_workflow_is_portable_and_absent_from_creative_prompts(tmp_path, monkeypatch):
    from agents.prose_skills import _skill_text

    serial = skills.SKILL_ROOT.parent / "serial-writing-workflow"
    body = (serial / "SKILL.md").read_text(encoding="utf-8").split("\n---\n", 1)[1].strip()
    for mode in skills.MODE_FILES:
        assert body not in skills.webnovel_skill_prompt(mode)
    root = tmp_path / "serial-writing-workflow"
    shutil.copytree(serial, root)
    read_text = Path.read_text

    def isolated_read(path, *args, **kwargs):
        assert path.resolve().is_relative_to(root.resolve()), f"External dependency: {path}"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", isolated_read)
    prompt = _skill_text(root, "serial-writing-workflow", ("SKILL.md",), "连载创作安排")
    assert body in prompt
    assert not any(value in prompt for value in ("meterials", "http://", "https://"))

    # Optional workflows must remain usable after the whole skill is copied.
    for reference in ("references/goals-and-practice.md", "references/submission-preparation.md"):
        assert reference in prompt
        selected = _skill_text(root, "serial-writing-workflow", ("SKILL.md", reference), "连载创作安排")
        assert (root / reference).read_text(encoding="utf-8").strip() in selected
        assert not any(value in selected for value in ("meterials", "http://", "https://"))


def test_new_writer_review_accounts_for_all_sources_and_packaged_destinations():
    root = skills.SKILL_ROOT
    entry = json.loads((root / "distillation.json").read_text(encoding="utf-8"))
    filename = "new-writer-distillation.json"
    assert filename in entry["additional_reviews"]
    review = json.loads((root / filename).read_text(encoding="utf-8"))
    materials = review["materials"]
    assert review["source_dependency"] is False
    assert review["reviewed_materials"] == len(materials) == 48
    assert len({item["id"] for item in materials}) == 48
    assert len({item["title"] for item in materials}) == 48
    assert review["formats"] == {"txt": 40, "doc": 4, "wps": 3, "docx": 1}
    counts = review["deduplication"]
    assert counts["prior_high_overlap"] + counts["internal_duplicate_versions"] + counts["separately_reviewed"] == 48
    for item in materials:
        assert item["prior_coverage"] and item["retained"] and item["excluded"]
        for name, files in item["outputs"].items():
            destination = root.parent / name
            assert destination.is_dir() and files
            for reference in files:
                path = (destination / reference).resolve()
                assert path.is_relative_to(destination.resolve()) and path.is_file()


@pytest.mark.parametrize("reference,available,unavailable", [
    (skills.DEVELOPMENT, ("characters", "draft", "rewrite"), ("blueprint", "outline", "plan")),
    (skills.CONFLICT, ("characters",), ("blueprint", "outline", "plan", "draft", "rewrite")),
])
def test_new_references_are_required_only_for_related_tasks(independent_skill, reference, available, unavailable):
    (independent_skill / reference).unlink()
    for mode in available:
        assert skills.webnovel_skill_prompt(mode)
    for mode in unavailable:
        with pytest.raises(ProseSkillError, match="网文基础创作"):
            skills.webnovel_skill_prompt(mode)


@pytest.mark.parametrize("rewrite", [False, True])
def test_streamed_model_request_keeps_context_and_plain_text_output(monkeypatch, rewrite):
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
    context = {"chapter": {"chapter_number": 2, "content": BODY if rewrite else ""},
               "request": {"instruction": "第一人称，无金手指，保留原定失败结局"}}
    result = writing.generate(writing.DRAFT_REWRITE if rewrite else writing.DRAFT, context)
    assert result.content == BODY and len(requests) == 1
    assert requests[0]["stream"] is True and "tools" not in requests[0]
    prompt = requests[0]["messages"][-1]["content"]
    assert skills.webnovel_skill_prompt("rewrite" if rewrite else "draft") in prompt
    assert json.loads(prompt.split("资料和用户要求：", 1)[1]) == context
    assert '"reviewed_materials"' not in prompt


@pytest.mark.parametrize("role,mode", [
    (writing.OUTLINE, "outline"), (writing.OUTLINE_REWRITE, "outline"),
    (writing.CORE_CHARACTERS, "characters"), (writing.SUPPORTING_CHARACTERS, "characters"),
    (writing.CHARACTER_PROFILES, "characters"), (writing.PLAN, "plan"),
    (writing.DRAFT, "draft"), (writing.DRAFT_REWRITE, "rewrite"),
])
def test_writing_model_gets_only_the_references_for_its_task(monkeypatch, role, mode):
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))

    class Requested(Exception):
        pass

    model = Mock(side_effect=Requested)
    monkeypatch.setattr(writing, "_request_without_tools", model)
    context = {"chapter": {"id": 44, "chapter_number": 8},
               "request": {"instruction": "第一人称慢热群像，不加金手指，保留悲剧结局"}}
    with pytest.raises(Requested):
        writing.generate(role, context)
    prompt = model.call_args.kwargs["prompt"]
    assert skills.webnovel_skill_prompt(mode) in prompt
    for name in {name for files in skills.MODE_FILES.values() for name in files}:
        body = (skills.SKILL_ROOT / name).read_text(encoding="utf-8").strip()
        assert (body in prompt) is (name in skills.MODE_FILES[mode])
    assert json.dumps(context, ensure_ascii=False) in prompt
    assert '"reviewed_materials"' not in prompt


@pytest.mark.parametrize("role", [writing.REVIEW, writing.CONSISTENCY, writing.CONSISTENCY_REPAIR, writing.CHARACTER_PROGRESS])
def test_analysis_and_local_repairs_do_not_load_foundation_methods(monkeypatch, role):
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    loader = Mock(side_effect=AssertionError("Unrelated task must not load creative methods"))
    monkeypatch.setattr(writing, "webnovel_skill_prompt", loader)

    class Requested(Exception):
        pass

    monkeypatch.setattr(writing, "_request_without_tools", Mock(side_effect=Requested))
    with pytest.raises(Requested):
        writing.generate(role, {})
    loader.assert_not_called()


@pytest.mark.parametrize("fallback", [False, True])
def test_native_drafting_and_gateway_fallback_preserve_foundations(monkeypatch, fallback):
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=True))
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
    assert writing.generate(writing.DRAFT, {"chapter": {"chapter_number": 8}}).content == BODY
    native_prompt = task.call_args.kwargs["description"]
    assert skills.webnovel_skill_prompt("draft") in native_prompt
    assert direct.call_count == int(fallback)
    if fallback:
        assert native_prompt.endswith(direct.call_args.kwargs["prompt"])


@pytest.mark.parametrize("native,fallback", [(False, False), (True, False), (True, True)])
def test_blueprint_paths_get_title_character_and_plot_methods(monkeypatch, native, fallback):
    from test_worldbuilding_generation import BLUEPRINT, VALUES

    monkeypatch.setattr(world, "get_settings", lambda: Settings(
        _env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=native))
    monkeypatch.setattr(world, "has_generation_listener", lambda: False)
    raw = json.dumps(BLUEPRINT)
    direct = Mock(return_value=raw)
    monkeypatch.setattr(world.WorldbuildingAssistant, "_request_without_tools", direct)
    task = Mock(return_value=object())
    crew = Mock()
    if fallback:
        crew.kickoff.side_effect = InternalServerError("unavailable", response=httpx.Response(
            500, request=httpx.Request("POST", "https://model.test/v1/chat/completions")), body=None)
    else:
        crew.kickoff.return_value = raw
    monkeypatch.setitem(sys.modules, "crewai", SimpleNamespace(
        Crew=Mock(return_value=crew), LLM=Mock(), Process=SimpleNamespace(sequential="sequential"), Task=task))
    monkeypatch.setattr(world.WorldbuildingAssistant, "create_generation_agent", lambda llm: object())
    blueprint = world.WorldbuildingAssistant._generate_with_crewai(VALUES)
    assert blueprint.project.title == BLUEPRINT["project"]["title"]
    prompt = task.call_args.kwargs["description"] if native else direct.call_args.kwargs["prompt"]
    assert skills.webnovel_skill_prompt("blueprint") in prompt
    assert '"reviewed_materials"' not in prompt
    assert direct.call_count == int(not native or fallback)
    if fallback:
        assert prompt.endswith(direct.call_args.kwargs["prompt"])


def test_missing_unrelated_resources_do_not_break_other_modes(independent_skill):
    (independent_skill / "references/title-and-introduction.md").unlink()
    (independent_skill / "references/feedback-and-revision.md").unlink()
    assert skills.webnovel_skill_prompt("characters")
    assert skills.webnovel_skill_prompt("draft")
    with pytest.raises(ProseSkillError, match="网文基础创作"):
        skills.webnovel_skill_prompt("blueprint")
    with pytest.raises(ProseSkillError, match="网文基础创作"):
        skills.webnovel_skill_prompt("rewrite")


@pytest.mark.parametrize("failure", ["missing-entry", "empty-reference", "wrong-name"])
def test_invalid_skill_is_reported_before_any_model_call(independent_skill, monkeypatch, failure):
    if failure == "missing-entry":
        (independent_skill / "SKILL.md").unlink()
    elif failure == "empty-reference":
        (independent_skill / skills.CHARACTERS).write_text("\n", encoding="utf-8")
    else:
        (independent_skill / "SKILL.md").write_text("---\nname: other\n---\nBody", encoding="utf-8")
    monkeypatch.setattr(writing, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    monkeypatch.setattr(world, "get_settings", lambda: Settings(_env_file=None, API_KEY="test", MODEL_NATIVE_TOOLS=False))
    model = Mock(side_effect=AssertionError("Must not request a model"))
    monkeypatch.setattr(writing, "_request_without_tools", model)
    monkeypatch.setattr(world.WorldbuildingAssistant, "_request_without_tools", model)
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.generate(writing.DRAFT, {}, DraftRequest()))
    assert error.value.status_code == 503 and "网文基础创作" in error.value.detail
    with pytest.raises(world.WorldbuildingGenerationError) as error:
        world.WorldbuildingAssistant._generate_with_crewai({"genre": "科幻"})
    assert error.value.status == 503 and "网文基础创作" in str(error.value)
    model.assert_not_called()
