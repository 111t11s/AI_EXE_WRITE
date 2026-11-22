# 小说数据 MCP 工具

本服务通过 stdio 提供 16 个资源的 80 个 CRUD 工具和 9 个写作工作流工具。工具调用现有 FastAPI 接口，沿用它的字段校验、数据库配置和外键规则。

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
| `rewrite_novel_outline` | `{"outline_id":8,"payload":{"volume_count":3,"instruction":"加强第二卷冲突"}}` | 读取原大纲后重写，保存新版本，原大纲及章节保留 |
| `generate_novel_core_characters` | `{"project_id":2,"payload":{"protagonist_count":1,"important_count":3,"outline_id":8}}` | 根据世界观及大纲生成核心人物；省略 outline_id 时自动使用项目最新大纲，有大纲时一并生成分卷阶段档案，返回角色 ID、档案角色 ID 与层级统计 |
| `generate_novel_supporting_characters` | `{"outline_id":8,"payload":{"volume_id":12,"supporting_count":2,"reuse_character_ids":[4,5]}}` | 根据分卷补充新配角并为勾选的前卷配角安排新戏份；新增数可为 0，返回新增及复用角色 ID |
| `plan_novel_chapters` | `{"volume_id":12,"payload":{"start_chapter":1,"chapter_count":5}}` | 新的计划章节 ID、章号与标题 |
| `draft_novel_chapter` | `{"chapter_id":34,"payload":{"target_characters":2000}}` | 检查项目已有角色后生成并保存正文；人物正文进展由独立分析工具处理，缺少角色返回 409；正文用 `read_novel_chapter` 获取 |
| `batch_draft_novel_chapters` | `{"project_id":12,"payload":{"chapter_ids":[34,35],"target_characters":2000}}` | 按章号顺序生成多个已有规划章节；每章正文保存并完成独立人物进展分析后才继续下一章，最多 20 章 |
| `analyze_novel_character_progress` | `{"chapter_id":34,"payload":{"instruction":"核对本章已发生行动和结果"}}` | 只分析已保存正文，按逐字原文依据保存人物行动、截至本章状态和关系变化；不会续写或修改正文 |
| `list_novel_character_progress` | `{"project_id":12,"before_chapter_number":34,"latest":true}` | 只读查询人物正文进展；按章序排除当前及未来章，latest 返回每人最近状态，支持 limit/offset 分页 |
| `rewrite_novel_chapter` | `{"chapter_id":34,"payload":{"target_characters":2000,"instruction":"保留情节，加强对话"}}` | 读取原正文后重写并替换，设为草稿；期间章节变化则拒绝覆盖 |
| `review_novel_chapter` | `{"chapter_id":34}` | 审读结论、问题证据与修改建议；不修改数据库 |
| `check_novel_consistency` | `{"project_id":2,"payload":{"outline_id":8,"chapter_ids":[34,35],"instruction":"关注主角动机与能力边界"}}` | 先读取大纲、完整人物及关系档案、分卷戏份与相关设定，再对照所选正文；返回实际读取档案清单和经校验原文引据，无依据不认定偏移；不修改小说 |

`payload.instruction` 可补充要求；`payload.model` 可选，取值为 `GET /api/v1/models` 返回的模型 id（`default` 沿用主配置，`deepseek` 使用 `.env` 的 DeepSeek 配置，两者都不进入发给模型的资料）。大纲每次新建一版草稿；规划的章号不能和项目内已有章节重叠；正文只写入尚无内容的计划章节。模型生成可能需要几分钟，MCP 客户端的调用超时可设为 300 秒。运行生成工作流需要 `.env` 模型配置；启用原生工具模式时另需 CrewAI 的可选运行环境。应用日志的内容、级别和进度标签使用中文，JSON 日志保留标准字段名，避免记录小说正文和密钥。

## 前端实时生成

页面通过 `fetch` 读取 SSE，在世界观、大纲、章节规划、正文和审读过程中持续展示模型分片。原有 JSON 接口继续可用，流式接口使用相同请求体：

| 操作 | POST 地址（前缀 `/api/v1`） |
|---|---|
| 确认世界观生成 | `/novel-assistant/sessions/{session_id}/messages/stream` |
| 生成大纲 | `/writing/projects/{project_id}/outline/stream` |
| 章节规划 | `/writing/volumes/{volume_id}/chapter-plan/stream` |
| 生成正文 | `/writing/chapters/{chapter_id}/draft/stream` |
| 批量生成正文 | `/writing/projects/{project_id}/chapters/batch-draft/stream` |
| 分析人物正文进展 | `/writing/chapters/{chapter_id}/character-progress/stream` |
| 审读正文 | `/writing/chapters/{chapter_id}/review/stream` |

事件依次包含 `status`（中文进度）、`delta`（正文分片），最后以 `result`（校验、保存完成后的原接口结果）或 `error`（中文错误和状态码）结束。等待模型时每 10 秒发送注释心跳，并通过 `X-Accel-Buffering: no` 防止代理缓冲。开始 SSE 后发生的生成错误通过 `error` 事件报告，客户端必须等到 `result` 才能判断成功。

浏览器连接中断后，后台继续完成本次操作，避免取消已提交的数据库写入；页面保留已有文本并提示核对项目结果，不会自动重试写入。流式接口固定使用已收集或已通过 MCP 读取的资料，直接接收 SDK 的正文分片；`MODEL_NATIVE_TOOLS` 仅作用于原 JSON 接口。

也可在 `/docs` 下的「小说写作智能体」分组直接调用对应 REST 接口。
