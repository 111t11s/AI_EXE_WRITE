"""Insert and verify real MCP sample records; leave them for manual cleanup.

Run from the project root: .venv/Scripts/python.exe -u tools/check_mcp_persistence.py
Each run creates a NEW project and world in the configured database.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    if not os.getenv("AITXT_AUTH_TOKEN"):
        print("请先登录账号，并将 access_token 设置到 AITXT_AUTH_TOKEN 后再验证入库。")
        return 2
    os.environ["LOG_FORMAT"] = "%(levelname)s %(message)s"

    import uvicorn
    from sqlalchemy import select

    from agents.mcp.worldbuilding import call_novel_tool, novel_session
    from api.novel_contracts import CRUD_PREFIX
    from api.novel_crud_api import response_row, tables
    from api.novel_schemas import ProjectCreate, WorldCreate
    from db.connection import get_engine, legacy_json_columns
    from main import app

    marker = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    report_path = ROOT / "outputs" / "mcp-verification" / f"{marker}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "marker": marker,
        "status": "running",
        "content_source": "explicit_sample_not_model_generated",
        "transport": "MCP stdio -> local HTTP API -> configured MySQL",
        "records": {},
        "cleanup_performed": False,
    }

    def checkpoint(event: str, **values) -> None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)

    def database_read(name: str, record_id: int) -> dict:
        relation = tables[name]
        with get_engine().connect() as connection:
            row = connection.execute(select(relation).where(relation.c.id == record_id)).mappings().one()
            return response_row(row, legacy_json_columns(connection, name))

    def verify(expected: dict, actual: dict, label: str) -> None:
        differences = [key for key, value in expected.items() if actual.get(key) != value]
        if differences:
            raise RuntimeError(f"{label}: mismatched fields: {', '.join(differences)}")

    async def exercise_mcp() -> None:
        project_payload = ProjectCreate(
            title=f"MCP入库验证-{marker}",
            genre="仙侠",
            setting="灵气复苏后的云泽大陆，宗门与凡人城池共存。",
            tone="热血成长",
            target_length="短篇（1万字，一卷）",
            premise=f"MCP真实入库验证样例，用户后续清理。标记：{marker}",
            protagonist_goal="修复家乡灵脉，守护亲友。",
        ).model_dump(mode="json")
        async with novel_session() as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            required = {"create_novel_project", "create_novel_world", "read_novel_project", "read_novel_world"}
            if not required <= names:
                raise RuntimeError("Required MCP tools are missing")
            checkpoint("mcp_initialized", available_tools=len(names))

            project = await call_novel_tool(session, "create_novel_project", {"payload": project_payload})
            report["records"]["novel_project"] = {"id": project["id"], "payload": project_payload}
            checkpoint("inserted", table="novel_project", id=project["id"], title=project["title"])
            verify(project_payload, project, "project create response")

            world_payload = WorldCreate(
                project_id=project["id"],
                title=f"云泽大陆-MCP验证-{marker}",
                summary="云泽大陆灵脉逐渐枯竭，少年凭残缺传承寻找修复灵脉的方法。",
                era="灵衰历三百年",
                history="百年前封印灾变耗尽灵气，宗门自此争夺剩余灵脉。",
                rules="修炼需消耗灵气；越阶施法损伤经脉；灵脉恢复必须付出等量资源。",
                cultures="城池共祭山川，宗门每十年举行灵脉盟会。",
            ).model_dump(mode="json")
            world = await call_novel_tool(session, "create_novel_world", {"payload": world_payload})
            report["records"]["novel_world"] = {"id": world["id"], "payload": world_payload}
            checkpoint("inserted", table="novel_world", id=world["id"], title=world["title"])
            verify(world_payload, world, "world create response")

        # A fresh MCP connection and separate SQL transactions check committed data.
        async with novel_session() as session:
            for table_name, record in report["records"].items():
                readback = await call_novel_tool(session, f"read_{table_name}", {"record_id": record["id"]})
                verify({"id": record["id"], **record["payload"]}, readback, f"{table_name} MCP read")
                record["mcp_read_verified"] = True
                checkpoint("mcp_read_verified", table=table_name, id=record["id"])

        for table_name, record in report["records"].items():
            readback = await asyncio.to_thread(database_read, table_name, record["id"])
            verify({"id": record["id"], **record["payload"]}, readback, f"{table_name} MySQL read")
            record["mysql_read_verified"] = True
            checkpoint("mysql_read_verified", table=table_name, id=record["id"])

    listener = socket.socket()
    server = None
    thread = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        os.environ["AITXT_API_BASE_URL"] = f"http://127.0.0.1:{listener.getsockname()[1]}{CRUD_PREFIX}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Temporary local API failed to start")
            time.sleep(0.05)
        checkpoint("api_started", report_path=str(report_path))
        asyncio.run(exercise_mcp())
        report["status"] = "passed"
    except Exception as exc:
        # Keep raw connection exceptions out of output: they can contain credentials.
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        checkpoint("failed", error_type=type(exc).__name__)
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        listener.close()
        get_engine.cache_clear()
        checkpoint("summary", status=report["status"],
                   records={name: value["id"] for name, value in report["records"].items()},
                   cleanup_performed=False, report_path=str(report_path))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
