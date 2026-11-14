"""Probe the configured model with real SSE traffic, without database writes.

Run from the project root: .venv/Scripts/python.exe -u tools/check_model_stream.py
Use --mode sdk to compare the same request through the OpenAI SDK.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("raw", "sdk", "worldbuilding"), default="raw")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--chunk-log-every", type=int, default=1)
    parser.add_argument("--include-usage", action="store_true")
    parser.add_argument("--with-tools", action="store_true")
    parser.add_argument("--output-blueprint", type=Path, help="Save validated worldbuilding JSON for inspection")
    args = parser.parse_args()
    if args.max_tokens < 1 or args.chunk_log_every < 1:
        parser.error("--max-tokens and --chunk-log-every must be positive")
    if args.output_blueprint is not None and args.mode != "worldbuilding":
        parser.error("--output-blueprint requires --mode worldbuilding")
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    os.environ["LOG_FORMAT"] = "%(levelname)s %(message)s"
    settings = get_settings()
    model = os.getenv("MODEL_NAME") or settings.DEEPSEEK_MODEL_NAME
    started = time.monotonic()
    report = {"mode": args.mode, "model": model, "stream": True, "status": "running"}
    texts = []
    pending_text = []
    last_frame = None
    frames = 0
    content_frames = 0

    def safe(value):
        return str(value).replace(settings.API_KEY, "[redacted]") if settings.API_KEY else str(value)

    def output(event, **values):
        print(json.dumps({"event": event, "elapsed_seconds": round(time.monotonic() - started, 3),
                          **values}, ensure_ascii=False), flush=True)

    def consume(chunk):
        nonlocal last_frame, frames, content_frames
        now = time.monotonic()
        if last_frame is not None:
            report["max_gap_seconds"] = round(max(report.get("max_gap_seconds", 0), now - last_frame), 3)
        last_frame = now
        frames += 1
        report.setdefault("first_frame_seconds", round(now - started, 3))
        choices = chunk.get("choices") or []
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}
            text = delta.get("content") or ""
            if text:
                report.setdefault("first_content_seconds", round(now - started, 3))
                texts.append(text)
                pending_text.append(text)
                content_frames += 1
                if content_frames == 1 or content_frames % args.chunk_log_every == 0:
                    output("delta", number=content_frames, text="".join(pending_text))
                    pending_text.clear()
            if choice.get("finish_reason"):
                report["finish_reason"] = choice["finish_reason"]
                output("finish", reason=choice["finish_reason"])
        if chunk.get("error"):
            report["upstream_error"] = safe(json.dumps(chunk["error"], ensure_ascii=False))[:500]
            raise RuntimeError("Upstream reported an error inside the stream")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "请用中文写一段约300字的仙侠世界背景，分成五段，直接输出正文。"}],
        "stream": True,
        "max_tokens": args.max_tokens,
    }
    if args.include_usage:
        payload["stream_options"] = {"include_usage": True}
    if args.with_tools:
        payload["tools"] = [{"type": "function", "function": {
            "name": "read_novel_world", "description": "仅在用户明确提供已有世界观 ID 时查询，当前写作请求不需要调用。",
            "parameters": {"type": "object", "properties": {"record_id": {"type": "integer"}},
                           "required": ["record_id"], "additionalProperties": False},
        }}]
    report.update(include_usage=args.include_usage, with_tools=args.with_tools)
    output("request", mode=args.mode, model=model, read_timeout=settings.REQUEST_TIMEOUT or 120)
    try:
        if not settings.API_KEY.strip():
            raise RuntimeError("API_KEY is not configured")
        if args.mode == "raw":
            url = settings.PROXY_URL.rstrip("/") + "/chat/completions"
            with httpx.Client(timeout=httpx.Timeout(settings.REQUEST_TIMEOUT or 120, connect=15)) as client:
                with client.stream("POST", url, headers={"Authorization": "Bearer " + settings.API_KEY,
                                                       "Accept": "text/event-stream"}, json=payload) as response:
                    report["http_status"] = response.status_code
                    report["content_type"] = response.headers.get("content-type", "")
                    output("headers", status=response.status_code, content_type=report["content_type"])
                    if response.status_code != 200:
                        response.read()
                        report["upstream_error"] = safe(response.text)[:500]
                        response.raise_for_status()
                    if "text/event-stream" not in report["content_type"].lower():
                        response.read()
                        report["response_preview"] = safe(response.text)[:500]
                        raise RuntimeError("Expected SSE but received a non-streaming response")
                    data_lines = []
                    for line in response.iter_lines():
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif not line and data_lines:
                            data = "\n".join(data_lines)
                            data_lines.clear()
                            if data == "[DONE]":
                                report["done_marker"] = True
                                break
                            consume(json.loads(data))
                    if data_lines:
                        data = "\n".join(data_lines)
                        if data == "[DONE]":
                            report["done_marker"] = True
                        else:
                            consume(json.loads(data))
        elif args.mode == "sdk":
            with OpenAI(api_key=settings.API_KEY, base_url=settings.PROXY_URL,
                        timeout=settings.REQUEST_TIMEOUT or 120, max_retries=0) as client:
                with client.chat.completions.create(**payload) as stream:
                    report["http_status"] = stream.response.status_code
                    report["content_type"] = stream.response.headers.get("content-type", "")
                    output("headers", status=report["http_status"], content_type=report["content_type"])
                    for chunk in stream:
                        consume(chunk.model_dump())
        else:
            from crewai.events.event_bus import crewai_event_bus
            from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMStreamChunkEvent
            from agents.worldbuilding_agent import WorldbuildingAssistant

            @crewai_event_bus.on(LLMStreamChunkEvent)
            def on_chunk(source, event):
                if event.chunk and not event.tool_call:
                    consume({"choices": [{"delta": {"content": event.chunk}}]})

            @crewai_event_bus.on(LLMCallCompletedEvent)
            def on_complete(source, event):
                report["model_calls"] = report.get("model_calls", 0) + 1
                consume({"choices": [{"delta": {}, "finish_reason": event.finish_reason}]})

            values = {"genre": "仙侠", "setting": "修仙世界", "power_system": "灵气修炼与境界晋升",
                      "cheat": "神秘传承", "protagonist": "草根少年，坚韧但缺乏资源",
                      "goal": "成长变强，守护亲友", "tone": "热血成长", "length": "短篇（1万字，一卷）"}
            try:
                blueprint = WorldbuildingAssistant._generate_with_crewai(values)
                report["schema_valid"] = True
                report["volumes"] = len(blueprint.outline.volumes)
                report["database_writes"] = False
                report["native_tools_enabled"] = settings.MODEL_NATIVE_TOOLS
                if args.output_blueprint is not None:
                    args.output_blueprint.parent.mkdir(parents=True, exist_ok=True)
                    args.output_blueprint.write_text(blueprint.model_dump_json(indent=2), encoding="utf-8")
                    report["blueprint_path"] = str(args.output_blueprint.resolve())
            finally:
                crewai_event_bus.flush(timeout=5)
        if args.mode != "worldbuilding" and not texts:
            raise RuntimeError("No text deltas received")
        if args.mode != "worldbuilding" and report.get("finish_reason") != "stop":
            raise RuntimeError("Stream did not finish normally")
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["error"] = safe(exc)[:500]
    if pending_text:
        output("delta", number=content_frames, text="".join(pending_text))
    report["total_seconds"] = round(time.monotonic() - started, 3)
    if args.mode == "worldbuilding":
        # Direct SDK generation is observed by llm_streaming's progress logs,
        # not by CrewAI's event bus. Don't misreport it as zero streamed text.
        report["crewai_event_frames"] = frames
    else:
        report.update(frames=frames, content_frames=content_frames, characters=sum(map(len, texts)))
    output("summary", **report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
