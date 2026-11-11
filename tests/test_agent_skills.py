import json

import pytest

from agent.skill import CreativeRequest, build_creative_skills
from agent.skill.models import MemoryFact, StageSnapshot
from agent.skill.storage import StoryMemorySkill, TaskControlSkill


class FakeProvider:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return json.dumps(self.output)


class FakeDatabase:
    def __init__(self):
        self.rows = {
            "novel_book": {1: {"id": 1, "current_phase": "IDLE"}},
            "novel_generation_task": {},
            "novel_generation_log": {},
            "novel_story_memory": {},
        }
        self.next_id = 1

    def select_by_id(self, table, record_id):
        return self.rows[table].get(record_id)

    def insert(self, table, data):
        record_id = self.next_id
        self.next_id += 1
        defaults = {"status": "PENDING", "retry_count": 0, "max_retries": 3}
        self.rows[table][record_id] = {"id": record_id, **defaults, **data}
        return record_id

    def update_by_id(self, table, record_id, data):
        self.rows[table][record_id].update(data)
        return 1


def test_registry_has_creative_and_editorial_skills():
    provider = FakeProvider({"title": "First", "genre": "fantasy", "premise": "A quest",
                             "audience": "adult", "writing_style": "concise"})
    skills = build_creative_skills(provider)

    assert len(skills) == 13
    brief = skills["book_brief"].run(CreativeRequest(instruction="Write a fantasy novel"))
    assert brief.title == "First"
    assert "JSON object" in provider.calls[0][0]
    assert json.loads(provider.calls[0][1])["instruction"] == "Write a fantasy novel"


def test_structured_skill_rejects_invalid_model_output():
    skill = build_creative_skills(FakeProvider({"title": "Only title"}))["book_brief"]
    with pytest.raises(ValueError, match="invalid structured output"):
        skill.run(CreativeRequest(instruction="Write a novel"))


@pytest.mark.parametrize("name,output", [
    ("world_building", {"summary": "A city", "rules": ["Magic has a cost"]}),
    ("faction_design", {"factions": [{"name": "Guild", "goal": "Control trade"}]}),
    ("character_design", {"characters": [{"name": "Lin", "role": "hero",
                                               "goal": "Find home", "conflict": "Exile"}]}),
    ("outline_planning", {"throughline": "Return home", "beats": [
        {"chapter_number": 1, "title": "Departure", "objective": "Leave",
         "consequence": "Pursued"}]}),
    ("chapter_planning", {"chapter_number": 1, "goal": "Escape",
                           "scenes": ["Flight"], "ending_hook": "A stranger appears"}),
    ("chapter_writing", {"title": "Flight", "content": "The chase began."}),
    ("memory_extraction", {"facts": [{"chapter_number": 1,
                                        "content": "Lin left the city"}]}),
    ("stage_summary", {"up_to_chapter": 1, "content": "Lin is in exile"}),
    ("continuity_review", {"passed": True, "summary": "Consistent"}),
    ("content_review", {"passed": True, "summary": "Clear pacing"}),
    ("chapter_revision", {"title": "Flight", "content": "The chase resumed."}),
    ("chapter_polishing", {"title": "Flight", "content": "The pursuit began."}),
])
def test_each_stage_validates_its_output(name, output):
    skills = build_creative_skills(FakeProvider(output))
    result = skills[name].run(CreativeRequest(instruction="Continue the story"))
    assert result == skills[name].output_model.model_validate(output)


def test_story_memory_and_task_controls():
    db = FakeDatabase()
    memory = StoryMemorySkill(db)
    fact = memory.save_fact(1, MemoryFact(chapter_number=2, content="Found a sword",
                                          related_character_ids=[7]))
    assert json.loads(fact["related_character_ids"]) == [7]
    snapshot = memory.save_snapshot(1, StageSnapshot(up_to_chapter=2, content="A new quest",
                                                      unresolved_threads=["Who stole the sword?"]))
    assert snapshot["memory_type"] == "SNAPSHOT"

    tasks = TaskControlSkill(db)
    task = tasks.create_task(1, "CHAPTER", {"chapter_number": 2})
    assert json.loads(task["params_json"])["chapter_number"] == 2
    running = tasks.transition(task["id"], "RUNNING")
    assert running["status"] == "RUNNING"
    assert tasks.heartbeat(task["id"])["heartbeat_at"]
    failed = tasks.transition(task["id"], "FAILED", error_message="timeout")
    assert failed["retry_count"] == 1
    assert tasks.transition(task["id"], "PENDING")["status"] == "PENDING"
    assert tasks.advance_phase(1, "WRITING")["current_phase"] == "WRITING"
    assert tasks.log_generation(1, "writer", "GENERATE", task_id=task["id"])["agent_name"] == "writer"
