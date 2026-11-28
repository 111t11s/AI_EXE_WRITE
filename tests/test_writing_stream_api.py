import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from agents import writing_agents as agent
from api import novel_writing_api as api
from api.account_api import Account, get_current_account
from api.novel_crud_api import get_database
from core.config import Settings
from main import app


VOLUME = {"title": "寻脉", "summary": "寻找失落灵脉", "protagonist_goal": "修复灵脉",
          "main_conflict": "宗门阻挠", "climax": "开启阵眼", "ending_hook": "古印苏醒",
          "beats": [{"title": "出发", "summary": "少年循水迹出发"}]}
CASES = [
    ("/projects/2/outline", {"volume_count": 1},
     {"summary": "寻找灵脉", "central_conflict": "灵气枯竭", "themes": "守护", "volumes": [VOLUME]},
     "save_outline", {"project_id": 2, "outline_id": 3, "version_no": 1, "volume_ids": [4], "plot_beat_count": 1}),
    ("/volumes/4/chapter-plan", {"chapter_count": 1},
     {"chapters": [{"title": "启程", "summary": "少年离家寻找灵脉"}]},
     "save_chapter_plan", {"project_id": 2, "volume_id": 4, "chapters": [{"chapter_id": 5, "chapter_number": 1, "title": "启程"}]}),
    ("/chapters/5/draft", {}, {"content": "少年循着灵脉，踏上了远行的路。" * 20, "character_updates": []},
     "save_draft", {"chapter_id": 5, "status": "draft", "word_count": 300}),
    ("/chapters/5/review", {}, {"verdict": "ready", "summary": "人物与设定一致", "issues": []},
     None, {"verdict": "ready", "summary": "人物与设定一致", "issues": []}),
    ("/outlines/3/rewrite", {"volume_count": 1, "instruction": "加强冲突"},
     {"summary": "寻找灵脉", "central_conflict": "灵气枯竭", "themes": "守护", "volumes": [VOLUME]},
     "save_outline", {"project_id": 2, "outline_id": 6, "version_no": 2, "volume_ids": [7], "plot_beat_count": 1}),
    ("/chapters/5/rewrite", {"instruction": "加强对话"}, {"content": "少年循着灵脉，踏上了远行的路。" * 20, "character_updates": []},
     "save_rewritten_draft", {"chapter_id": 5, "status": "draft", "word_count": 300}),
    ("/chapters/5/polish", {"instruction": "保留文风"}, {"content": "少年循着灵脉，踏上了远行的路。" * 20},
     "save_rewritten_draft", {"chapter_id": 5, "status": "draft", "word_count": 300}),
    ("/chapters/5/continue", {"instruction": "继续对话"}, {"content": "少年循着灵脉，踏上了远行的路。" * 20},
     "save_continued_draft", {"chapter_id": 5, "status": "draft", "word_count": 300}),
]
CASES += [(path, payload, artifact["content"], save_name, receipt)
          for path, payload, artifact, save_name, receipt in list(CASES)
          if path.endswith(("/draft", "/rewrite")) and "content" in artifact]


@pytest.mark.parametrize("path,payload,artifact,save_name,receipt", CASES)
def test_all_writing_endpoints_stream_then_validate_and_save(monkeypatch, path, payload, artifact, save_name, receipt):
    settings = Settings(_env_file=None, API_KEY="test-key", PROXY_URL="https://model.test/v1", MODEL_NATIVE_TOOLS=True)
    monkeypatch.setattr(agent, "get_settings", lambda: settings)
    monkeypatch.setitem(app.dependency_overrides, get_database, lambda: object())
    monkeypatch.setitem(app.dependency_overrides, get_current_account,
                        lambda: Account(id=1, username="writer", display_name="作者"))
    monkeypatch.setattr(api, "require_resource", Mock())
    monkeypatch.setattr(api, "task_project", Mock(return_value=2))
    monkeypatch.setattr(api, "next_volume_number", Mock(return_value=1))
    monkeypatch.setattr(api, "outline_volume_number", Mock(return_value=1))
    monkeypatch.setattr(api.foreshadow_db, "list_for_volume", Mock(return_value=[]))
    context = {
        "project": {"id": 2}, "world": {"rules": "施法消耗灵气"}, "chapter": {"id": 5}, "characters": [{"name": "少年"}],
    }
    async def load(loader, *args):
        return (context, []) if loader is api.character_context else context
    monkeypatch.setattr(api, "load_context", AsyncMock(side_effect=load))
    save = Mock(return_value=receipt)
    if save_name:
        monkeypatch.setattr(api, save_name, save)
    monkeypatch.setattr(agent, "create_agent", Mock(side_effect=AssertionError("Browser stream must use direct SSE")))
    requests = []

    class ModelStream(httpx.SyncByteStream):
        def __iter__(self):
            text = artifact if isinstance(artifact, str) else json.dumps(artifact, ensure_ascii=False)
            for start in range(0, len(text), 11):
                save.assert_not_called()
                frame = {"choices": [{"delta": {"content": text[start:start + 11]}}]}
                yield ("data: " + json.dumps(frame) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=ModelStream(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: OpenAI(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    with TestClient(app) as client:
        response = client.post("/api/v1/writing" + path + "/stream", json=payload)
    assert response.status_code == 200 and response.headers["x-accel-buffering"] == "no"
    assert "event: delta" in response.text and "event: error" not in response.text
    final = response.text.strip().split("\n\n")[-1]
    assert json.loads(final.split("data: ", 1)[1]) == receipt
    assert len(requests) == 1 and requests[0]["stream"] is True and "tools" not in requests[0]
    assert save.call_count == int(save_name is not None)
