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
        "AITXT_API_BASE_URL": "http://127.0.0.1:8000/api/v1"
      }
    }
  }
}
```

`AITXT_API_BASE_URL` 可省略，默认使用示例中的地址。API 地址变更时，配置值应包含 `/api/v1`。MCP 进程无需配置数据库密码。

工具名使用 `{操作}_{表名}`，例如：

| 工具 | 参数示例 |
|---|---|
| `list_novel_chapter` | `{"filters":{"project_id":12,"status":"draft","limit":10}}` |
| `create_novel_chapter` | `{"payload":{"project_id":12,"chapter_number":1,"title":"开篇"}}` |
| `read_novel_chapter` | `{"record_id":34}` |
| `update_novel_chapter` | `{"record_id":34,"payload":{"volume_id":null}}` |
| `delete_novel_chapter` | `{"record_id":34}` |

`list_*` 可省略 `filters`；`update_*` 只发送传入的字段，显式 `null` 可解除允许为空的关联。删除成功返回 `{"record_id":34,"deleted":true}`。删除会遵循数据库外键规则，可能级联删除关联记录。

世界观生成使用 CrewAI 的原生 MCP 注册，将 `list_novel_world` 和 `read_novel_world` 提供给模型。用户确认且蓝图校验通过后，应用按顺序调用 `create_novel_project` 和 `create_novel_world`。确认接口的 `persistence` 返回 `project_id`、`world_id` 和 `error`，部分写入时会保留已取得的 ID。相同内存会话重复确认会返回同一结果。大纲和其他子资源仍在生成结果中；模型失败时返回的备用蓝图不会写库。API 监听非默认端口时，应为 API 进程设置 `AITXT_API_BASE_URL`，以便它启动的 MCP 进程连回正确的地址。

生成 Agent 需要支持原生 `Agent.mcps` 的 CrewAI 环境，已用当前环境的 CrewAI 1.15.39 和 MCP SDK 1.30.0 验证。单独运行 `python -m agents.mcp` 不需要 CrewAI。

## 大纲到正文

用同一个项目按下列顺序调用。示例以《潮汐档案馆》的项目 ID `2` 为起点；分卷和章节 ID 使用前一步的返回值。

| MCP 工具 | 参数示例 | 产出 |
|---|---|---|
| `generate_novel_outline` | `{"project_id":2,"payload":{"volume_count":3}}` | 新的大纲版本、分卷 ID、剧情节点数量 |
| `plan_novel_chapters` | `{"volume_id":12,"payload":{"start_chapter":1,"chapter_count":5}}` | 新的计划章节 ID、章号与标题 |
| `draft_novel_chapter` | `{"chapter_id":34,"payload":{"target_characters":2000}}` | 初稿状态和字数；正文用 `read_novel_chapter` 获取 |
| `review_novel_chapter` | `{"chapter_id":34}` | 审读结论、问题证据与修改建议；不修改数据库 |

`payload.instruction` 可补充要求。大纲每次新建一版草稿；规划的章号不能和项目内已有章节重叠；正文只写入尚无内容的计划章节。模型生成可能需要几分钟，MCP 客户端的调用超时可设为 300 秒。运行生成工作流需要复用当前项目的 CrewAI 环境及 `.env` 模型配置；基础 `requirements.txt` 未包含 CrewAI 的可选运行环境。

也可在 `/docs` 下的「小说写作智能体」分组直接调用对应 REST 接口。
