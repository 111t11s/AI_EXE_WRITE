"""Load self-contained prose craft prompts from the packaged skill directory."""

from pathlib import Path

from agents.llm_streaming import emit_generation_event
from core.logging import get_logger


logger = get_logger("prose_skills")
SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "prose-writing"
COMMON_FILES = ("SKILL.md", "references/scene-craft.md", "references/story-rhythm.md")
REVISION_FILE = "references/revision.md"


class ProseSkillError(RuntimeError):
    pass


def prose_skill_prompt(*, rewrite: bool = False) -> str:
    files = (*COMMON_FILES, REVISION_FILE) if rewrite else COMMON_FILES
    bodies = []
    for name in files:
        try:
            text = (SKILL_ROOT / name).read_text(encoding="utf-8")
            if name == "SKILL.md":
                if not text.startswith("---\n"):
                    raise ValueError("缺少技能元数据")
                header, text = text[4:].split("\n---\n", 1)
                if "name: prose-writing" not in header.splitlines():
                    raise ValueError("技能名称不匹配")
            if not text.strip():
                raise ValueError("技能内容为空")
            bodies.append(text.strip())
        except (OSError, ValueError) as exc:
            logger.error("正文技能加载失败：文件=%s，异常类型=%s", name, type(exc).__name__)
            raise ProseSkillError("正文写作技能暂不可用，请检查技能文件") from exc
    mode = "正文重写" if rewrite else "正文初稿"
    logger.info("已加载正文写作技能：模式=%s", mode)
    emit_generation_event("status", message=f"已加载：小说正文写作技能（{mode}）")
    return "\n\n" + "\n\n".join(bodies) + "\n"
