import json
from unittest.mock import patch

import httpx
import pytest
from openai import APIStatusError, OpenAI
from pydantic import ValidationError

from agents import writing_agents as agent
from api.writing_schemas import OutlineRequest
from core.config import Settings


@pytest.mark.parametrize("native_tools", [False, True])
def test_writing_stream_preserves_mcp_context_and_falls_back_once(monkeypatch, native_tools):
    from openai import InternalServerError

    settings = Settings(_env_file=None, API_KEY="test-key", PROXY_URL="https://model.test/v1",
                        MODEL_NATIVE_TOOLS=native_tools)
    monkeypatch.setattr(agent, "get_settings", lambda: settings)
    requests = []
    expected = {"verdict": "ready", "summary": "世界规则和角色动机一致", "issues": []}

    def respond(request):
        requests.append(json.loads(request.content))
        text = json.dumps(expected, ensure_ascii=False)
        chunks = [{"choices": [{"delta": {"content": text[:20]}}]},
                  {"choices": [{"delta": {"content": text[20:]}, "finish_reason": "stop"}]}]
        frames = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, content=frames.encode(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: OpenAI(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    response = httpx.Response(500, request=httpx.Request("POST", "https://model.test/v1/chat/completions"))
    with patch("crewai.LLM"), patch("crewai.Crew") as crew, patch.object(agent, "create_agent", return_value=None) as create:
        crew.return_value.kickoff.side_effect = InternalServerError("upstream error", response=response, body=None)
        result = agent.generate(agent.REVIEW, {"world": {"id": 2, "rules": "施法消耗灵气"}})
    assert result.model_dump() == expected
    assert create.call_count == int(native_tools)
    assert crew.return_value.kickoff.call_count == int(native_tools)
    assert len(requests) == 1 and requests[0]["stream"] is True and "tools" not in requests[0]
    assert "施法消耗灵气" in requests[0]["messages"][-1]["content"]


def test_context_mode_does_not_retry_rejected_credentials(monkeypatch):
    settings = Settings(_env_file=None, API_KEY="test-key", PROXY_URL="https://model.test/v1")
    assert settings.MODEL_NATIVE_TOOLS is False
    monkeypatch.setattr(agent, "get_settings", lambda: settings)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "Invalid key"}})

    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: OpenAI(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    with pytest.raises(APIStatusError) as error:
        agent.generate(agent.REVIEW, {})
    assert error.value.status_code == 401 and len(requests) == 1


@pytest.mark.parametrize("theme, label", [
    ("development", "发育（成长推进）"), ("comeback", "逆袭"),
    ("ending", "结尾收束"), ("survival", "逃杀（生存竞逐）"),
])
def test_outline_prompt_contains_selected_theme(monkeypatch, theme, label):
    output = {"summary": "主线", "central_conflict": "冲突", "themes": "主题", "volumes": [{
        "title": "第一卷", "summary": "摘要", "protagonist_goal": "目标", "main_conflict": "冲突",
        "climax": "高潮", "ending_hook": "后果", "beats": [{"title": "行动", "summary": "因果"}]}]}
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        body = json.dumps(output, ensure_ascii=False)
        frames = [{"choices": [{"delta": {"content": body}, "finish_reason": "stop"}]}]
        wire = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, content=wire.encode(), headers={"content-type": "text/event-stream"})

    settings = Settings(_env_file=None, API_KEY="test-key", PROXY_URL="https://model.test/v1")
    monkeypatch.setattr(agent, "get_settings", lambda: settings)
    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: OpenAI(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    agent.generate(agent.OUTLINE, {"project": {"genre": "玄幻"},
                                   "request": {"volume_count": 1, "outline_theme": theme}})
    assert f"本次大纲主题：{label}" in requests[0]["messages"][-1]["content"]


def test_outline_request_defaults_theme_and_validates_custom_description():
    assert OutlineRequest().outline_theme == "development"
    assert OutlineRequest(instruction="说明一个融合主题", outline_theme="custom").outline_theme == "custom"
    with pytest.raises(ValidationError, match="自定义主题"):
        OutlineRequest(outline_theme="custom")
    with pytest.raises(ValidationError):
        OutlineRequest(outline_theme="unknown")


BODY = "夜里第三次换过灵石时，沈砚舟终于安静下来。\n\n" + "她收起长剑，守着窗边最后一盏灵灯。" * 12


@pytest.mark.parametrize("role", [agent.DRAFT, agent.DRAFT_REWRITE])
@pytest.mark.parametrize("format", ["plain", "json", "fenced-json", "fenced-text"])
def test_body_accepts_prose_and_legacy_json_without_changing_paragraphs(role, format):
    raw = BODY
    if format in {"json", "fenced-json"}:
        raw = json.dumps({"content": BODY, "character_updates": []}, ensure_ascii=False)
    if format.startswith("fenced-"):
        language = "json" if format == "fenced-json" else "text"
        raw = f"```{language}\n{raw}\n```"
    assert agent._parse_output(role, raw).content == BODY


@pytest.mark.parametrize("raw", ["", "正文过短", "正文" * 15001,
                                '{"content": "' + BODY, '{"content": 123}',
                                json.dumps({"summary": BODY}), "```json\n" + BODY + "\n```",
                                "```text\n" + BODY],
                         ids=["empty", "short", "long", "broken-json", "wrong-type", "missing-content",
                              "invalid-fenced-json", "open-fence"])
def test_body_rejects_invalid_lengths_and_broken_structured_output(raw):
    with pytest.raises(ValidationError):
        agent._parse_output(agent.DRAFT, raw)


@pytest.mark.parametrize("role", [agent.REVIEW, agent.CHARACTER_PROGRESS, agent.OUTLINE, agent.PLAN])
def test_structured_roles_still_reject_plain_text(role):
    with pytest.raises(ValidationError):
        agent._parse_output(role, BODY)
