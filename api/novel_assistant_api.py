"""小说设定助手 API。"""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from db.models import WorldTemplate

from agents.worldbuilding_agent import SLOT_LABELS, ConversationState, WorldbuildingAssistant, WorldbuildingGenerationError
from api.generation_stream import generation_response
from api import generation_jobs
from api.account_api import CurrentAccount
from api.writing_schemas import OutlineTheme
from agents.outline_skills import OutlineSkillError, outline_skill_selection

router = APIRouter(prefix="/novel-assistant")
_sessions: dict[str, ConversationState] = {}
_assistant = WorldbuildingAssistant()


def skill_selection(genre):
    try:
        return outline_skill_selection(genre)
    except OutlineSkillError as exc:
        raise HTTPException(503, str(exc)) from None


@router.get("/outline-skills")
def get_outline_skills(user: CurrentAccount, genre: str = Query(default="", max_length=200)) -> dict:
    return skill_selection(genre)


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    outline_theme: OutlineTheme = "development"
    # 可选模型配置 ID（GET /api/v1/models）；省略时使用主模型。
    model: str | None = Field(default=None, max_length=40)
    world_template: dict[str, str] | None = None


class WorldTemplateParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=30000)
    model: str | None = Field(default=None, max_length=40)


class NovelFromTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    world_template: dict[str, str]
    title: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=40)


@router.post("/from-template/tasks", status_code=202, summary="按结构化世界观模板新建小说")
async def start_novel_from_template(request: NovelFromTemplateRequest, user: CurrentAccount):
    try:
        template = WorldTemplate.model_validate(request.world_template)
    except ValidationError:
        raise HTTPException(422, "世界观模板字段不完整或格式不正确") from None
    missing = [label for label, value in (
        ("核心概念", template.core_concept), ("最特殊之处", template.special_feature),
        ("类型", template.genre), ("基调", template.tone), ("时代", template.era),
        ("主要地域", template.main_regions), ("文明/科技水平", template.civilization_level),
        ("世界允许", template.allowed), ("世界禁止", template.forbidden),
        ("超凡力量或核心技术", template.power_core), ("使用条件", template.use_conditions),
        ("消耗或成本", template.costs), ("失败后果", template.failure_consequences),
        ("反制方式", template.countermeasures)) if not value.strip()]
    if missing:
        raise HTTPException(422, "世界观模板缺少：" + "、".join(missing))
    session_id = uuid4().hex
    values = {
        "genre": template.genre,
        "setting": f"{template.era}；{template.main_regions}",
        "power_system": template.power_core,
        "cheat": template.special_feature,
        "protagonist": "由当前世界观自动生成的男女主",
        "goal": f"在{template.core_concept}的世界中掌握核心规则并解决主要冲突",
        "tone": template.tone,
        "length": "长篇",
    }
    state = ConversationState(account_id=user.id, values=values)
    _sessions[session_id] = state
    payload = template.model_dump(mode="json")
    return generation_jobs.store.start(
        account_id=user.id, key=f"worldbuilding:template:{session_id}", kind="worldbuilding",
        title="按世界观模板生成小说", resource_id=session_id,
        operation=lambda: _assistant.confirm(state, "development", request.model, payload, request.title)).summary()


@router.post("/world-template/parse/tasks", status_code=202, summary="后台 AI 拆解世界观文本")
async def parse_world_template_task(request: WorldTemplateParseRequest, user: CurrentAccount):
    async def operation():
        return {"template": await run_in_threadpool(_assistant.parse_world_template, request.text, request.model)}
    return generation_jobs.store.start(
        account_id=user.id, key=f"world-template:{uuid4().hex}", kind="world_template",
        title="AI 拆解世界观文本", operation=operation).summary()


@router.post("/world-template/parse", summary="把整段世界观文本拆解到模板字段")
async def parse_world_template(request: WorldTemplateParseRequest, user: CurrentAccount) -> dict:
    try:
        template = await run_in_threadpool(_assistant.parse_world_template, request.text, request.model)
    except WorldbuildingGenerationError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    return {"template": template}


class PrefillRequest(BaseModel):
    """用热点选题推荐预填问答设定；只接受既有设定项。"""

    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(default_factory=dict, max_length=8)


@router.post("/sessions")
def create_session(user: CurrentAccount) -> dict:
    session_id = uuid4().hex
    state = ConversationState(account_id=user.id)
    _sessions[session_id] = state
    return {"session_id": session_id, "message": _assistant.start(state), "values": state.values,
            "question": state.question, "outline_skill": skill_selection(None),
            "outline_theme": state.outline_theme}


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, request: MessageRequest, user: CurrentAccount) -> dict:
    state = _sessions.get(session_id)
    if state is None or state.account_id != user.id:
        raise HTTPException(404, "会话不存在或已过期")
    message = request.content.strip()
    if message in {"确认生成", "确认", "开始生成"}:
        try:
            result = await _assistant.confirm(state, request.outline_theme, request.model, request.world_template)
        except WorldbuildingGenerationError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "message": "世界观、第一卷大纲和男女主已生成完成。",
            "values": state.values,
            "result": result,
            "warning": state.generation_error,
            "persistence": state.persistence,
            "done": True,
            "question": state.question,
            "outline_skill": skill_selection(state.values.get("genre")),
            "outline_theme": state.outline_theme,
        }
    reply = _assistant.receive(state, message)
    return {"message": reply, "values": state.values, "done": state.result is not None,
            "question": state.question, "outline_skill": skill_selection(state.values.get("genre")),
            "outline_theme": state.outline_theme}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, user: CurrentAccount) -> dict:
    state = _sessions.get(session_id)
    if state is None or state.account_id != user.id:
        raise HTTPException(404, "会话不存在或已过期")
    return {
        "values": state.values,
        "question": state.question,
        "history": state.history,
        "result": state.result,
        "warning": state.generation_error,
        "persistence": state.persistence,
        "done": state.result is not None and not state.confirmation_lock.locked(),
        "outline_skill": skill_selection(state.values.get("genre")),
        "outline_theme": state.outline_theme,
    }


@router.post("/sessions/{session_id}/prefill", summary="用热点选题推荐预填基础设定")
def prefill_session(session_id: str, request: PrefillRequest, user: CurrentAccount) -> dict:
    state = _sessions.get(session_id)
    if state is None or state.account_id != user.id:
        raise HTTPException(404, "会话不存在或已过期")
    if state.result is not None or state.confirmation_lock.locked():
        raise HTTPException(409, "当前会话已开始生成，请先点击「开始新对话」")
    for key, value in request.values.items():
        if key not in SLOT_LABELS:
            raise HTTPException(422, f"未知的设定项：{key}")
        text = str(value).strip()
        if not text:
            continue
        if len(text) > 500:
            raise HTTPException(422, f"{SLOT_LABELS[key]}内容过长")
        state.values[key] = text
    message = _assistant.next_question(state)
    return {
        "message": message, "values": state.values, "question": state.question, "done": False,
        "outline_skill": skill_selection(state.values.get("genre")),
        "outline_theme": state.outline_theme,
    }


@router.post("/sessions/{session_id}/messages/stream")
async def stream_message(session_id: str, request: MessageRequest, user: CurrentAccount):
    state = _sessions.get(session_id)
    if state is None or state.account_id != user.id:
        raise HTTPException(404, "会话不存在或已过期")
    if request.content.strip() not in {"确认生成", "确认", "开始生成"}:
        raise HTTPException(400, "流式接口仅用于确认生成")
    if state.missing:
        raise HTTPException(400, "请先完成基础设定")
    return generation_response(lambda: send_message(session_id, request, user), message="正在连接模型，准备生成世界观…")


@router.post("/sessions/{session_id}/messages/tasks", status_code=202)
async def start_worldbuilding(session_id: str, request: MessageRequest, user: CurrentAccount):
    state = _sessions.get(session_id)
    if state is None or state.account_id != user.id:
        raise HTTPException(404, "会话不存在或已过期")
    if request.content.strip() not in {"确认生成", "确认", "开始生成"} or state.missing:
        raise HTTPException(400, "请先完成基础设定并确认生成")
    return generation_jobs.store.start(account_id=user.id, key=f"worldbuilding:{session_id}", kind="worldbuilding",
        title="生成世界观与大纲", resource_id=session_id,
        operation=lambda: send_message(session_id, request, user)).summary()
