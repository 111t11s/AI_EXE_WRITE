"""Helpers for consuming OpenAI-compatible streamed chat responses."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextvars import ContextVar
from time import monotonic
from typing import Any

from core.logging import get_logger


logger = get_logger("llm_streaming")
GENERATION_EVENTS: ContextVar[Callable[[str, dict], None] | None] = ContextVar("generation_events", default=None)


def emit_generation_event(event: str, **data) -> None:
    listener = GENERATION_EVENTS.get()
    if listener is not None:
        listener(event, data)


def has_generation_listener() -> bool:
    return GENERATION_EVENTS.get() is not None

def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _message_parts(message: Any) -> tuple[str, str]:
    # Gateways use either name for the separate reasoning channel. Keep it
    # separate from content so it cannot corrupt the JSON we validate and save.
    thinking = _value(message, "reasoning_content") or _value(message, "reasoning", "")
    content = _value(message, "content", "")
    return (thinking if isinstance(thinking, str) else "",
            content if isinstance(content, str) else "")


def _delta_parts(chunk: Any) -> tuple[str, str]:
    choices = _value(chunk, "choices", ()) or ()
    if not choices:
        return "", ""
    delta = _value(choices[0], "delta")
    return _message_parts(delta)


def collect_chat_text(response: Any) -> str:
    """Collect a normal completion or an iterable of streamed chunks."""
    original = response
    started = monotonic()
    last_logged = started
    characters = 0
    content_chunks = 0
    thinking_characters = 0
    thinking_chunks = 0

    def record(text: str) -> str:
        nonlocal last_logged, characters, content_chunks
        if text:
            emit_generation_event("delta", text=text)
            characters += len(text)
            content_chunks += 1
            now = monotonic()
            if content_chunks == 1 or now - last_logged >= 15:
                logger.info("模型正文接收中：分片数=%s，字符数=%s，已用时=%.1f秒",
                            content_chunks, characters, now - started)
                last_logged = now
        return text

    def record_thinking(text: str) -> None:
        nonlocal last_logged, thinking_characters, thinking_chunks
        if text:
            emit_generation_event("thinking", text=text)
            thinking_characters += len(text)
            thinking_chunks += 1
            now = monotonic()
            if thinking_chunks == 1 or now - last_logged >= 15:
                logger.info("模型思考接收中：分片数=%s，字符数=%s，已用时=%.1f秒",
                            thinking_chunks, thinking_characters, now - started)
                last_logged = now

    try:
        # Some compatible gateways ignore stream=true. Read that same JSON
        # response rather than issue a second, non-streaming generation.
        http_response = getattr(response, "response", None)
        if http_response is not None and "application/json" in http_response.headers.get("content-type", ""):
            http_response.read()
            response = http_response.json()
        choices = _value(response, "choices")
        if choices:
            message = _value(choices[0], "message")
            thinking, content = _message_parts(message)
            record_thinking(thinking)
            result = record(content)
        elif isinstance(response, Iterable):
            parts = []
            for chunk in response:
                thinking, content = _delta_parts(chunk)
                record_thinking(thinking)
                if content:
                    parts.append(record(content))
            result = "".join(parts)
        else:
            result = ""
        logger.info("模型正文接收完成：分片数=%s，字符数=%s，已用时=%.1f秒",
                    content_chunks, characters, monotonic() - started)
        emit_generation_event("status", message="模型内容已接收完成，正在校验…")
        return result
    finally:
        close = getattr(original, "close", None)
        if callable(close):
            close()
