"""Expose the novel CRUD API as MCP tools over stdio."""

import os
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from api.novel_contracts import CRUD_PREFIX, CRUD_RESOURCES, CrudResource
from api.novel_schemas import RowId, SessionId, Sequence
from api.writing_schemas import (
    ChapterPlanReceipt, ChapterPlanRequest, DraftReceipt, DraftRequest, OutlineReceipt, OutlineRequest,
    ReviewReport, WritingInput,
    CoreCharacterRequest, SupportingCharacterRequest, CharacterReceipt,
    ConsistencyRequest, ConsistencyReceipt,
    CharacterProgressPage,
    CharacterProgressReceipt,
    BatchDraftRequest, BatchDraftReceipt,
)


DEFAULT_API_BASE_URL = f"http://127.0.0.1:8000{CRUD_PREFIX}"
ERROR_DETAILS = {401: "请先登录或更新账号令牌", 403: "账号无权执行此操作", 404: "记录不存在", 409: "记录或关联冲突", 422: "参数校验失败", 502: "生成失败",
                 503: "服务暂时不可用", 504: "模型响应超时"}
WORKFLOW_TIMEOUT = httpx.Timeout(300, connect=5)


class DeleteResult(BaseModel):
    record_id: RowId | SessionId
    deleted: Literal[True] = True


async def request(
    ctx: Context, method: str, path: str, status: int, *, result_schema=None, **kwargs,
):
    client: httpx.AsyncClient = ctx.request_context.lifespan_context
    try:
        response = await client.request(method, path.lstrip("/"), **kwargs)
    except httpx.TimeoutException:
        raise ToolError("请求小说 API 超时；请先查询当前记录，再决定是否重试写操作。") from None
    except httpx.RequestError:
        raise ToolError("无法访问小说 API，请确认 API 已启动且服务地址正确。") from None

    if response.status_code != status:
        message = ERROR_DETAILS.get(response.status_code, "请求失败")
        if response.status_code in ERROR_DETAILS:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and isinstance(body.get("detail"), str):
                message = body["detail"][:200]
        raise ToolError(f"小说 API 返回 HTTP {response.status_code}：{message}")

    if result_schema is not None:
        try:
            return result_schema.model_validate(response.json())
        except ValueError:
            raise ToolError("小说 API 返回的数据格式与工具定义不符。") from None


def register_tools(server: FastMCP, resource: CrudResource) -> None:
    path, label = resource.path, resource.label
    id_type, filter_schema = resource.id_type, resource.filter_schema
    create_schema, patch_schema = resource.create_schema, resource.patch_schema
    read_schema, page_schema = resource.read_schema, resource.page_schema

    async def list_rows(ctx: Context, filters: filter_schema | None = None) -> page_schema:
        params = filters.model_dump(mode="json", exclude_none=True, exclude_unset=True) if filters else {}
        return await request(ctx, "GET", path, 200, result_schema=page_schema, params=params)

    async def create_row(ctx: Context, payload: create_schema) -> read_schema:
        return await request(ctx, "POST", path, 201, result_schema=read_schema,
                             json=payload.model_dump(mode="json", exclude_unset=True))

    async def read_row(ctx: Context, record_id: id_type) -> read_schema:
        return await request(ctx, "GET", f"{path}/{record_id}", 200, result_schema=read_schema)

    async def update_row(ctx: Context, record_id: id_type, payload: patch_schema) -> read_schema:
        return await request(ctx, "PATCH", f"{path}/{record_id}", 200, result_schema=read_schema,
                             json=payload.model_dump(mode="json", exclude_unset=True))

    async def delete_row(ctx: Context, record_id: id_type) -> DeleteResult:
        await request(ctx, "DELETE", f"{path}/{record_id}", 204)
        return DeleteResult(record_id=record_id)

    world = resource.table_name == "novel_world"
    operations = (
        ("list", list_rows, f"查询{label}列表，可按 filters 筛选和分页。", True, False, True),
        ("create", create_row, f"新增{label}并返回完整记录。" +
         ("设为当前版本时会取消同项目其他版本的当前状态。" if world else ""), False, world, False),
        ("read", read_row, f"按 record_id 读取单条{label}记录。", True, False, True),
        ("update", update_row, f"局部更新{label}。仅提交要改动的字段，可空字段可传 null。", False, True, True),
        ("delete", delete_row, f"删除{label}。按数据库外键规则处理关联记录，可能级联删除。", False, True, True),
    )
    for action, fn, description, read_only, destructive, idempotent in operations:
        server.add_tool(
            fn, name=f"{action}_{resource.table_name}", description=description, structured_output=True,
            annotations=ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                                        idempotentHint=idempotent, openWorldHint=False),
        )


def register_writing_tools(server: FastMCP) -> None:
    @server.tool(description="读取小说中由独立人物进展分析任务验证原句后保存的人物进展。before_chapter_number 排除当前章和未来章节，latest 返回每人最近记录。",
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def list_novel_character_progress(ctx: Context, project_id: RowId, before_chapter_number: Sequence | None = None,
                                           latest: bool = False, limit: int = 100, offset: int = 0) -> CharacterProgressPage:
        params = {"latest": latest, "limit": limit, "offset": offset}
        if before_chapter_number is not None:
            params["before_chapter_number"] = before_chapter_number
        return await request(ctx, "GET", f"writing/projects/{project_id}/character-progress", 200,
                             result_schema=CharacterProgressPage, params=params)

    def annotations(*, read_only: bool = False) -> ToolAnnotations:
        return ToolAnnotations(readOnlyHint=read_only, destructiveHint=False,
                               idempotentHint=False, openWorldHint=True)

    @server.tool(description="按用户指定的大纲版本及多选章节核对剧情、人物和设定是否偏移，返回带原文依据的报告，不修改小说。",
                 annotations=annotations(read_only=True))
    async def check_novel_consistency(ctx: Context, project_id: RowId, payload: ConsistencyRequest) -> ConsistencyReceipt:
        return await request(ctx, "POST", f"writing/projects/{project_id}/consistency", 200,
                             result_schema=ConsistencyReceipt, json=payload.model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="根据当前世界观及项目最新大纲生成主角和重要配角；有大纲时自动读取并绑定各分卷阶段档案，不会覆盖已有角色。",
                 annotations=annotations())
    async def generate_novel_core_characters(ctx: Context, project_id: RowId,
                                             payload: CoreCharacterRequest | None = None) -> CharacterReceipt:
        return await request(ctx, "POST", f"writing/projects/{project_id}/characters/core", 201,
                             result_schema=CharacterReceipt, json=(payload or CoreCharacterRequest()).model_dump(mode="json"),
                             timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="根据指定大纲生成配角；可用 volume_id 指定分卷，reuse_character_ids 勾选前卷配角安排新戏份，原档案保持不变。",
                 annotations=annotations())
    async def generate_novel_supporting_characters(ctx: Context, outline_id: RowId,
                                                   payload: SupportingCharacterRequest | None = None) -> CharacterReceipt:
        return await request(ctx, "POST", f"writing/outlines/{outline_id}/characters/supporting", 201,
                             result_schema=CharacterReceipt,
                             json=(payload or SupportingCharacterRequest()).model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="为项目生成一版新大纲，并保存分卷和剧情节点。需要已有当前世界观。",
                 annotations=annotations())
    async def generate_novel_outline(ctx: Context, project_id: RowId,
                                     payload: OutlineRequest | None = None) -> OutlineReceipt:
        return await request(ctx, "POST", f"writing/projects/{project_id}/outline", 201, result_schema=OutlineReceipt,
                             json=(payload or OutlineRequest()).model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="读取指定版本原大纲、角色和世界观，按要求重写并保存为新版本；保留原大纲与已有章节。",
                 annotations=annotations())
    async def rewrite_novel_outline(ctx: Context, outline_id: RowId, payload: OutlineRequest) -> OutlineReceipt:
        return await request(ctx, "POST", f"writing/outlines/{outline_id}/rewrite", 201, result_schema=OutlineReceipt,
                             json=payload.model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="基于现有正文和设定重写完整章节，成功后替换正文并设为草稿；期间章节变更则拒绝覆盖。",
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
    async def rewrite_novel_chapter(ctx: Context, chapter_id: RowId, payload: DraftRequest) -> DraftReceipt:
        return await request(ctx, "POST", f"writing/chapters/{chapter_id}/rewrite", 200, result_schema=DraftReceipt,
                             json=payload.model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="将指定分卷拆成计划章节，从 start_chapter 开始编号；已有章号不会被覆盖。",
                 annotations=annotations())
    async def plan_novel_chapters(ctx: Context, volume_id: RowId,
                                 payload: ChapterPlanRequest | None = None) -> ChapterPlanReceipt:
        return await request(ctx, "POST", f"writing/volumes/{volume_id}/chapter-plan", 201,
                             result_schema=ChapterPlanReceipt, json=(payload or ChapterPlanRequest()).model_dump(mode="json"),
                             timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="按计划生成指定章节的正文初稿，仅保存到尚无正文的 planned 章节。",
                 annotations=annotations())
    async def draft_novel_chapter(ctx: Context, chapter_id: RowId,
                                 payload: DraftRequest | None = None) -> DraftReceipt:
        return await request(ctx, "POST", f"writing/chapters/{chapter_id}/draft", 200, result_schema=DraftReceipt,
                             json=(payload or DraftRequest()).model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="按章号顺序批量生成已有规划章节的正文；每章正文保存并完成独立人物进展分析后才继续下一章，最多 20 章。",
                 annotations=annotations())
    async def batch_draft_novel_chapters(ctx: Context, project_id: RowId,
                                         payload: BatchDraftRequest) -> BatchDraftReceipt:
        return await request(ctx, "POST", f"writing/projects/{project_id}/chapters/batch-draft", 200,
                             result_schema=BatchDraftReceipt, json=payload.model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="分析已保存章节中的人物行动、状态和关系变化，保存逐字正文依据；不会续写或修改正文。",
                 annotations=annotations())
    async def analyze_novel_character_progress(ctx: Context, chapter_id: RowId,
                                               payload: WritingInput | None = None) -> CharacterProgressReceipt:
        return await request(ctx, "POST", f"writing/chapters/{chapter_id}/character-progress", 200,
                             result_schema=CharacterProgressReceipt,
                             json=(payload or WritingInput()).model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)

    @server.tool(description="审读现有章节，返回连续性问题、文中依据和修改建议。不会修改正文或章节状态。",
                 annotations=annotations(read_only=True))
    async def review_novel_chapter(ctx: Context, chapter_id: RowId,
                                  payload: WritingInput | None = None) -> ReviewReport:
        return await request(ctx, "POST", f"writing/chapters/{chapter_id}/review", 200, result_schema=ReviewReport,
                             json=(payload or WritingInput()).model_dump(mode="json"), timeout=WORKFLOW_TIMEOUT)


def create_server(api_base_url: str, *, transport: httpx.AsyncBaseTransport | None = None,
                  auth_token: str | None = None) -> FastMCP:
    base_url = httpx.URL(api_base_url)
    if (base_url.scheme not in {"http", "https"} or not base_url.host
            or base_url.userinfo or base_url.query or base_url.fragment):
        raise ValueError("AITXT_API_BASE_URL 必须是无凭据、查询参数或片段的 HTTP(S) 地址")

    @asynccontextmanager
    async def lifespan(_server):
        token = auth_token if auth_token is not None else os.getenv("AITXT_AUTH_TOKEN", "")
        async with httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(30, connect=5), trust_env=False, transport=transport,
            headers={"Authorization": f"Bearer {token}"} if token else {},
        ) as client:
            yield client

    server = FastMCP("aitxt-novel", lifespan=lifespan, instructions="通过 AITXT 小说 API 管理小说项目和设定数据。")
    for resource in CRUD_RESOURCES:
        register_tools(server, resource)
    register_writing_tools(server)
    return server


def main() -> None:
    from core.logging import configure_external_logging

    server = create_server(os.getenv("AITXT_API_BASE_URL", DEFAULT_API_BASE_URL))
    configure_external_logging()
    server.run(transport="stdio")
