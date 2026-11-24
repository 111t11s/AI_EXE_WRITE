"""按类型加载项目内的大纲技能，供问答与生成共用。"""

from dataclasses import dataclass
from pathlib import Path

from agents.llm_streaming import emit_generation_event
from core.app_logging import get_logger


logger = get_logger("outline_skills")
SKILL_ROOT = Path(__file__).resolve().parent / "skills"


class OutlineSkillError(RuntimeError):
    pass


@dataclass(frozen=True)
class OutlineSkill:
    id: str
    name: str
    description: str
    aliases: tuple[str, ...] = ()

    def read(self) -> str:
        # 文件名仅来自代码内目录，不使用用户输入拼接路径。
        try:
            text = (SKILL_ROOT / self.id / "SKILL.md").read_text(encoding="utf-8")
            header, body = text.removeprefix("---\n").split("\n---\n", 1)
            if f"name: {self.id}" not in header or not body.strip():
                raise ValueError("技能内容不完整")
            return body.strip()
        except (OSError, ValueError) as exc:
            logger.error("大纲技能加载失败：技能=%s，异常类型=%s", self.id, type(exc).__name__)
            raise OutlineSkillError(f"大纲技能“{self.name}”暂不可用，请检查技能文件") from exc

    def metadata(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description}


GENERAL = OutlineSkill("outline-general", "通用小说大纲设计技能", "主线因果、分卷目标、人物变化与伏笔回收")
SPECIALISTS = (
    OutlineSkill("outline-xuanhuan", "玄幻小说大纲设计技能", "力量、资源、地图与势力联动", ("玄幻",)),
    OutlineSkill("outline-urban", "都市小说大纲设计技能", "事业推进、城市关系与利益冲突", ("都市",)),
    OutlineSkill("outline-wuxia", "武侠小说大纲设计技能", "江湖恩怨、师承与侠义选择", ("武侠",)),
    OutlineSkill("outline-xianxia", "仙侠小说大纲设计技能", "求道目标、修炼代价与宗门因果", ("仙侠", "修真", "修仙")),
    OutlineSkill("outline-history", "历史与架空历史大纲设计技能", "事件年表、时代条件与历史分歧的后果", ("历史",)),
)


def select_outline_skills(genre: str | None) -> tuple[OutlineSkill, ...]:
    value = (genre or "").strip()
    if not value:
        return ()
    matches = [(min(value.index(alias) for alias in skill.aliases if alias in value), skill)
               for skill in SPECIALISTS if any(alias in value for alias in skill.aliases)]
    # 混合类型均加载，按类型出现顺序排列；同一技能的别名不重复加载。
    return (GENERAL, *(skill for _, skill in sorted(matches, key=lambda pair: pair[0])))


def outline_skill_selection(genre: str | None) -> dict:
    skills = select_outline_skills(genre)
    if genre and not skills:
        skills = (GENERAL,)
    for skill in skills:
        skill.read()  # 页面仅在技能文件确实可读时展示“已加载”。
    specialized = len(skills) > 1
    notice = ("已加载：" + "、".join(skill.name for skill in skills)) if skills else "选择小说类型后自动加载大纲设计技能。"
    if skills and not specialized:
        notice += "。当前类型暂无专项蒸馏资料，使用通用技能。"
    return {"genre": genre or "", "specialized": specialized,
            "skills": [skill.metadata() for skill in skills], "notice": notice}


def outline_skill_prompt(genre: str | None) -> str:
    skills = select_outline_skills(genre) or (GENERAL,)
    bodies = [skill.read() for skill in skills]
    names = "、".join(skill.name for skill in skills)
    logger.info("已加载大纲设计技能：类型=%s，技能=%s", genre or "未指定", names)
    emit_generation_event("status", message=f"已加载：{names}")
    return ("\n大纲设计技能（创作参考）：\n" + "\n\n".join(bodies)
            + "\n以上技能用于构思；用户明确要求、已有小说设定和本次输出 Schema 优先。"
            "混合类型按用户重点融合，不机械拼接两套剧情；不要把技能说明写进小说结果。\n")
