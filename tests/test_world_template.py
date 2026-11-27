from test_character_generation import novel  # noqa: F401 - pytest fixture


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
