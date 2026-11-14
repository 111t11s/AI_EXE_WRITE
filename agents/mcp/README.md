# 小说数据 MCP 工具

本服务通过 stdio 提供 15 个资源的 75 个 CRUD 工具和 4 个写作工作流工具。工具调用现有 FastAPI 接口，沿用它的字段校验、数据库配置和外键规则。

先从项目根目录启动 API：

```powershell
.\.venv\Scripts\python.exe -X utf8 main.py
```

然后让 MCP 客户端启动 `python -m agents.mcp`。以下是使用 `mcpServers` 配置的客户端示例，需替换 Python 和项目路径：

```json
{
  "mcpServers": {
    "aitxt-novel": {
      "command": "C:/path/to/AITXT/.venv/Scripts/python.exe",
      "args": ["-m", "agents.mcp"],
      "env": {
        "PYTHONPATH": "C:/path/to/AITXT",
        "AITXT_API_BASE_URL": "http://127.0.0.1:8000/api/v1",
        "AITXT_AUTH_TOKEN": "替换为该账号登录返回的access_token"
      }
    }
  }
}
```

`AITXT_API_BASE_URL` 可省略，默认使用示例中的地址。API 地址变更时，配置值应包含 `/api/v1`。MCP 进程无需配置数据库密码。

所有数据接口均要求登录。外部 MCP 客户端先通过 `POST /api/v1/auth/login`（JSON：`username`、`password`）取得 `access_token`，在 MCP 进程环境中设置 `AITXT_AUTH_TOKEN`；过期后重新登录并更新令牌。不要把令牌提交到仓库。工具仅能读写该账号的小说，新项目自动归属该账号。应用内部启动的 MCP 会自动继承当前请求的账号令牌，继续执行相同权限校验，无需配置共享账号。

工具名使用 `{操作}_{表名}`，例如：

| 工具 | 参数示例 |
|---|---|
| `list_novel_chapter` | `{"filters":{"project_id":12,"status":"draft","limit":10}}` |
| `create_novel_chapter` | `{"payload":{"project_id":12,"chapter_number":1,"title":"开篇"}}` |
| `read_novel_chapter` | `{"record_id":34}` |
| `update_novel_chapter` | `{"record_id":34,"payload":{"volume_id":null}}` |
| `delete_novel_chapter` | `{"record_id":34}` |

`list_*` 可省略 `filters`；`update_*` 只发送传入的字段，显式 `null` 可解除允许为空的关联。删除成功返回 `{"record_id":34,"deleted":true}`。删除会遵循数据库外键规则，可能级联删除关联记录。

世界观生成默认将已收集的设定直接交给模型流式生成；写作接口先通过 MCP 读取项目资料，再将这些资料交给模型。默认 `MODEL_NATIVE_TOOLS=false`，避免每次先发送可能被兼容网关拒绝的 CrewAI 原生工具请求。确认网关支持后，可在 `.env` 中设置 `MODEL_NATIVE_TOOLS=true` 启用原生工具调用，5xx 时仍会重试一次不带工具的流式请求。蓝图校验通过后，应用按顺序调用 MCP 的 `create_novel_project` 和 `create_novel_world`。确认接口的 `persistence` 返回 `project_id`、`world_id` 和 `error`，部分写入时会保留已取得的 ID。成功后在同一会话重复确认会返回同一结果。模型生成失败时会返回错误、保留设定并允许重试，不写入数据库。大纲和其他子资源仍在生成结果中。API 监听非默认端口时，应为 API 进程设置 `AITXT_API_BASE_URL`，以便它启动的 MCP 进程连回正确的地址。

启用 `MODEL_NATIVE_TOOLS=true` 时需要支持原生 `Agent.mcps` 的 CrewAI 环境，已用当前环境的 CrewAI 1.15.39 和 MCP SDK 1.30.0 验证。默认生成路径及单独运行 `python -m agents.mcp` 不需要 CrewAI。

## 大纲到正文

用同一个项目按下列顺序调用。示例以《潮汐档案馆》的项目 ID `2` 为起点；分卷和章节 ID 使用前一步的返回值。

| MCP 工具 | 参数示例 | 产出 |
|---|---|---|
| `generate_novel_outline` | `{"project_id":2,"payload":{"volume_count":3}}` | 新的大纲版本、分卷 ID、剧情节点数量 |
| `plan_novel_chapters` | `{"volume_id":12,"payload":{"start_chapter":1,"chapter_count":5}}` | 新的计划章节 ID、章号与标题 |
| `draft_novel_chapter` | `{"chapter_id":34,"payload":{"target_characters":2000}}` | 初稿状态和字数；正文用 `read_novel_chapter` 获取 |
| `review_novel_chapter` | `{"chapter_id":34}` | 审读结论、问题证据与修改建议；不修改数据库 |

`payload.instruction` 可补充要求。大纲每次新建一版草稿；规划的章号不能和项目内已有章节重叠；正文只写入尚无内容的计划章节。模型生成可能需要几分钟，MCP 客户端的调用超时可设为 300 秒。运行生成工作流需要 `.env` 模型配置；启用原生工具模式时另需 CrewAI 的可选运行环境。应用日志的内容、级别和进度标签使用中文，JSON 日志保留标准字段名，避免记录小说正文和密钥。

## 前端实时生成

页面通过 `fetch` 读取 SSE，在世界观、大纲、章节规划、正文和审读过程中持续展示模型分片。原有 JSON 接口继续可用，流式接口使用相同请求体：

| 操作 | POST 地址（前缀 `/api/v1`） |
|---|---|
| 确认世界观生成 | `/novel-assistant/sessions/{session_id}/messages/stream` |
| 生成大纲 | `/writing/projects/{project_id}/outline/stream` |
| 章节规划 | `/writing/volumes/{volume_id}/chapter-plan/stream` |
| 生成正文 | `/writing/chapters/{chapter_id}/draft/stream` |
| 审读正文 | `/writing/chapters/{chapter_id}/review/stream` |

事件依次包含 `status`（中文进度）、`delta`（正文分片），最后以 `result`（校验、保存完成后的原接口结果）或 `error`（中文错误和状态码）结束。等待模型时每 10 秒发送注释心跳，并通过 `X-Accel-Buffering: no` 防止代理缓冲。开始 SSE 后发生的生成错误通过 `error` 事件报告，客户端必须等到 `result` 才能判断成功。

浏览器连接中断后，后台继续完成本次操作，避免取消已提交的数据库写入；页面保留已有文本并提示核对项目结果，不会自动重试写入。流式接口固定使用已收集或已通过 MCP 读取的资料，直接接收 SDK 的正文分片；`MODEL_NATIVE_TOOLS` 仅作用于原 JSON 接口。

也可在 `/docs` 下的「小说写作智能体」分组直接调用对应 REST 接口。
