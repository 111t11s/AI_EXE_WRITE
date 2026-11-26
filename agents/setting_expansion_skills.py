"""加载设定补全技能。"""

from pathlib import Path

from agents.prose_skills import _skill_text


SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "setting-expansion"


def setting_expansion_skill_prompt() -> str:
    return _skill_text(SKILL_ROOT, "setting-expansion", ("SKILL.md",), "设定补全")
