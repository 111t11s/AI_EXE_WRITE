from test_character_generation import novel  # noqa: F401 - pytest fixture

from agents.worldbuilding_agent import WorldbuildingAssistant


def test_world_template_text_is_split_into_fields(novel, monkeypatch):
    project, client, headers = novel["project"], novel["client"], novel["headers"]["writer"]
    fields = {
        "core_concept": "潮汐城市", "special_feature": "街道随潮水移动",
        "genre": "奇幻", "tone": "希望", "era": "洪水之后",
        "main_regions": "沉没的旧城", "civilization_level": "低科技",
        "allowed": "低潮读取旧图", "forbidden": "满潮使用旧图",
        "power_core": "潮汐魔法", "use_conditions": "站在潮线内",
        "costs": "消耗记忆", "failure_consequences": "被困街道",
        "countermeasures": "锚点固定",
    }
    monkeypatch.setattr(WorldbuildingAssistant, "parse_world_template",
                        staticmethod(lambda text, model=None: fields))
    response = client.post("/api/v1/novel-assistant/world-template/parse",
                           headers=headers, json={"text": "这是一个潮汐城市，街道会随潮水移动。"})
    assert response.status_code == 200, response.text
    assert response.json()["template"]["core_concept"] == "潮汐城市"
    assert response.json()["template"]["countermeasures"] == "锚点固定"


def test_world_template_is_persisted_and_returned(novel):
    project, client, headers = novel["project"], novel["client"], novel["headers"]["writer"]
    template = {
        "core_concept": "潮汐城市", "special_feature": "街道随潮水移动",
        "genre": "奇幻", "tone": "希望", "era": "洪水之后",
        "main_regions": "沉没的旧城", "civilization_level": "低科技",
        "allowed": "低潮读取旧图", "forbidden": "满潮使用旧图",
        "power_core": "潮汐魔法", "use_conditions": "站在潮线内",
        "costs": "消耗记忆", "failure_consequences": "被困街道",
        "countermeasures": "锚点固定",
    }
    response = client.post("/api/v1/worlds", headers=headers, json={
        "project_id": project, "version_no": 2, "title": "潮汐世界",
        "summary": "一个潮汐城市", "era": "洪水之后", "rules": "低潮规则",
        "cultures": "潮汐文化", "template": template, "is_current": False,
    })
    assert response.status_code == 201, response.text
    world_id = response.json()["id"]
    row = client.get(f"/api/v1/worlds/{world_id}", headers=headers)
    assert row.status_code == 200, row.text
    value = row.json()["template"]
    if isinstance(value, str):
        import json
        value = json.loads(value)
    assert value["core_concept"] == "潮汐城市"
    assert value["countermeasures"] == "锚点固定"

def _template():
    return {
        "core_concept": "潮汐城市", "special_feature": "街道随潮水移动",
        "genre": "奇幻", "tone": "希望", "era": "洪水之后",
        "main_regions": "沉没的旧城", "civilization_level": "低科技",
        "allowed": "低潮读取旧图", "forbidden": "满潮使用旧图",
        "power_core": "潮汐魔法", "use_conditions": "站在潮线内",
        "costs": "消耗记忆", "failure_consequences": "被困街道",
        "countermeasures": "锚点固定",
    }


def test_start_novel_from_template_creates_job(novel, monkeypatch):
    client, headers = novel["client"], novel["headers"]["writer"]
    calls = []

    async def fake_confirm(self, state, theme, model, world_template, title):
        calls.append((state.values, world_template, title))
        return {}

    monkeypatch.setattr(WorldbuildingAssistant, "confirm", fake_confirm)
    response = client.post("/api/v1/novel-assistant/from-template/tasks",
                           headers=headers, json={"world_template": _template(), "title": "潮汐旧港"})
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "worldbuilding"
    import time
    time.sleep(0.2)
    assert calls and calls[0][1]["core_concept"] == "潮汐城市"
    assert calls[0][0]["genre"] == "奇幻" and calls[0][0]["length"] == "长篇"
    assert calls[0][2] == "潮汐旧港"


def test_start_novel_from_template_rejects_incomplete_template(novel):
    client, headers = novel["client"], novel["headers"]["writer"]
    response = client.post("/api/v1/novel-assistant/from-template/tasks",
                           headers=headers, json={"world_template": {"core_concept": "只有一项"}})
    assert response.status_code == 422

def test_world_template_parse_runs_as_task(novel, monkeypatch):
    client, headers = novel["client"], novel["headers"]["writer"]
    monkeypatch.setattr(WorldbuildingAssistant, "parse_world_template",
                        staticmethod(lambda text, model=None: _template()))
    response = client.post("/api/v1/novel-assistant/world-template/parse/tasks",
                           headers=headers, json={"text": "一段世界观文本"})
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["kind"] == "world_template"
    import time
    detail = None
    for _ in range(30):
        detail = client.get(f"/api/v1/generation-jobs/{job['id']}", headers=headers).json()
        if detail["status"] == "completed":
            break
        time.sleep(0.1)
    assert detail["status"] == "completed", detail
    assert detail["result"]["template"]["core_concept"] == "潮汐城市"

