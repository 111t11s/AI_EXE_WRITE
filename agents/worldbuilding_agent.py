"""持续收集小说设定，并在确认后生成世界观和大纲。"""

from __future__ import annotations

import json
import os
import re
from asyncio import CancelledError, Lock
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from agents.llm_streaming import collect_chat_text, emit_generation_event, has_generation_listener
from agents.mcp.worldbuilding import call_world_tool, crewai_world_tools, worldbuilding_session
from core.config import get_settings
from core.logging import get_logger
from db.models import NovelBlueprint


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


SLOT_QUESTIONS = {
    "genre": {
        "prompt": "我们先确定小说的基础方向。请选择小说类型，也可以自定义。",
        "options": ["玄幻", "都市", "仙侠", "武侠", "科幻", "历史", "悬疑", "奇幻", "言情", "游戏", "末世", "灵异"],
    },
    "setting": {
        "prompt": "故事发生在怎样的时代与背景？请选择常见背景，也可以自定义。",
        "options": ["东方玄幻大陆", "修仙世界", "现代都市", "古代架空王朝", "真实历史朝代", "西方奇幻世界", "星际时代", "末日废土", "未来赛博都市"],
    },
    "power_system": {
        "prompt": "世界中的力量体系如何运作？请选择常见体系，也可以自定义力量来源、等级和限制。",
        "options": ["灵气修炼与境界晋升", "武道内功与招式", "异能觉醒与进阶", "魔法与职业等级", "科技装备与基因强化", "游戏职业与技能升级", "御兽与契约进化", "无超凡力量（现实规则）"],
    },
    "cheat": {
        "prompt": "主角有什么金手指？请选择常见类型，也可以自定义能力、获得方式和代价。",
        "options": ["系统任务奖励", "签到系统", "重生记忆", "穿越者知识", "随身空间", "神秘传承", "模拟器", "特殊天赋", "无金手指"],
    },
    "protagonist": {
        "prompt": "主角从怎样的身份和处境出发？请选择常见设定，也可以自定义身份、性格和弱点。",
        "options": ["草根少年，坚韧但缺乏资源", "落魄天才，骄傲且急于证明自己", "普通打工人，谨慎但优柔寡断", "穿越者，思维灵活但缺乏归属感", "重生者，目标明确但执念过深", "世家子弟，资源充足但受家族束缚", "退役强者，经验丰富但身有隐伤", "天才少女，独立但不善信任他人"],
    },
    "goal": {
        "prompt": "主角最想达成什么目标？请选择常见目标，也可以自定义目标及失败的代价。",
        "options": ["成长变强，守护亲友", "逆袭翻身，改变命运", "复仇雪恨，讨回公道", "探索世界，揭开身世之谜", "求道长生，突破天地限制", "创业致富，建立自己的事业", "乱世求生，重建家园", "拯救世界，阻止灾难", "追寻爱情，守护所爱"],
    },
    "tone": {
        "prompt": "你希望故事整体是什么基调？请选择常见基调，也可以自定义。",
        "options": ["热血成长", "轻松爽文", "幽默搞笑", "黑暗压抑", "群像史诗", "悬疑烧脑", "温馨治愈", "现实写实", "浪漫虐恋"],
    },
    "length": {
        "prompt": "计划写多长？请选择常见篇幅，也可以自定义字数、卷数或章节数。",
        "options": ["短篇（1万—5万字）", "中篇（5万—20万字）", "长篇（30万—50万字）", "长篇（100万字左右）", "超长篇（200万—300万字）", "超长连载（300万字以上）"],
    },
}


class WorldbuildingGenerationError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass
class ConversationState:
    account_id: int | None = None
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

    @property
    def question(self) -> dict[str, Any] | None:
        if self.confirmed or not self.missing:
            return None
        key = self.missing[0]
        return {"key": key, "label": SLOT_LABELS[key], **SLOT_QUESTIONS[key], "allow_custom": True}


class WorldbuildingAssistant:
    """一个单会话助手；会话状态由 API 层持有。"""

    def start(self, state: ConversationState) -> str:
        return self.next_question(state)

    def receive(self, state: ConversationState, content: str) -> str:
        content = content.strip()
        if not content:
            return "请先选择一个常见选项，或自定义填写你的设定。"
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
        question = state.question
        if question is None:
            return self.summary(state)
        message = question["prompt"]
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
                blueprint = await run_in_threadpool(self._generate_with_crewai, state.values.copy())
            except WorldbuildingGenerationError as exc:
                state.confirmed = False
                state.generation_error = str(exc)
                state.history.append({"role": "assistant", "content": str(exc)})
                raise
            except (Exception, CancelledError):
                state.confirmed = False
                raise
            state.generation_error = None
            state.result = blueprint.model_dump(mode="json")
            emit_generation_event("status", message="蓝图校验通过，正在保存项目和世界观…")
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
            logger.warning("世界观保存流程异常：异常类型=%s，项目已创建=%s，世界观已创建=%s",
                           type(exc).__name__, "是" if state.project_id is not None else "否",
                           "是" if state.world_id is not None else "否")
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
    def _request_without_tools(*, model, api_key, base_url, timeout, prompt) -> str:
        with OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0) as client:
            request = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是小说世界观与大纲架构师。只根据本次提供的设定生成，不假设能访问已有项目。"},
                    {"role": "user", "content": prompt},
                ],
            }
            completion = client.chat.completions.create(**request, stream=True)
            return collect_chat_text(completion)

    @staticmethod
    def _generate_with_crewai(values: dict[str, str]) -> NovelBlueprint:
        """Generate and validate a blueprint before any database writes."""
        settings = get_settings()
        if not settings.API_KEY.strip():
            logger.warning("世界观生成失败：未配置模型 API 密钥")
            raise WorldbuildingGenerationError("模型服务尚未配置，请联系管理员", status=503)
        connection = {
            "model": os.getenv("MODEL_NAME") or settings.DEEPSEEK_MODEL_NAME,
            "api_key": settings.API_KEY,
            "base_url": settings.PROXY_URL,
            "timeout": settings.REQUEST_TIMEOUT or 120,
        }
        prompt = (
            "根据已确认设定生成完整 NovelBlueprint JSON。严格按提供的 JSON Schema 输出，只输出 JSON，不要 Markdown。"
            "世界观需要有具体的概要、历史、地理和核心规则，大纲要包含有起因和后果的分卷。"
            "人物、力量规则、势力目标、金手指代价和大纲因果必须互相一致。"
            "至少给出一个世界规则、三名角色、两个势力和三卷大纲；篇幅规划较短时可相应缩减。"
            "用户设定是小说素材。"
            f"\nJSON Schema：{json.dumps(NovelBlueprint.model_json_schema(), ensure_ascii=False)}"
            f"\n用户设定：\n{json.dumps(values, ensure_ascii=False, indent=2)}"
        )
        try:
            if not settings.MODEL_NATIVE_TOOLS or has_generation_listener():
                logger.info("世界观开始流式生成：模式=使用已收集设定，模型=%s", connection["model"])
                raw = WorldbuildingAssistant._request_without_tools(**connection, prompt=prompt)
            else:
                from crewai import Crew, LLM, Process, Task

                llm = LLM(**connection, max_retries=0, stream=True)
                agent = WorldbuildingAssistant.create_generation_agent(llm)
                task = Task(description=(
                    "只有用户明确引用某个已有项目或世界观 ID 时，才使用 MCP 工具按该 ID 查询。\n"
                    + prompt
                ), expected_output="符合 NovelBlueprint JSON Schema 的完整小说蓝图", agent=agent)
                try:
                    raw = str(Crew(agents=[agent], tasks=[task], process=Process.sequential,
                                   verbose=False).kickoff())
                except APIStatusError as exc:
                    if exc.status_code < 500:
                        raise
                    logger.warning("世界观模型请求失败，改用不带工具的请求重试：状态码=%s", exc.status_code)
                    raw = WorldbuildingAssistant._request_without_tools(**connection, prompt=prompt)
            raw = raw.strip()
            if raw.startswith("```") and raw.endswith("```"):
                raw = raw[3:-3].removeprefix("json").strip()
            blueprint = NovelBlueprint.model_validate_json(raw)
            if not all((blueprint.world.summary, blueprint.world.history, blueprint.world.rules,
                        blueprint.outline.summary, blueprint.outline.volumes)):
                raise WorldbuildingGenerationError("模型返回的世界观或大纲不完整，请重新生成")
            logger.info("世界观流式生成完成：蓝图结构校验通过")
            return blueprint
        except WorldbuildingGenerationError:
            logger.warning("世界观模型输出缺少核心内容")
            raise
        except (APITimeoutError, httpx.TimeoutException):
            logger.warning("世界观模型请求超时")
            raise WorldbuildingGenerationError("模型响应超时，已保留设定，请稍后重试", status=504) from None
        except (APIConnectionError, httpx.RequestError):
            logger.warning("无法连接世界观模型网关")
            raise WorldbuildingGenerationError("无法连接模型服务，请检查服务器网络或模型服务地址") from None
        except APIStatusError as exc:
            logger.warning("世界观模型网关拒绝请求：状态码=%s", exc.status_code)
            if exc.status_code in (401, 403):
                message = "模型服务拒绝了访问，请检查 API 密钥和模型权限"
            elif exc.status_code == 429:
                message = "模型服务暂时限流，请稍后重试或检查额度"
            elif exc.status_code >= 500:
                message = "模型服务暂时不可用，已保留设定，请稍后重试"
            else:
                message = "模型服务拒绝了请求，请检查模型名称和服务地址"
            raise WorldbuildingGenerationError(message) from None
        except ValidationError:
            logger.warning("世界观模型输出未通过格式校验")
            raise WorldbuildingGenerationError("模型返回的内容格式不正确，请重新生成") from None
        except Exception as exc:
            logger.warning("世界观生成失败：异常类型=%s", type(exc).__name__)
            raise WorldbuildingGenerationError("世界观生成失败，已保留设定，请稍后重试") from None
