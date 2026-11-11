"""Canonical, validated data structures for a novel project blueprint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NovelModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NovelProject(NovelModel):
    title: str = Field(default="待命名小说", min_length=1, max_length=200)
    genre: str = Field(min_length=1, max_length=80)
    setting: str = Field(min_length=1, max_length=500)
    tone: str = Field(min_length=1, max_length=200)
    target_length: str = Field(min_length=1, max_length=120)
    premise: str = ""
    protagonist_goal: str = Field(min_length=1, max_length=500)
    status: Literal["planning", "writing", "completed", "paused"] = "planning"


class Region(NovelModel):
    name: str
    description: str
    climate: str = ""
    resources: str = ""
    locations: str = ""


class WorldSetting(NovelModel):
    title: str = Field(default="世界观设定", min_length=1, max_length=200)
    summary: str
    era: str = Field(max_length=500)
    history: str = ""
    rules: str = ""
    regions: list[Region] = Field(default_factory=list)
    cultures: str = ""


class PowerRank(NovelModel):
    order: int = Field(ge=1)
    name: str
    description: str = ""
    breakthrough_condition: str = ""
    capabilities: str = ""


class PowerSystem(NovelModel):
    name: str
    source: str
    rules: str = ""
    ranks: list[PowerRank] = Field(default_factory=list)
    limitations: str = ""
    growth_rules: str = ""


class CheatSystem(NovelModel):
    name: str
    cheat_type: str
    origin: str = ""
    functions: str = ""
    limitations: str = ""
    costs: str = ""
    growth_path: str = ""
    story_secret: str = ""


class Faction(NovelModel):
    name: str
    faction_type: str = ""
    summary: str
    goal: str = ""
    leader: str = ""
    territory: str = ""
    strengths: str = ""
    weaknesses: str = ""
    relationships: str = ""


class Character(NovelModel):
    name: str
    role: str
    summary: str
    personality: str = ""
    background: str = ""
    goal: str = ""
    motivation: str = ""
    weakness: str = ""
    character_arc: str = ""
    abilities: str = ""
    faction_name: str = ""
    relationships: str = ""


class PlotBeat(NovelModel):
    order: int = Field(ge=1)
    title: str
    summary: str
    beat_type: str = "plot"
    foreshadowing: str = ""


class OutlineVolume(NovelModel):
    volume_number: int = Field(ge=1)
    title: str
    summary: str
    protagonist_goal: str = ""
    main_conflict: str = ""
    key_events: list[PlotBeat] = Field(default_factory=list)
    climax: str = ""
    ending_hook: str = ""


class NovelOutline(NovelModel):
    summary: str
    central_conflict: str
    themes: str = ""
    volumes: list[OutlineVolume] = Field(default_factory=list)


class NovelBlueprint(NovelModel):
    """生成器、API 和后续数据库写入共同使用的完整小说蓝图。"""

    schema_version: Literal["1.0"] = "1.0"
    project: NovelProject
    world: WorldSetting
    power_system: PowerSystem
    cheat: CheatSystem
    factions: list[Faction] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    outline: NovelOutline


class DesignSessionData(NovelModel):
    """问答阶段收集的原始设定，确认后可映射到 NovelBlueprint。"""

    genre: str = ""
    setting: str = ""
    power_system: str = ""
    cheat: str = ""
    protagonist: str = ""
    goal: str = ""
    tone: str = ""
    length: str = ""
