"""加载小说章节拆解技能。"""

from pathlib import Path

from agents.prose_skills import _skill_text


SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "novel-analysis"


def novel_analysis_skill_prompt() -> str:
    return _skill_text(SKILL_ROOT, "novel-analysis", ("SKILL.md",), "小说章节拆解")
