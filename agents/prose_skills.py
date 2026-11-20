"""Load self-contained prose craft prompts from the packaged skill directory."""

from pathlib import Path

from agents.llm_streaming import emit_generation_event
from core.logging import get_logger


logger = get_logger("prose_skills")
SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "prose-writing"
COMMON_FILES = ("SKILL.md", "references/scene-craft.md", "references/story-rhythm.md")
REVISION_FILE = "references/revision.md"
OPENING_SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "opening-chapters"
OPENING_FILES = ("SKILL.md", "references/opening-methods.md")


class ProseSkillError(RuntimeError):
    pass


def _skill_text(root: Path, skill_name: str, files: tuple[str, ...], label: str) -> str:
    bodies = []
    for name in files:
        try:
            text = (root / name).read_text(encoding="utf-8")
            if name == "SKILL.md":
                if not text.startswith("---\n"):
                    raise ValueError("缺少技能元数据")
                header, text = text[4:].split("\n---\n", 1)
                if f"name: {skill_name}" not in header.splitlines():
                    raise ValueError("技能名称不匹配")
            if not text.strip():
                raise ValueError("技能内容为空")
            bodies.append(text.strip())
        except (OSError, ValueError) as exc:
            logger.error("%s技能加载失败：文件=%s，异常类型=%s", label, name, type(exc).__name__)
            raise ProseSkillError(f"{label}技能暂不可用，请检查技能文件") from exc
    return "\n\n" + "\n\n".join(bodies) + "\n"


def prose_skill_prompt(*, rewrite: bool = False) -> str:
    files = (*COMMON_FILES, REVISION_FILE) if rewrite else COMMON_FILES
    prompt = _skill_text(SKILL_ROOT, "prose-writing", files, "正文写作")
    mode = "正文重写" if rewrite else "正文初稿"
    logger.info("已加载正文写作技能：模式=%s", mode)
    emit_generation_event("status", message=f"已加载：小说正文写作技能（{mode}）")
    return prompt


def opening_skill_prompt(*, chapter_number: int | None, rewrite: bool = False) -> str:
    # Use the global chapter sequence, never a database ID or a volume position.
    if type(chapter_number) is not int or chapter_number not in (1, 2, 3):
        return ""
    files = (*OPENING_FILES, REVISION_FILE) if rewrite else OPENING_FILES
    prompt = _skill_text(OPENING_SKILL_ROOT, "opening-chapters", files, "黄金三章")
    mode = "正文重写" if rewrite else "正文初稿"
    logger.info("已加载黄金三章技能：章序=%s，模式=%s", chapter_number, mode)
    emit_generation_event("status", message=f"已加载：小说黄金三章技能（第 {chapter_number} 章，{mode}）")
    return prompt
