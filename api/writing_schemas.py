"""Inputs, generated artifacts and receipts for the novel writing workflow."""

import re
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

FORESHADOW_CATEGORY_LABELS = {
    "identity": "身份秘密", "treasure": "秘宝奇物", "event": "事件真相",
    "relationship": "关系伏笔", "ability": "能力来历", "world": "世界规则",
    "prophecy": "预言征兆", "debt": "人情仇恨", "location": "地点秘密", "other": "其他",
}


class WritingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instruction: str = Field(default="", max_length=2000)
    # 可选模型配置 ID（GET /api/v1/models）；省略时使用主模型。
    model: str | None = Field(default=None, max_length=40)
    # 用户从写作技法知识库选择的技法，最多 5 条。
    technique_ids: list[int] = Field(default_factory=list, max_length=5)

    @field_validator("technique_ids")
    @classmethod
    def valid_technique_ids(cls, values):
        if any(value <= 0 for value in values):
            raise ValueError("技法编号必须为正整数")
        if len(values) != len(set(values)):
            raise ValueError("不能重复选择同一技法")
        return values


class OutlineRequest(WritingInput):
    volume_count: int = Field(default=1, ge=1, le=5)
    volume_number: int = Field(default=0, ge=0, le=1000)
    volume_theme: str = Field(default="", max_length=500)
    outline_theme: OutlineTheme = "development"
    min_foreshadows: int = Field(default=0, ge=0, le=30)
    foreshadow_categories: list[str] = Field(default_factory=list, max_length=10)
    target_word_min: int = Field(default=0, ge=0, le=10_000_000)
    target_word_max: int = Field(default=0, ge=0, le=10_000_000)
    plot_progression: str = Field(default="", max_length=4000)
    consume_foreshadow_ids: list[RowId] = Field(default_factory=list, max_length=30)
    plant_foreshadow_ids: list[RowId] = Field(default_factory=list, max_length=30)

    @field_validator("foreshadow_categories")
    @classmethod
    def unique_foreshadow_categories(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("伏笔类型不能重复选择")
        if any(value not in FORESHADOW_CATEGORY_LABELS for value in values):
            raise ValueError("存在不支持的伏笔类型")
        return values

    @model_validator(mode="after")
    def validate_outline_plan(self):
        if self.target_word_min and self.target_word_max and self.target_word_max < self.target_word_min:
            raise ValueError("大纲总体字数上限不能小于下限")
        return self

    @field_validator("consume_foreshadow_ids", "plant_foreshadow_ids")
    @classmethod
    def unique_foreshadows(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("伏笔不能重复选择")
        return values

    @model_validator(mode="after")
    def disjoint_foreshadows(self):
        if set(self.consume_foreshadow_ids) & set(self.plant_foreshadow_ids):
            raise ValueError("同一伏笔不能同时设为消费和预埋")
        return self

    @model_validator(mode="after")
    def require_custom_theme_description(self):
        if self.outline_theme == "custom" and not self.instruction.strip():
            raise ValueError("选择自定义主题时请在补充要求中说明主题")
        return self


class ChapterPlanRequest(WritingInput):
    start_chapter: Sequence = 1
    # Retained for old clients; planning now derives the whole-volume length
    # from the volume word budget and the 2500–3500 character target.
    chapter_count: int | None = Field(default=None, ge=1, le=80)


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
    protagonist_count: Literal[2] = 2
    important_count: Literal[0] = 0
    outline_id: RowId | None = None
    volume_id: RowId | None = None

    @model_validator(mode="after")
    def require_outline_for_volume(self):
        if self.volume_id is not None and self.outline_id is None:
            raise ValueError("指定分卷时必须选择所属大纲")
        return self


class SupportingCharacterRequest(WritingInput):
    supporting_count: int | None = Field(default=None, ge=0)
    volume_id: RowId | None = None
    reuse_character_ids: list[RowId] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def check_selection(self):
        if len(self.reuse_character_ids) != len(set(self.reuse_character_ids)):
            raise ValueError("复用角色不能重复")
        if self.reuse_character_ids and self.volume_id is None:
            raise ValueError("复用角色必须选择目标分卷")
        if self.supporting_count == 0 and not self.reuse_character_ids:
            raise ValueError("请选择要复用的配角，或让 AI 自动决定新增人数")
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
    # 采纳修复时使用的模型配置；编辑意见可省略。
    model: str | None = Field(default=None, max_length=40)


class ConsistencyRepairRequest(BaseModel):
    """批量采纳：按问题序号依次修复，使用各问题当前的修复意见。"""

    model_config = ConfigDict(extra="forbid")

    issue_indexes: list[int] = Field(min_length=1, max_length=50)
    model: str | None = Field(default=None, max_length=40)


class Generated(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BeatDraft(Generated):
    title: Title
    summary: Detail
    beat_type: str = Field(default="plot", min_length=1, max_length=80)
    foreshadowing: str = Field(default="", max_length=4000)


class OutlineForeshadow(Generated):
    title: Title
    category: str = Field(default="other", max_length=32)
    summary: Detail
    details: str = Field(default="", max_length=4000)


class VolumeDraft(Generated):
    title: Title
    summary: Detail
    protagonist_goal: Detail
    main_conflict: Detail
    climax: Detail
    ending_hook: Detail
    beats: list[BeatDraft] = Field(min_length=1, max_length=10)
    foreshadows: list[OutlineForeshadow] = Field(default_factory=list, max_length=30)


class OutlineDraft(Generated):
    summary: Detail
    central_conflict: Detail
    themes: Detail
    volumes: list[VolumeDraft] = Field(min_length=1, max_length=5)


class ChapterPlan(Generated):
    title: Title
    summary: Detail
    character_ids: list[RowId] = Field(default_factory=list, max_length=30)
    foreshadow_ids: list[RowId] = Field(default_factory=list, max_length=20)
    new_foreshadows: list[OutlineForeshadow] = Field(default_factory=list, max_length=10)


class ChapterPlanDraft(Generated):
    chapters: list[ChapterPlan] = Field(min_length=1, max_length=80)


class TechniqueSelection(Generated):
    technique_ids: list[RowId] = Field(default_factory=list, max_length=5)


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


class CharacterRelationshipDraft(Generated):
    target_name: Annotated[str, Field(min_length=1, max_length=160)]
    relationship_type: Annotated[str, Field(min_length=1, max_length=100)]
    description: Detail


class CharacterFactionDraft(Generated):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    faction_type: str = Field(default="", max_length=120)
    summary: Detail
    goal: str = ""
    leader: str = Field(default="", max_length=160)
    territory: str = Field(default="", max_length=300)
    strengths: str = ""
    weaknesses: str = ""
    relationships: str = ""


class CharacterDraft(Generated):
    category: Literal["protagonist", "important", "supporting"]
    gender: Literal["男", "女"] | None = None
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
    faction_name: str = Field(default="", max_length=160)
    relationships: list[CharacterRelationshipDraft] = Field(default_factory=list, max_length=20)
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
    characters: list[CharacterDraft] = Field(default_factory=list)
    new_factions: list[CharacterFactionDraft] = Field(default_factory=list, max_length=12)
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
                  "volume_character", "world", "region", "power_system", "power_rank",
                  "cheat_system", "faction", "chapter", "foreshadow"]
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


class EntityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chapter_id: RowId
    quote: str = Field(min_length=2, max_length=1000)


class NewCharacterCandidate(Generated):
    name: str = Field(min_length=1, max_length=160)
    role: str = Field(default="新登场角色", max_length=80)
    summary: str = Field(default="", max_length=2000)
    personality: str = Field(default="", max_length=2000)
    background: str = Field(default="", max_length=2000)
    goal: str = Field(default="", max_length=2000)
    motivation: str = Field(default="", max_length=2000)
    weakness: str = Field(default="", max_length=2000)
    character_arc: str = Field(default="", max_length=2000)
    abilities: str = Field(default="", max_length=2000)
    faction_name: str = Field(default="", max_length=160)
    evidence: list[EntityEvidence] = Field(min_length=1, max_length=5)


class NewFactionCandidate(Generated):
    name: str = Field(min_length=1, max_length=160)
    faction_type: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=2000)
    goal: str = Field(default="", max_length=2000)
    leader: str = Field(default="", max_length=160)
    territory: str = Field(default="", max_length=300)
    strengths: str = Field(default="", max_length=2000)
    weaknesses: str = Field(default="", max_length=2000)
    relationships: str = Field(default="", max_length=2000)
    evidence: list[EntityEvidence] = Field(min_length=1, max_length=5)


class EntityDiscoveryReport(Generated):
    characters: list[NewCharacterCandidate] = Field(default_factory=list, max_length=20)
    factions: list[NewFactionCandidate] = Field(default_factory=list, max_length=20)
    misc_notes: list["MiscNoteCandidate"] = Field(default_factory=list, max_length=30)


ForeshadowCategory = Literal["identity", "treasure", "event", "relationship", "ability",
                             "world", "prophecy", "debt", "location", "other"]
MiscNoteType = Literal["event", "item", "beast", "passerby", "technique", "location", "custom"]


class ForeshadowCandidate(Generated):
    title: str = Field(min_length=1, max_length=200)
    category: ForeshadowCategory = "other"
    summary: str = Field(default="", max_length=4000)
    details: str = Field(default="", max_length=20000)
    evidence: list[EntityEvidence] = Field(min_length=1, max_length=5)


class MiscNoteCandidate(Generated):
    note_type: MiscNoteType = "event"
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    details: str = Field(default="", max_length=20000)
    evidence: list[EntityEvidence] = Field(min_length=1, max_length=5)


class ForeshadowCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    category: ForeshadowCategory = "other"
    summary: str = Field(default="", max_length=4000)
    details: str = Field(default="", max_length=20000)


class MiscNoteCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    note_type: MiscNoteType = "event"
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    details: str = Field(default="", max_length=20000)


EntityDiscoveryReport.model_rebuild()


class ExistingEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    existing_id: RowId
    model: str | None = Field(default=None, max_length=40)


DEFAULT_TREND_INSTRUCTION = ("优先参考各小说网站最新热门榜单中的作品类型、题材设定和读者偏好，"
                            "提炼原创网文选题，不直接改编真实人物或事件；避开政治、时政、外交、军事、选举和政策争议。")


class TrendBook(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=80)


class TrendRequest(WritingInput):
    """热点选题分析：可手动提供热点，也可使用服务端抓取的列表。"""

    topics: list[Annotated[str, Field(min_length=2, max_length=200)]] = Field(default_factory=list, max_length=40)
    # 手动粘贴的选题要求（不是热点标题时），先理解要求再从热点中挑选相关条目。
    brief: str = Field(default="", max_length=1000)
    genre_preference: str = Field(default="都市、玄幻、言情、悬疑等网文类型", max_length=40)
    audience: str = Field(default="关注热门网络小说的读者", max_length=40)
    sources: list[Annotated[str, Field(min_length=1, max_length=80)]] | None = Field(default=None, max_length=20)
    books: list[TrendBook] = Field(default_factory=list, max_length=40)
    instruction: str = Field(default=DEFAULT_TREND_INSTRUCTION, max_length=2000)
    idea_count: int = Field(default=4, ge=2, le=5)


class TrendIdea(Generated):
    # 热点推荐由模型自由发挥：字段先全部收下，再由业务层丢弃缺字段的半成品，
    # 避免一条不完整的方案让整份结果作废。
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    genre: str = Field(default="", max_length=40)
    setting: str = Field(default="", max_length=300)
    power_system: str = Field(default="", max_length=400)
    cheat: str = Field(default="", max_length=240)
    protagonist: str = Field(default="", max_length=400)
    goal: str = Field(default="", max_length=240)
    tone: str = Field(default="", max_length=60)
    length: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=80)
    premise: str = Field(default="", max_length=600)
    core_conflict: str = Field(default="", max_length=600)
    # 热梗/可讨论点：为什么有人愿意转发、站队或玩梗。
    discussion_hook: str = Field(default="", max_length=400)
    selling_points: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(default_factory=list, max_length=5)
    reference_topics: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=5)
    risks: str = Field(default="", max_length=400)

    @field_validator("selling_points", "reference_topics", mode="before")
    @classmethod
    def wrap_single_value(cls, value):
        """模型偶尔把列表写成一段文字；按行拆分后仍可使用。"""
        if isinstance(value, str):
            parts = [part.strip() for part in re.split(r"[\n;；]+", value) if part.strip()]
            return parts or [value.strip()]
        return value

    @field_validator("risks", mode="before")
    @classmethod
    def empty_risks(cls, value):
        return "" if value is None else value


class TrendReport(Generated):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    overview: str = Field(min_length=1, max_length=800)
    # 对手动选题要求（brief）的解读；没有 brief 时为空。
    requirement_summary: str = Field(default="", max_length=400)
    ideas: list[TrendIdea] = Field(min_length=2, max_length=6)

    @model_validator(mode="before")
    @classmethod
    def accept_common_shapes(cls, value):
        """兼容模型只返回方案数组、或漏写总览的常见写法。"""
        fallback = "以下方案根据本次热点提炼，可按需调整。"
        if isinstance(value, list):
            return {"overview": fallback, "ideas": value}
        if isinstance(value, dict):
            data = dict(value)
            if "ideas" not in data:
                for key in ("recommendations", "results", "plans"):
                    if isinstance(data.get(key), list):
                        data["ideas"] = data[key]
                        break
            data.setdefault("overview", fallback)
            return data
        return value


class TrendReceipt(TrendReport):
    generated_at: str
    topics: list[str]
    sources: list[str] = Field(default_factory=list)
    record_id: int | None = None


AnalysisDimensionName = Literal["character", "profession", "world", "plot", "prose"]


class NovelAnalysisEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    quote: str = Field(min_length=2, max_length=600)
    note: str = Field(default="", max_length=300)


class NovelAnalysisTechnique(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    dimension: str = Field(min_length=1, max_length=40)
    dimension_label: str = Field(default="", max_length=40)
    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=600)
    mechanism: str = Field(default="", max_length=1200)
    reuse: str = Field(default="", max_length=1000)
    evidence: list[NovelAnalysisEvidence] = Field(min_length=1, max_length=4)
    confidence: Literal["high", "medium", "low"] = "medium"
    applicable_genres: list[Annotated[str, Field(max_length=20)]] = Field(default_factory=list, max_length=8)


class NovelAnalysisDimension(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    dimension: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=40)
    available: bool = False
    summary: str = Field(default="", max_length=800)
    techniques: list[NovelAnalysisTechnique] = Field(default_factory=list, max_length=4)
    missing: str = Field(default="", max_length=600)


class NovelAnalysisCandidate(BaseModel):
    """不属于固定五维、等待用户审核的新维度技法。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    dimension: str = Field(min_length=1, max_length=40)
    dimension_label: str = Field(min_length=1, max_length=40)
    dimension_reason: str = Field(default="", max_length=600)
    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=600)
    mechanism: str = Field(default="", max_length=1200)
    reuse: str = Field(default="", max_length=1000)
    evidence: list[NovelAnalysisEvidence] = Field(min_length=1, max_length=4)
    confidence: Literal["high", "medium", "low"] = "medium"
    applicable_genres: list[Annotated[str, Field(max_length=20)]] = Field(default_factory=list, max_length=8)


class NovelAnalysisReport(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    overview: str = Field(min_length=1, max_length=1000)
    source_summary: str = Field(default="", max_length=800)
    dimensions: list[NovelAnalysisDimension] = Field(min_length=1, max_length=10)
    extra_techniques: list[NovelAnalysisCandidate] = Field(default_factory=list, max_length=5)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class NovelAnalysisReceipt(NovelAnalysisReport):
    file_names: list[str] = Field(default_factory=list)
    generated_at: str
    record_id: int | None = None


class SettingExpansionReport(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    core_concept: str = Field(min_length=1, max_length=4000)
    world_overview: str = Field(min_length=1, max_length=5000)
    power_system: str = Field(min_length=1, max_length=5000)
    profession_system: str = Field(default="", max_length=4000)
    society: str = Field(default="", max_length=4000)
    factions: str = Field(default="", max_length=4000)
    story_hooks: list[str] = Field(default_factory=list, max_length=12)
    preserved_elements: list[str] = Field(default_factory=list, max_length=20)
    additions: list[str] = Field(default_factory=list, max_length=20)
    open_questions: list[str] = Field(default_factory=list, max_length=12)


class SettingExpansionReceipt(SettingExpansionReport):
    file_names: list[str] = Field(default_factory=list)
    generated_at: str
    record_id: int | None = None


class OutlineReceipt(BaseModel):
    project_id: RowId
    outline_id: RowId
    version_no: Sequence
    volume_ids: list[RowId]
    plot_beat_count: int
    foreshadow_count: int = 0


class ChapterPlanEntry(BaseModel):
    chapter_id: RowId
    chapter_number: Sequence
    title: str


class ChapterPlanReceipt(BaseModel):
    project_id: RowId
    volume_id: RowId
    chapters: list[ChapterPlanEntry]
    foreshadow_count: int = 0


class DraftReceipt(BaseModel):
    chapter_id: RowId
    status: Literal["draft"] = "draft"
    word_count: int


class CharacterProgressReceipt(BaseModel):
    chapter_id: RowId
    updated_count: int
    skipped_count: int = 0
    warning: str = ""


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
    warning: str = ""


class CharacterReceipt(BaseModel):
    project_id: RowId
    outline_id: RowId | None = None
    character_ids: list[RowId]
    counts: dict[str, int]
    volume_id: RowId | None = None
    reused_character_ids: list[RowId] = Field(default_factory=list)
    profile_character_ids: list[RowId] = Field(default_factory=list)
