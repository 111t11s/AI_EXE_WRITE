"""Inputs, generated artifacts and receipts for the novel writing workflow."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

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
