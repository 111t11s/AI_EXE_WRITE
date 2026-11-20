"""Inputs, generated artifacts and receipts for the novel writing workflow."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.novel_schemas import RowId, Sequence


Title = Annotated[str, Field(min_length=1, max_length=200)]
Detail = Annotated[str, Field(min_length=1, max_length=4000)]

OutlineTheme = Literal[
    "development", "comeback", "ending", "survival",
    "revenge", "mystery", "relationship", "custom",
]

OUTLINE_THEME_LABELS = {
    "development": "发育（成长推进）",
    "comeback": "逆袭",
    "ending": "结尾收束",
    "survival": "逃杀（生存竞逐）",
    "revenge": "复仇",
    "mystery": "揭秘",
    "relationship": "关系推进",
    "custom": "自定义主题",
}


class WritingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instruction: str = Field(default="", max_length=2000)


class OutlineRequest(WritingInput):
    volume_count: int = Field(default=3, ge=1, le=5)
    outline_theme: OutlineTheme = "development"

    @model_validator(mode="after")
    def require_custom_theme_description(self):
        if self.outline_theme == "custom" and not self.instruction.strip():
            raise ValueError("选择自定义主题时请在补充要求中说明主题")
        return self


class ChapterPlanRequest(WritingInput):
    start_chapter: Sequence = 1
    chapter_count: int = Field(default=5, ge=1, le=12)


class DraftRequest(WritingInput):
    target_characters: int = Field(default=2000, ge=300, le=6000)


class BatchDraftRequest(DraftRequest):
    chapter_ids: list[RowId] = Field(min_length=1, max_length=20)

    @field_validator("chapter_ids")
    @classmethod
    def unique_chapters(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("批量生成的章节不能重复")
        return values


class CoreCharacterRequest(WritingInput):
    protagonist_count: int = Field(default=1, ge=1, le=2)
    important_count: int = Field(default=3, ge=0, le=8)
    outline_id: RowId | None = None
    volume_id: RowId | None = None

    @model_validator(mode="after")
    def require_outline_for_volume(self):
        if self.volume_id is not None and self.outline_id is None:
            raise ValueError("指定分卷时必须选择所属大纲")
        return self


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


class CharacterProfileRequest(WritingInput):
    character_ids: list[RowId] = Field(min_length=1, max_length=20)

    @field_validator("character_ids")
    @classmethod
    def unique_characters(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("角色不能重复选择")
        return values


class ConsistencyRequest(WritingInput):
    outline_id: RowId
    chapter_ids: list[RowId] = Field(min_length=1, max_length=20)

    @field_validator("chapter_ids")
    @classmethod
    def unique_chapters(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("所选章节不能重复")
        return values


class ConsistencySuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    suggestion: Detail


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


class CharacterChapterUpdate(Generated):
    character_id: RowId
    actions: str = Field(min_length=1, max_length=1600)
    current_state: str = Field(min_length=1, max_length=1600)
    relationships: str = Field(default="", max_length=1600)
    evidence: list[Annotated[str, Field(min_length=1, max_length=600)]] = Field(min_length=1, max_length=6)


class ChapterDraft(Generated):
    """Legacy combined draft payload retained for compatibility."""
    content: str = Field(min_length=100, max_length=30000)
    character_updates: list[CharacterChapterUpdate] = Field(max_length=40)


class ChapterContentDraft(Generated):
    """A body-only draft; character progress is analyzed separately."""
    # Ignore the field emitted by older model prompts so existing stream
    # clients can be upgraded without mixing progress back into the body task.
    model_config = ConfigDict(extra="ignore")
    content: str = Field(min_length=100, max_length=30000)


class CharacterProgressAnalysis(Generated):
    character_updates: list[CharacterChapterUpdate] = Field(max_length=40)


class CharacterProgressEntry(CharacterChapterUpdate):
    id: RowId
    chapter_id: RowId
    chapter_number: Sequence
    title: str
    volume_id: RowId | None


class CharacterProgressPage(BaseModel):
    project_id: RowId
    items: list[CharacterProgressEntry]
    limit: int
    offset: int
    has_more: bool


class ReviewIssue(Generated):
    severity: Literal["minor", "major"]
    evidence: Detail
    suggestion: Detail


class ReviewReport(Generated):
    verdict: Literal["ready", "revise"]
    summary: Detail
    issues: list[ReviewIssue] = Field(max_length=12)


class CharacterVolumeProfile(Generated):
    volume_id: RowId
    plot_beat_ids: list[RowId] = Field(default_factory=list, max_length=30)
    actions: str = Field(min_length=1, max_length=900)
    outcome: str = Field(min_length=1, max_length=900)
    progress_relation: str = Field(min_length=1, max_length=900)


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
    volume_profiles: list[CharacterVolumeProfile] = Field(default_factory=list, max_length=5)


class ReusedCharacterDraft(Generated):
    character_id: RowId
    scenes: Detail
    volume_profiles: list[CharacterVolumeProfile] = Field(default_factory=list, max_length=5)


class ExistingCharacterProfile(Generated):
    character_id: RowId
    volume_profiles: list[CharacterVolumeProfile] = Field(min_length=1, max_length=5)


class CharacterProfileList(Generated):
    profiles: list[ExistingCharacterProfile] = Field(min_length=1, max_length=20)


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
    kind: Literal["project", "outline", "volume", "plot_beat", "character", "character_relationship",
                  "volume_character", "world", "region", "power_system", "power_rank", "cheat_system", "faction", "chapter"]
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
    reference_materials: list[ConsistencyReference] = Field(default_factory=list)
    limitations: list[Detail] = Field(max_length=20)
    job_id: str | None = None
    chapter_fingerprints: dict[str, str] = Field(default_factory=dict)


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


class CharacterProgressReceipt(BaseModel):
    chapter_id: RowId
    updated_count: int


class BatchDraftChapterReceipt(BaseModel):
    chapter_id: RowId
    chapter_number: Sequence
    title: str
    word_count: int
    progress_count: int


class BatchDraftReceipt(BaseModel):
    project_id: RowId
    completed_count: int
    chapters: list[BatchDraftChapterReceipt]


class CharacterReceipt(BaseModel):
    project_id: RowId
    outline_id: RowId | None = None
    character_ids: list[RowId]
    counts: dict[str, int]
    volume_id: RowId | None = None
    reused_character_ids: list[RowId] = Field(default_factory=list)
    profile_character_ids: list[RowId] = Field(default_factory=list)
