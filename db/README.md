# Novel data model v1.0

This is the new canonical structure for the novel-design workflow. The legacy XML mapper and `DataBaseManager` have been removed from the project.

## Files

- `models.py`: Pydantic contract used by generation, API responses, and persistence code.
- `schema.sql`: fresh MySQL 8 schema. Apply it to an empty database; it does not migrate or alter an existing database.

## Entity flow

```text
design session -> project -> world version
                         ├── regions
                         ├── power system -> ordered ranks
                         ├── cheat system
                         ├── factions
                         ├── characters -> character relationships
                         ├── outline version -> volumes -> plot beats
                         └── chapters
```

The generation boundary is `NovelBlueprint`: one project, one world setting, a power system, a cheat system, factions, characters, and a versionable multi-volume outline. The assistant's collected questionnaire answers are stored in `DesignSessionData` until the user confirms generation.

描述型内容统一使用 `LONGTEXT`，包括世界规则、地域资源、力量机制、等级能力、金手指限制、势力关系、人物性格、角色能力、主题和伏笔。这样便于保存自然语言段落、Markdown 或带层次的长篇说明，不需要在构建阶段拆成 JSON 数组。只有问答会话的 `collected_data` 保留 JSON，因为它需要按字段逐项更新。

## Table aliases

Every table in `schema.sql` carries the matching Chinese name as a MySQL table comment. Every data column also has a MySQL `COMMENT` describing its meaning in Chinese. MySQL clients show these as table and column remarks.

Primary keys, foreign keys, ordering/version fields, status values, timestamps, and long-text descriptions are all annotated. `collected_data` is the only JSON payload in the schema.

| Table | 中文别名 |
|---|---|
| `novel_project` | 小说项目表 |
| `novel_design_session` | 设定问答会话表 |
| `novel_design_message` | 设定问答消息表 |
| `novel_world` | 世界观版本表 |
| `novel_region` | 世界地域表 |
| `novel_power_system` | 力量体系表 |
| `novel_power_rank` | 力量等级表 |
| `novel_cheat_system` | 金手指设定表 |
| `novel_faction` | 势力阵营表 |
| `novel_character` | 小说角色表 |
| `novel_character_relationship` | 角色关系表 |
| `novel_outline` | 小说大纲版本表 |
| `novel_outline_volume` | 大纲分卷表 |
| `novel_plot_beat` | 剧情节点表 |
| `novel_chapter` | 小说章节表 |

## CRUD 接口

启动 `python main.py` 后可在 `http://localhost:8000/docs` 查看和调用接口。连接读取项目 `.env` 中的 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`，使用已经创建的表。

如果远程 MySQL 账户设置了 `REQUIRE SSL`，在项目 `.env` 中添加 `DB_TLS_REQUIRED=true`，然后重启应用。可设置 `DB_TLS_CA` 为可信 MySQL CA 的 PEM 文件路径以验证服务端证书；填写 CA 也会启用 TLS。未填写 CA 时，连接会加密，但不会验证服务端证书。后端与 MySQL 同机、使用本地账户时，可以保留默认设置。

这些接口也可通过 [小说数据 MCP 服务](../agents/mcp/README.md) 调用。

描述字段的 API 类型是文本。对于旧版数据库中仍为 `JSON` 的描述列，接口会把文本写成 JSON 字符串；已有数组或对象会以 JSON 文本返回，更新其他字段时保留其原始结构。`collected_data` 仍直接接收和返回 JSON 对象。数据库字段类型变更后应重启应用以刷新识别结果。

每个资源的路径前缀为 `/api/v1`。集合支持 `POST`（新增，返回 `201` 和完整记录）及 `GET`（列表）；在集合路径后加 `/{id}`，支持 `GET`（详情）、`PATCH`（局部更新，返回更新后的记录）及 `DELETE`（成功返回 `204`）。除问答会话使用服务端生成的 UUID 外，记录 ID 均为数据库生成的整数。

| 集合路径 | 可选筛选字段 |
|---|---|
| `/projects` | `status` |
| `/design-sessions` | `project_id`、`status` |
| `/design-messages` | `session_id` |
| `/worlds` | `project_id`、`is_current` |
| `/regions` | `world_id` |
| `/power-systems` | `world_id` |
| `/power-ranks` | `power_system_id` |
| `/cheat-systems` | `world_id` |
| `/factions` | `world_id` |
| `/characters` | `project_id`、`faction_id`、`role`、`status` |
| `/character-relationships` | `source_character_id`、`target_character_id` |
| `/outlines` | `project_id`、`status` |
| `/outline-volumes` | `outline_id` |
| `/plot-beats` | `volume_id` |
| `/chapters` | `project_id`、`volume_id`、`chapter_number`、`status` |

列表使用 `limit`（默认 20，最大 100）和 `offset`（默认 0）分页，返回 `items`、`limit`、`offset` 和 `has_more`。分卷、章节、剧情节点、等级、消息和版本按各自的业务序号排序。例如，`GET /api/v1/chapters?project_id=12&status=draft&limit=10` 查询项目 12 的草稿章节。

`PATCH` 只修改提交的字段，允许给可空字段传 `null` 来解除关联，例如 `PATCH /api/v1/chapters/34` 传 `{"volume_id": null}`。记录 ID、时间戳和所属父记录不可修改。角色所属势力、章节所属分卷以及关系两端的角色必须属于同一小说项目。将一个世界观版本设为当前版本会取消该项目其他版本的当前状态。删除遵循表定义中的外键规则：例如，删除项目会级联删除其章节和世界观，并把关联问答会话的 `project_id` 置空。

不存在的记录返回 `404`，重复序号、版本号或无效关联返回 `409`，请求字段校验失败返回 `422`，数据库不可用时返回 `503`。`/novel-assistant` 的问答会话仍保存在内存中；确认生成后，助手通过 MCP 将验证通过的项目和世界观写入数据库。
