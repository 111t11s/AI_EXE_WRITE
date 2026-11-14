import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import InternalServerError, OpenAI

from agents import worldbuilding_agent as agent
from api import novel_assistant_api as api
from api.account_api import Account, get_current_account
from main import app


VALUES = dict(genre="Fantasy", setting="A drowned city", power_system="Tidal magic", cheat="A map",
              protagonist="A cartographer", goal="Find the lost shore", tone="Hopeful", length="One volume")
BLUEPRINT = {
    "project": {"title": "The Tidal Atlas", "genre": VALUES["genre"], "setting": VALUES["setting"],
                "tone": VALUES["tone"], "target_length": VALUES["length"], "protagonist_goal": VALUES["goal"]},
    "world": {"summary": "A city whose streets move with the tide", "era": "After the flood",
              "history": "The flood buried every landmark", "rules": "Maps work only at low tide"},
    "power_system": {"name": "Tidal magic", "source": "The tides"},
    "cheat": {"name": "Living atlas", "cheat_type": "Map"},
    "outline": {"summary": "A cartographer maps the drowned roads", "central_conflict": "The fading shore",
                "volumes": [{"volume_number": 1, "title": "Low Tide", "summary": "Find the first landmark"}]},
}


@pytest.fixture
def generation(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_current_account,
                        lambda: Account(id=1, username="writer", display_name="作者"))
    monkeypatch.setattr(api, "_sessions", {"draft": agent.ConversationState(account_id=1, values=VALUES.copy())})
    monkeypatch.setenv("MODEL_NAME", "test-model")
    monkeypatch.setattr(agent, "get_settings", lambda: SimpleNamespace(
        API_KEY="test-key", PROXY_URL="https://model.test/v1", REQUEST_TIMEOUT=5,
        MODEL_NATIVE_TOOLS=True,
    ))
    monkeypatch.setattr(agent.WorldbuildingAssistant, "create_generation_agent", lambda llm: None)

    @asynccontextmanager
    async def worldbuilding_session():
        yield None

    writes = AsyncMock(side_effect=[{"id": 10}, {"id": 20}])
    monkeypatch.setattr(agent, "worldbuilding_session", worldbuilding_session)
    monkeypatch.setattr(agent, "call_world_tool", writes)

    def mock_gateway(responses):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            status, body = next(responses)
            if isinstance(body, httpx.SyncByteStream):
                return httpx.Response(status, stream=body, headers={"content-type": "text/event-stream"})
            return httpx.Response(status, json=body)

        monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ))
        return requests

    yield writes, mock_gateway


def completion(blueprint):
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(blueprint)}}]}


def crew_error():
    response = httpx.Response(500, request=httpx.Request("POST", "https://model.test/v1/chat/completions"))
    return InternalServerError("upstream error", response=response, body=None)


def test_context_mode_generates_once_and_still_persists_via_mcp(generation, monkeypatch):
    writes, mock_gateway = generation
    monkeypatch.setattr(agent, "get_settings", lambda: SimpleNamespace(
        API_KEY="test-key", PROXY_URL="https://model.test/v1", REQUEST_TIMEOUT=5,
        MODEL_NATIVE_TOOLS=False,
    ))
    requests = mock_gateway(iter([(200, EventStream(BLUEPRINT))]))
    with TestClient(app) as client, patch.object(agent.WorldbuildingAssistant, "create_generation_agent") as create:
        result = confirm(client)
        repeated = confirm(client)
    create.assert_not_called()
    assert result.status_code == 200 and repeated.json() == result.json()
    assert result.json()["persistence"] == {"project_id": 10, "world_id": 20, "error": None}
    assert len(requests) == 1 and requests[0]["stream"] is True and "tools" not in requests[0]
    assert VALUES["setting"] in requests[0]["messages"][-1]["content"]
    assert writes.await_count == 2


def confirm(client):
    return client.post("/api/v1/novel-assistant/sessions/draft/messages", json={"content": "确认生成"})


@pytest.mark.parametrize("failure,status", [(None, None), (httpx.ReadTimeout, 504)])
def test_browser_stream_delivers_deltas_and_saves_only_valid_complete_output(generation, failure, status):
    writes, mock_gateway = generation
    requests = mock_gateway(iter([(200, EventStream(BLUEPRINT, failure=failure))]))
    with TestClient(app) as client:
        response = client.post("/api/v1/novel-assistant/sessions/draft/messages/stream",
                               json={"content": "确认生成"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [frame.split("\n", 1) for frame in response.text.strip().split("\n\n")]
    decoded = [(name.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
               for name, data in events]
    assert decoded[0][0] == "status" and any(name == "delta" for name, _ in decoded)
    assert len(requests) == 1 and "tools" not in requests[0]
    if failure:
        assert decoded[-1][0] == "error" and decoded[-1][1]["status"] == status
        assert not any(name == "result" for name, _ in decoded)
        writes.assert_not_awaited()
        assert api._sessions["draft"].confirmed is False
    else:
        assert decoded[-1][0] == "result" and decoded[-1][1]["persistence"]["world_id"] == 20
        assert "".join(data["text"] for name, data in decoded if name == "delta") == json.dumps(BLUEPRINT, ensure_ascii=False)
        assert writes.await_count == 2


def test_gateway_500_retries_without_tools_and_saves_once(generation):
    writes, mock_gateway = generation
    requests = mock_gateway(iter([(200, completion(BLUEPRINT))]))
    with TestClient(app) as client, patch("crewai.LLM"), patch("crewai.Crew") as crew:
        crew.return_value.kickoff.side_effect = crew_error()
        result = confirm(client)
        repeated = confirm(client)

    assert result.status_code == 200
    assert result.json()["persistence"] == {"project_id": 10, "world_id": 20, "error": None}
    assert result.json()["result"]["world"]["summary"] == BLUEPRINT["world"]["summary"]
    assert repeated.json() == result.json()
    assert len(requests) == 1 and requests[0]["model"] == "test-model" and "tools" not in requests[0]
    assert requests[0]["stream"] is True
    assert [entry.args[1] for entry in writes.await_args_list] == ["create_novel_project", "create_novel_world"]
    assert writes.await_args_list[-1].args[2]["payload"]["project_id"] == 10


@pytest.mark.parametrize(("first_response", "expected_detail"), [
    ((500, {"error": {"message": "private upstream text"}}), "模型服务暂时不可用"),
    ((200, completion({**BLUEPRINT, "world": {**BLUEPRINT["world"], "rules": ""}})), "世界观或大纲不完整"),
])
def test_failed_generation_preserves_answers_and_allows_retry(generation, first_response, expected_detail):
    writes, mock_gateway = generation
    mock_gateway(iter([first_response, (200, completion(BLUEPRINT))]))
    with TestClient(app) as client, patch("crewai.LLM"), patch("crewai.Crew") as crew:
        crew.return_value.kickoff.side_effect = [crew_error(), crew_error()]
        failed = confirm(client)
        assert failed.status_code == 502
        assert expected_detail in failed.json()["detail"]
        session = client.get("/api/v1/novel-assistant/sessions/draft").json()
        assert session["values"] == VALUES
        assert session["result"] is None and not session["done"]
        writes.assert_not_awaited()

        succeeded = confirm(client)
        assert succeeded.status_code == 200
        assert succeeded.json()["done"] and succeeded.json()["warning"] is None
        assert writes.await_count == 2


class EventStream(httpx.SyncByteStream):
    def __init__(self, blueprint, failure=None):
        self.text = json.dumps(blueprint, ensure_ascii=False)
        self.failure = failure
        self.closed = False

    def __iter__(self):
        for start in range(0, len(self.text), 31):
            chunk = {"choices": [{"index": 0, "delta": {"content": self.text[start:start + 31]}}]}
            yield ("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode()
            if self.failure:
                raise self.failure("upstream interrupted")
        yield b'data: {"choices":[],"usage":{"completion_tokens":100}}\n\n'
        yield b"data: [DONE]\n\n"

    def close(self):
        self.closed = True


@pytest.mark.parametrize("failure,status", [(None, 200), (httpx.ReadTimeout, 504), (httpx.ReadError, 502)])
@pytest.mark.parametrize("native_tools", [False, True])
def test_streamed_generation_saves_only_complete_output(generation, monkeypatch, failure, status, native_tools):
    writes, mock_gateway = generation
    monkeypatch.setattr(agent, "get_settings", lambda: SimpleNamespace(
        API_KEY="test-key", PROXY_URL="https://model.test/v1", REQUEST_TIMEOUT=5,
        MODEL_NATIVE_TOOLS=native_tools,
    ))
    stream = EventStream(BLUEPRINT, failure=failure)
    requests = mock_gateway(iter([(200, stream), (200, EventStream(BLUEPRINT))]))
    with TestClient(app) as client, patch("crewai.LLM") as llm, patch("crewai.Crew") as crew:
        crew.return_value.kickoff.side_effect = crew_error()
        response = confirm(client)
        assert response.status_code == status
        assert requests[0]["stream"] is True
        if native_tools:
            assert llm.call_args.kwargs["stream"] is True
        else:
            llm.assert_not_called()
        assert stream.closed
        if failure:
            writes.assert_not_awaited()
            snapshot = client.get("/api/v1/novel-assistant/sessions/draft").json()
            assert snapshot["values"] == VALUES and snapshot["result"] is None
            response = confirm(client)
            assert response.status_code == 200
        assert response.json()["result"]["world"]["summary"] == BLUEPRINT["world"]["summary"]
        assert writes.await_count == 2
