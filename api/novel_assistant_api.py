"""小说设定助手 API。"""

from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.worldbuilding_agent import ConversationState, WorldbuildingAssistant, WorldbuildingGenerationError
from api.generation_stream import generation_response
from api.account_api import CurrentAccount

router = APIRouter(prefix="/novel-assistant")
_sessions: dict[str, ConversationState] = {}
_assistant = WorldbuildingAssistant()


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@router.post("/sessions")
def create_session(user: CurrentAccount) -> dict:
    session_id = uuid4().hex
    state = ConversationState(account_id=user.id)
    _sessions[session_id] = state
    return {"session_id": session_id, "message": _assistant.start(state), "values": state.values,
            "question": state.question}


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, request: MessageRequest, user: CurrentAccount) -> dict:
    state = _sessions.get(session_id)
    if state is None or state.account_id != user.id:
        raise HTTPException(404, "会话不存在或已过期")
    message = request.content.strip()
    if message in {"确认生成", "确认", "开始生成"}:
        try:
            result = await _assistant.confirm(state)
        except WorldbuildingGenerationError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "message": "世界观和大纲已生成完成。",
            "values": state.values,
            "result": result,
            "warning": state.generation_error,
            "persistence": state.persistence,
            "done": True,
            "question": state.question,
        }
    reply = _assistant.receive(state, message)
    return {"message": reply, "values": state.values, "done": state.result is not None,
            "question": state.question}


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
