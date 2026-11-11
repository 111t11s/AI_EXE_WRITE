"""Request contracts for persisted rows, separate from generated blueprints."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


RowId = Annotated[int, Field(gt=0, le=2**64 - 1)]
Sequence = Annotated[int, Field(ge=1, le=2**32 - 1)]
Count = Annotated[int, Field(ge=0, le=2**32 - 1)]
Name = Annotated[str, Field(min_length=1, max_length=160)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
SessionId = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]


class RowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(RowInput):
    title: Title = "待命名小说"
    genre: str = Field(min_length=1, max_length=80)
    setting: str = Field(min_length=1, max_length=500)
    tone: str = Field(min_length=1, max_length=200)
    target_length: str = Field(min_length=1, max_length=120)
    premise: str | None = None
    protagonist_goal: str = Field(min_length=1, max_length=500)
    status: Literal["planning", "writing", "completed", "paused"] = "planning"


class DesignSessionCreate(RowInput):
    project_id: RowId | None = None
    status: Literal["collecting", "ready", "generating", "completed", "failed"] = "collecting"
    collected_data: dict[str, JsonValue] = Field(default_factory=dict)


class DesignMessageCreate(RowInput):
    session_id: SessionId
    sequence_no: Sequence
    speaker: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1)


class WorldCreate(RowInput):
    project_id: RowId
    version_no: Sequence = 1
    title: Title = "世界观设定"
    summary: str
    era: str = Field(max_length=500)
    history: str | None = None
    rules: str = ""
    cultures: str = ""
    is_current: bool = True


class RegionCreate(RowInput):
    world_id: RowId
    name: Name
    description: str
    climate: str = Field(default="", max_length=160)
    resources: str = ""
    locations: str = ""


class PowerSystemCreate(RowInput):
    world_id: RowId
    name: Name
    source: str
    rules: str = ""
    limitations: str = ""
    growth_rules: str = ""


class PowerRankCreate(RowInput):
    power_system_id: RowId
    rank_order: Sequence
    name: Name
    description: str = ""
    breakthrough_condition: str = ""
    capabilities: str = ""


class CheatSystemCreate(RowInput):
    world_id: RowId
    name: Name
    cheat_type: str = Field(min_length=1, max_length=120)
    origin: str = ""
    functions: str = ""
    limitations: str = ""
    costs: str = ""
    growth_path: str = ""
    story_secret: str = ""


class FactionCreate(RowInput):
    world_id: RowId
    name: Name
    faction_type: str = Field(default="", max_length=120)
    summary: str
    goal: str = ""
    leader: str = Field(default="", max_length=160)
    territory: str = Field(default="", max_length=300)
    strengths: str = ""
    weaknesses: str = ""
    relationships: str = ""


class CharacterCreate(RowInput):
    project_id: RowId
    faction_id: RowId | None = None
    name: Name
    role: str = Field(min_length=1, max_length=80)
    summary: str
    personality: str = ""
    background: str = ""
    goal: str = ""
    motivation: str = ""
    weakness: str = ""
    character_arc: str = ""
    abilities: str = ""
    first_appearance_chapter: Sequence | None = None
    status: Literal["active", "departed", "dead"] = "active"


class CharacterRelationshipCreate(RowInput):
    source_character_id: RowId
    target_character_id: RowId
    relationship_type: str = Field(min_length=1, max_length=100)
    description: str
    changed_at_chapter: Sequence | None = None


class OutlineCreate(RowInput):
    project_id: RowId
    version_no: Sequence = 1
    summary: str
    central_conflict: str
    themes: str = ""
    status: Literal["draft", "approved", "active", "archived"] = "draft"


class OutlineVolumeCreate(RowInput):
    outline_id: RowId
    volume_number: Sequence
    title: Title
    summary: str
    protagonist_goal: str = ""
    main_conflict: str = ""
    climax: str = ""
    ending_hook: str = ""


class PlotBeatCreate(RowInput):
    volume_id: RowId
    sort_order: Sequence
    title: Title
    summary: str
    beat_type: str = Field(default="plot", min_length=1, max_length=80)
    foreshadowing: str = ""


class ChapterCreate(RowInput):
    project_id: RowId
    volume_id: RowId | None = None
    chapter_number: Sequence
    title: str = Field(default="", max_length=200)
    summary: str = ""
    content: str = ""
    status: Literal["planned", "draft", "reviewed", "published"] = "planned"
    word_count: Count = 0
