import json
from unittest.mock import patch

import httpx
import pytest
from openai import APIStatusError, OpenAI

from agents import writing_agents as agent
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
