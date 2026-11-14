import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import novel_assistant_api as api
from api.account_api import Account, get_current_account


SLOTS = ["genre", "setting", "power_system", "cheat", "protagonist", "goal", "tone", "length"]
CUSTOM_ANSWERS = ["蒸汽朋克探险", "浮空群岛", "燃烧记忆驱动机械", "会说话的怀表", "失忆钟表匠",
                  "找回被偷走的昨天", "荒诞而温暖", "12万字，三卷"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "_sessions", {})
    app = FastAPI()
    app.dependency_overrides[get_current_account] = lambda: Account(id=1, username="writer", display_name="作者")
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("custom", [False, True], ids=["common-options", "custom-answers"])
def test_questionnaire_collects_choices_or_custom_answers(client, custom):
    response = client.post("/novel-assistant/sessions")
    assert response.status_code == 200
    reply = response.json()
    path = f"/novel-assistant/sessions/{reply['session_id']}"
    assert {"玄幻", "都市", "仙侠"} <= set(reply["question"]["options"])
    answers = {}

    for index, slot in enumerate(SLOTS):
        question = reply["question"]
        assert question["key"] == slot
        assert question["allow_custom"] is True
        assert question["options"] and all(isinstance(value, str) and value for value in question["options"])
        assert client.get(path).json()["question"] == question
        answer = CUSTOM_ANSWERS[index] if custom else question["options"][0]
        response = client.post(f"{path}/messages", json={"content": answer})
        assert response.status_code == 200
        reply = response.json()
        answers[slot] = answer
        assert reply["values"] == answers
        assert reply["done"] is False

    assert reply["question"] is None
    assert "确认生成" in reply["message"]
    snapshot = client.get(path).json()
    assert snapshot["question"] is None and snapshot["result"] is None
    assert snapshot["values"] == answers
    assert len(snapshot["history"]) == 17

    corrected = client.post(f"{path}/messages", json={"content": "小说类型：仙侠"}).json()
    assert corrected["values"] == {**answers, "genre": "仙侠"}
    assert corrected["question"] is None
    assert "仙侠" in corrected["message"]


def test_corrections_and_invalid_answers_keep_the_current_question(client):
    reply = client.post("/novel-assistant/sessions").json()
    path = f"/novel-assistant/sessions/{reply['session_id']}"
    client.post(f"{path}/messages", json={"content": "玄幻"})

    corrected = client.post(f"{path}/messages", json={"content": "小说类型：都市"}).json()
    assert corrected["values"] == {"genre": "都市"}
    assert corrected["question"]["key"] == "setting"
    for content, status in [("", 422), ("   ", 200), ("确认生成", 400)]:
        response = client.post(f"{path}/messages", json={"content": content})
        assert response.status_code == status
        snapshot = client.get(path).json()
        assert snapshot["values"] == {"genre": "都市"}
        assert snapshot["question"] == corrected["question"]
