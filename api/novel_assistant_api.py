"""小说设定助手 API。"""

from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.worldbuilding_agent import ConversationState, WorldbuildingAssistant

router = APIRouter(prefix="/novel-assistant")
_sessions: dict[str, ConversationState] = {}
_assistant = WorldbuildingAssistant()


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@router.post("/sessions")
def create_session() -> dict:
    session_id = uuid4().hex
    state = ConversationState()
    _sessions[session_id] = state
    return {"session_id": session_id, "message": _assistant.start(state), "values": state.values}


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, request: MessageRequest) -> dict:
    state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(404, "会话不存在或已过期")
    message = request.content.strip()
    if message in {"确认生成", "确认", "开始生成"}:
        try:
            result = await _assistant.confirm(state)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "message": "世界观和大纲已生成完成。",
            "values": state.values,
            "result": result,
            "warning": state.generation_error,
            "persistence": state.persistence,
            "done": True,
        }
    reply = _assistant.receive(state, message)
    return {"message": reply, "values": state.values, "done": False}


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(404, "会话不存在或已过期")
    return {
        "values": state.values,
        "history": state.history,
        "result": state.result,
        "warning": state.generation_error,
        "persistence": state.persistence,
        "done": state.result is not None and not state.confirmation_lock.locked(),
    }
