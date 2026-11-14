"""MCP tools used by the worldbuilding assistant."""

import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agents.mcp.server import DEFAULT_API_BASE_URL
from core.identity import AUTH_TOKEN


WORLD_QUERY_TOOLS = ("list_novel_world", "read_novel_world")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def server_parameters() -> StdioServerParameters:
    token = AUTH_TOKEN.get() or os.getenv("AITXT_AUTH_TOKEN", "")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents.mcp"],
        env={
            "PYTHONPATH": str(PROJECT_ROOT),
            "AITXT_API_BASE_URL": os.getenv("AITXT_API_BASE_URL", DEFAULT_API_BASE_URL),
            "AITXT_AUTH_TOKEN": token,
        },
    )


def crewai_tools(names: tuple[str, ...]):
    """Register the chosen data tools with CrewAI's native MCP adapter."""
    from crewai.mcp import MCPServerStdio, create_static_tool_filter

    params = server_parameters()
    return MCPServerStdio(
        command=params.command, args=params.args, env=params.env,
        tool_filter=create_static_tool_filter(allowed_tool_names=list(names)),
    )


def crewai_world_tools():
    return crewai_tools(WORLD_QUERY_TOOLS)


@asynccontextmanager
async def novel_session():
    async with stdio_client(server_parameters()) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=40)) as session:
            await session.initialize()
            yield session


worldbuilding_session = novel_session


class NovelToolError(RuntimeError):
    def __init__(self, name: str, status: int = 503):
        super().__init__(f"{name} 调用失败")
        self.status = status


async def call_novel_tool(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    if result.isError or not isinstance(result.structuredContent, dict):
        message = next((block.text for block in result.content if block.type == "text"), "")
        match = re.search(r"小说 API 返回 HTTP (401|403|404|409|422)\b", message)
        raise NovelToolError(name, int(match[1]) if match else 503)
    return result.structuredContent


call_world_tool = call_novel_tool
