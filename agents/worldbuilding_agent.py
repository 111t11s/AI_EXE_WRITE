"""持续收集小说设定，并在确认后生成世界观和大纲。"""

from __future__ import annotations

import json
import os
import re
from asyncio import CancelledError, Lock
from dataclasses import dataclass, field
from typing import Any

from starlette.concurrency import run_in_threadpool

from agents.mcp.worldbuilding import call_world_tool, crewai_world_tools, worldbuilding_session
from core.config import get_settings
from core.logging import get_logger
from db.models import Character, CheatSystem, NovelBlueprint, NovelOutline, NovelProject, PowerSystem, WorldSetting


logger = get_logger("worldbuilding")


SLOT_LABELS = {
    "genre": "小说类型",
    "setting": "时代与背景",
    "power_system": "力量体系",
    "cheat": "金手指类型",
    "protagonist": "主角设定",
    "goal": "主角目标",
    "tone": "故事基调",
    "length": "篇幅规划",
}


@dataclass
class ConversationState:
    values: dict[str, str] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    confirmed: bool = False
    result: dict[str, Any] | None = None
    generation_error: str | None = None
    persistence_error: str | None = None
    project_id: int | None = None
    world_id: int | None = None
    confirmation_lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def persistence(self) -> dict:
        return {"project_id": self.project_id, "world_id": self.world_id, "error": self.persistence_error}

    @property
    def missing(self) -> list[str]:
        return [key for key in SLOT_LABELS if not self.values.get(key)]


class WorldbuildingAssistant:
    """一个单会话助手；会话状态由 API 层持有。"""

    def start(self, state: ConversationState) -> str:
        if not state.history:
            message = "我们先确定小说的基础方向。你想写什么类型的小说？例如玄幻、都市、科幻、历史或悬疑。"
            state.history.append({"role": "assistant", "content": message})
            return message
        return self.next_question(state)

    def receive(self, state: ConversationState, content: str) -> str:
        content = content.strip()
        if not content:
            return "请先输入一些设定，我会据此继续完善小说方案。"
        state.history.append({"role": "user", "content": content})

        if state.confirmed:
            return "当前蓝图已生成。重新开始一个新会话即可创建另一部小说；如需修改，可先在生成结果中调整后再保存新版本。"

        correction = self._parse_correction(content)
        if correction:
            key, value = correction
            state.values[key] = value
            if state.missing:
                return self.next_question(state)
            return self.summary(state)

        if state.missing:
            state.values[state.missing[0]] = content
        else:
            message = "请按“设定项：新内容”的格式告诉我需要修改哪项，例如“力量体系：灵气分为九境”。"
            state.history.append({"role": "assistant", "content": message})
            return message
        if state.missing:
            return self.next_question(state)
        return self.summary(state)

    @staticmethod
    def _parse_correction(content: str) -> tuple[str, str] | None:
        aliases = {
            "小说类型": "genre", "题材": "genre", "genre": "genre",
            "时代与背景": "setting", "世界背景": "setting", "背景": "setting",
            "setting": "setting", "力量体系": "power_system", "power_system": "power_system",
            "金手指类型": "cheat", "金手指": "cheat", "cheat": "cheat",
            "主角设定": "protagonist", "主角": "protagonist", "protagonist": "protagonist",
            "主角目标": "goal", "目标": "goal", "goal": "goal",
            "故事基调": "tone", "基调": "tone", "tone": "tone",
            "篇幅规划": "length", "篇幅": "length", "length": "length",
        }
        match = re.match(r"^\s*([^：:]{1,30})\s*[：:]\s*(.+)$", content, re.DOTALL)
        if not match:
            return None
        key = aliases.get(match.group(1).strip().lower())
        value = match.group(2).strip()
        return (key, value) if key and value else None

    def next_question(self, state: ConversationState) -> str:
        key = state.missing[0]
        prompts = {
            "setting": "时代和世界背景是什么？例如现代都市、东方玄幻大陆、星际殖民时代。",
            "power_system": "力量体系如何运作？请说明力量来源、等级划分和主要限制。",
            "cheat": "主角的金手指是什么？它怎样获得，能解决什么问题，又有什么代价？",
            "protagonist": "主角是谁？请描述身份、性格、初始处境和一个明显弱点。",
            "goal": "主角最想达成的目标是什么？失败会失去什么？",
            "tone": "你希望故事整体是什么基调？例如热血成长、黑暗压抑、轻松爽文或群像史诗。",
            "length": "计划写多长？可以填写预计字数、卷数或单卷章节数。",
        }
        message = prompts[key]
        state.history.append({"role": "assistant", "content": message})
        return message

    def summary(self, state: ConversationState) -> str:
        lines = ["基础设定已收集完成，请确认后生成世界观和大纲："]
        lines.extend(f"- {SLOT_LABELS[key]}：{state.values[key]}" for key in SLOT_LABELS)
        message = "\n".join(lines) + "\n\n回复“确认生成”开始生成，或直接告诉我需要修改的项目。"
        state.history.append({"role": "assistant", "content": message})
        return message

    async def confirm(self, state: ConversationState) -> dict[str, Any]:
        async with state.confirmation_lock:
            if state.result is not None:
                return state.result
            if state.missing:
                raise ValueError(f"还缺少：{SLOT_LABELS[state.missing[0]]}")
            state.confirmed = True
            try:
                blueprint, state.generation_error = await run_in_threadpool(self._generate_with_crewai, state.values.copy())
            except (Exception, CancelledError):
                state.confirmed = False
                raise
            state.result = blueprint.model_dump(mode="json")
            if state.generation_error is None:
                await self.save_world(state, blueprint)
            state.history.append({"role": "assistant", "content": "世界观和大纲已生成完成。"})
            return state.result

    @staticmethod
    async def save_world(state: ConversationState, blueprint: NovelBlueprint) -> None:
        """Persist the validated project and world, recording any partial write."""
        try:
            async with worldbuilding_session() as session:
                project = await call_world_tool(session, "create_novel_project", {
                    "payload": blueprint.project.model_dump(mode="json"),
                })
                state.project_id = project["id"]
                world = await call_world_tool(session, "create_novel_world", {
                    "payload": {
                        **blueprint.world.model_dump(mode="json", exclude={"regions"}),
                        "project_id": state.project_id,
                    },
                })
                state.world_id = world["id"]
        except CancelledError:
            if state.world_id is None:
                state.persistence_error = "保存请求已中断，请核对数据库中的记录。"
            raise
        except Exception as exc:
            logger.warning("世界观保存流程异常 type=%s project_created=%s world_created=%s",
                           type(exc).__name__, state.project_id is not None, state.world_id is not None)
            if state.world_id is None:
                state.persistence_error = "未能确认项目和世界观均已保存。请检查数据库或 MCP 连接。"

    @staticmethod
    def create_generation_agent(llm):
        from crewai import Agent

        return Agent(role="小说世界观与大纲架构师",
                     goal="根据已确认设定输出自洽、可执行的世界观和长篇大纲",
                     backstory="你擅长搭建力量体系、势力关系和长篇故事结构。",
                     llm=llm, verbose=False, allow_delegation=False,
                     max_iter=3, max_retry_limit=0, mcps=[crewai_world_tools()])

    @staticmethod
    def _generate_with_crewai(values: dict[str, str]) -> tuple[NovelBlueprint, str | None]:
        """调用 CrewAI 并验证输出；模型异常时返回可编辑的合法蓝图。"""
        try:
            from crewai import Crew, LLM, Process, Task

            settings = get_settings()
            model_name = os.getenv("MODEL_NAME", settings.DEEPSEEK_MODEL_NAME)
            llm = LLM(model=model_name, api_key=settings.API_KEY, base_url=settings.PROXY_URL,
                      timeout=settings.REQUEST_TIMEOUT or 120, max_retries=0, stream=False)
            agent = WorldbuildingAssistant.create_generation_agent(llm)
            prompt = json.dumps(values, ensure_ascii=False, indent=2)
            task = Task(description=(
                "根据已确认设定生成完整 NovelBlueprint JSON。严格按提供的 JSON Schema 输出，"
                "只输出 JSON，不要 Markdown。人物、力量规则、势力目标、金手指代价和大纲因果必须互相一致。"
                "只有用户明确引用某个已有项目或世界观 ID 时，才使用 MCP 工具按该 ID 查询。"
                "至少给出一个世界规则、三名角色、两个势力和三卷大纲；篇幅规划较短时可相应缩减。"
                f"\nJSON Schema：{json.dumps(NovelBlueprint.model_json_schema(), ensure_ascii=False)}"
                f"\n用户设定：\n{prompt}"
            ), expected_output="符合 NovelBlueprint JSON Schema 的完整小说蓝图", agent=agent)
            raw = str(Crew(agents=[agent], tasks=[task], process=Process.sequential,
                           verbose=False).kickoff())
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return NovelBlueprint.model_validate_json(raw), None
        except Exception as exc:
            fallback = NovelBlueprint(
                project=NovelProject(
                    genre=values["genre"], setting=values["setting"], tone=values["tone"],
                    target_length=values["length"], protagonist_goal=values["goal"],
                    premise=values["protagonist"],
                ),
                world=WorldSetting(
                    summary="基础资料已收集，等待补全世界历史、地理和核心规则。",
                    era=values["setting"],
                ),
                power_system=PowerSystem(name="待细化", source=values["power_system"]),
                cheat=CheatSystem(name="待细化", cheat_type=values["cheat"]),
                characters=[Character(
                    name=values["protagonist"][:160], role="主角", summary=values["protagonist"],
                    goal=values["goal"],
                )],
                outline=NovelOutline(
                    summary="完整大纲尚未生成，请在模型连接恢复后重新生成。",
                    central_conflict=values["goal"],
                ),
            )
            return fallback, f"生成暂不可用：{type(exc).__name__}"
