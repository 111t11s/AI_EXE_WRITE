import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from agents import worldbuilding_agent, writing_agents
from agents.llm_streaming import GENERATION_EVENTS, collect_chat_text


TEXT = '{"summary":"灵潮衰退后的修仙世界"}'


def sse_response(request):
    payload = json.loads(request.content)
    assert payload["stream"] is True
    chunks = [{"delta": {"role": "assistant"}}, {"delta": {"reasoning_content": "private reasoning"}}]
    chunks += [{"delta": {"content": TEXT[start:start + 3]}} for start in range(0, len(TEXT), 3)]
    chunks.append({"delta": {}, "finish_reason": "stop"})
    frames = []
    for chunk in chunks:
        frame = {"id": "test-stream", "object": "chat.completion.chunk", "created": 0,
                 "model": "gpt-test", "choices": [{"index": 0, **chunk}]}
        frames.append("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n")
    frames.append('data: {"choices":[],"usage":{"completion_tokens":100}}\n\n')
    frames.append("data: [DONE]\n\n")
    return httpx.Response(200, content="".join(frames).encode(), headers={"content-type": "text/event-stream"})


@pytest.mark.parametrize("module", [worldbuilding_agent, writing_agents])
def test_fallback_consumes_sse_and_ignores_reasoning_and_usage(monkeypatch, module):
    clients = []

    def factory(**kwargs):
        client = OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(sse_response)))
        clients.append(client)
        return client

    monkeypatch.setattr(module, "OpenAI", factory)
    kwargs = {"model": "gpt-test", "api_key": "test-key", "base_url": "https://model.test/v1",
              "timeout": 5, "prompt": "Generate a world"}
    if module is writing_agents:
        kwargs["role"] = writing_agents.OUTLINE
    request = (worldbuilding_agent.WorldbuildingAssistant._request_without_tools
               if module is worldbuilding_agent else writing_agents._request_without_tools)
    assert request(**kwargs) == TEXT
    assert len(clients) == 1 and clients[0].is_closed()


def test_crewai_native_provider_consumes_sse(monkeypatch):
    from crewai import LLM
    from crewai.llms.providers.openai.completion import OpenAICompletion

    with OpenAI(api_key="test-key", base_url="https://model.test/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(sse_response))) as client:
        monkeypatch.setattr(OpenAICompletion, "_get_sync_client", lambda self: client)
        llm = LLM(model="gpt-test", api_key="test-key", base_url="https://model.test/v1", stream=True)
        assert llm.call("Generate a world") == TEXT


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
@pytest.mark.parametrize("as_object", [False, True])
def test_reasoning_is_forwarded_separately_from_json_content(field, as_object):
    events = []
    token = GENERATION_EVENTS.set(lambda name, data: events.append((name, data)))
    try:
        chunks = [
            {"choices": [{"delta": {field: "先检查设定"}}]},
            {"choices": [{"delta": {"content": '{"summary":"实时输出"}'}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"completion_tokens": 8}},
        ]
        if as_object:
            chunks = [json.loads(json.dumps(chunk), object_hook=lambda data: SimpleNamespace(**data))
                      for chunk in chunks]
        assert collect_chat_text(iter(chunks)) == '{"summary":"实时输出"}'
    finally:
        GENERATION_EVENTS.reset(token)
    assert events[0] == ("thinking", {"text": "先检查设定"})
    assert events[1] == ("delta", {"text": '{"summary":"实时输出"}'})
    assert [name for name, _ in events] == ["thinking", "delta", "status"]
