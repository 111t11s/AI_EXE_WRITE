"""Inputs, generated artifacts and receipts for the novel writing workflow."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.novel_schemas import RowId, Sequence


Title = Annotated[str, Field(min_length=1, max_length=200)]
Detail = Annotated[str, Field(min_length=1, max_length=4000)]


class WritingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instruction: str = Field(default="", max_length=2000)


class OutlineRequest(WritingInput):
    volume_count: int = Field(default=3, ge=1, le=5)


class ChapterPlanRequest(WritingInput):
    start_chapter: Sequence = 1
    chapter_count: int = Field(default=5, ge=1, le=12)


class DraftRequest(WritingInput):
    target_characters: int = Field(default=2000, ge=300, le=6000)


class CoreCharacterRequest(WritingInput):
    protagonist_count: int = Field(default=1, ge=1, le=2)
    important_count: int = Field(default=3, ge=0, le=8)


class SupportingCharacterRequest(WritingInput):
    supporting_count: int = Field(default=6, ge=0, le=12)
    volume_id: RowId | None = None
    reuse_character_ids: list[RowId] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def check_selection(self):
        if len(self.reuse_character_ids) != len(set(self.reuse_character_ids)):
            raise ValueError("复用角色不能重复")
        if self.reuse_character_ids and self.volume_id is None:
            raise ValueError("复用角色必须选择目标分卷")
        if not self.supporting_count and not self.reuse_character_ids:
            raise ValueError("请新增至少一位配角或勾选要复用的配角")
        return self


class ConsistencyRequest(WritingInput):
    outline_id: RowId
    chapter_ids: list[RowId] = Field(min_length=1, max_length=20)

    @field_validator("chapter_ids")
    @classmethod
    def unique_chapters(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("所选章节不能重复")
        return values


class Generated(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BeatDraft(Generated):
    title: Title
    summary: Detail
    beat_type: str = Field(default="plot", min_length=1, max_length=80)
    foreshadowing: str = Field(default="", max_length=4000)


class VolumeDraft(Generated):
    title: Title
    summary: Detail
    protagonist_goal: Detail
    main_conflict: Detail
    climax: Detail
    ending_hook: Detail
    beats: list[BeatDraft] = Field(min_length=1, max_length=10)


class OutlineDraft(Generated):
    summary: Detail
    central_conflict: Detail
    themes: Detail
    volumes: list[VolumeDraft] = Field(min_length=1, max_length=5)


class ChapterPlan(Generated):
    title: Title
    summary: Detail


class ChapterPlanDraft(Generated):
    chapters: list[ChapterPlan] = Field(min_length=1, max_length=12)


class ChapterDraft(Generated):
    content: str = Field(min_length=100, max_length=30000)


class ReviewIssue(Generated):
    severity: Literal["minor", "major"]
    evidence: Detail
    suggestion: Detail


class ReviewReport(Generated):
    verdict: Literal["ready", "revise"]
    summary: Detail
    issues: list[ReviewIssue] = Field(max_length=12)


class CharacterDraft(Generated):
    category: Literal["protagonist", "important", "supporting"]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    role: str = Field(min_length=1, max_length=70)
    summary: Detail
    personality: Detail
    background: Detail
    goal: Detail
    motivation: Detail
    weakness: Detail
    character_arc: Detail
    abilities: Detail


class ReusedCharacterDraft(Generated):
    character_id: RowId
    scenes: Detail


class CharacterDraftList(Generated):
    characters: list[CharacterDraft] = Field(max_length=22)
    reused_characters: list[ReusedCharacterDraft] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_cast(self):
        if not self.characters and not self.reused_characters:
            raise ValueError("角色和复用戏份不能同时为空")
        return self


class ChapterEvidence(Generated):
    chapter_id: RowId
    quote: str = Field(min_length=1, max_length=1000)


class ConsistencyBasis(Generated):
    kind: Literal["outline", "volume", "plot_beat", "character", "world", "chapter"]
    record_id: RowId
    field: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=1000)


class ConsistencyIssue(Generated):
    severity: Literal["minor", "major"]
    category: Literal["plot", "character", "world", "continuity"]
    title: Title
    evidence: list[ChapterEvidence] = Field(min_length=1, max_length=5)
    basis: ConsistencyBasis
    explanation: Detail
    suggestion: Detail


class ConsistencyReport(Generated):
    verdict: Literal["consistent", "drifted", "insufficient"]
    summary: Detail
    issues: list[ConsistencyIssue] = Field(max_length=30)
    limitations: list[Detail] = Field(default_factory=list, max_length=12)


class ConsistencyReference(BaseModel):
    kind: str
    record_id: RowId
    label: str


class ConsistencyReceipt(ConsistencyReport):
    project_id: RowId
    outline_id: RowId
    outline_version: Sequence
    checked_at: str
    chapters: list["ChapterPlanEntry"]
    references: list[ConsistencyReference]
    limitations: list[Detail] = Field(max_length=16)


class OutlineReceipt(BaseModel):
    project_id: RowId
    outline_id: RowId
    version_no: Sequence
    volume_ids: list[RowId]
    plot_beat_count: int


class ChapterPlanEntry(BaseModel):
    chapter_id: RowId
    chapter_number: Sequence
    title: str


class ChapterPlanReceipt(BaseModel):
    project_id: RowId
    volume_id: RowId
    chapters: list[ChapterPlanEntry]


class DraftReceipt(BaseModel):
    chapter_id: RowId
    status: Literal["draft"] = "draft"
    word_count: int


class CharacterReceipt(BaseModel):
    project_id: RowId
    character_ids: list[RowId]
    counts: dict[str, int]
    volume_id: RowId | None = None
    reused_character_ids: list[RowId] = Field(default_factory=list)
