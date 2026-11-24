"""Load task-specific webnovel foundations from self-contained skill files."""

from pathlib import Path

from agents.llm_streaming import emit_generation_event
from agents.prose_skills import _skill_text
from core.app_logging import get_logger


logger = get_logger("webnovel_skills")
SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "webnovel-basics"
CHARACTERS = "references/characters.md"
PLOT = "references/plot-and-payoff.md"
LANGUAGE = "references/viewpoint-and-language.md"
CONFLICT = "references/conflict-and-foreshadowing.md"
DEVELOPMENT = "references/long-form-planning.md"
MODE_FILES = {
    "blueprint": ("references/title-and-introduction.md", CHARACTERS, PLOT, CONFLICT, DEVELOPMENT),
    "outline": (CHARACTERS, PLOT, CONFLICT, DEVELOPMENT),
    "characters": (CHARACTERS,),
    "plan": (CHARACTERS, PLOT, CONFLICT, DEVELOPMENT),
    "draft": (CHARACTERS, LANGUAGE, PLOT, CONFLICT),
    "rewrite": (CHARACTERS, LANGUAGE, PLOT, CONFLICT, "references/feedback-and-revision.md"),
    "continue": (CHARACTERS, LANGUAGE, PLOT, CONFLICT),
    "polish": (CHARACTERS, LANGUAGE, PLOT, CONFLICT, "references/feedback-and-revision.md"),
}
MODE_LABELS = {"blueprint": "小说蓝图", "outline": "大纲设计", "characters": "人物设计",
               "plan": "章节规划", "draft": "正文初稿", "rewrite": "正文重写",
               "continue": "正文续写", "polish": "正文润色"}


def webnovel_skill_prompt(mode: str) -> str:
    files = ("SKILL.md", *MODE_FILES[mode])
    prompt = _skill_text(SKILL_ROOT, "webnovel-basics", files, "网文基础创作")
    label = MODE_LABELS[mode]
    logger.info("已加载网文基础创作技能：模式=%s", label)
    emit_generation_event("status", message=f"已加载：网文基础创作技能（{label}）")
    return f"\n当前网文基础创作任务：{label}，只应用与本任务有关的方法。\n" + prompt
