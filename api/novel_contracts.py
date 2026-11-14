"""Shared resource contracts for the novel CRUD API and its MCP tools."""

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property

from pydantic import Field, create_model

from api import novel_schemas as schemas


CRUD_PREFIX = "/api/v1"


@dataclass(frozen=True)
class CrudResource:
    path: str
    table_name: str
    label: str
    create_schema: type[schemas.RowInput]
    owner: str | None = None
    filters: tuple[str, ...] = ()
    order: tuple[str, ...] = ()
    timestamps: tuple[str, ...] = ()
    session_ids: bool = False
    account_owned: bool = False

    @property
    def id_type(self):
        return schemas.SessionId if self.session_ids else schemas.RowId

    @property
    def schema_prefix(self):
        return self.create_schema.__name__.removesuffix("Create")

    @cached_property
    def patch_schema(self):
        # Explicit null is only accepted by nullable fields; omitted fields stay unset.
        patch_fields = {
            key: (field.rebuild_annotation(), Field(default_factory=lambda: None))
            for key, field in self.create_schema.model_fields.items() if key != self.owner
        }
        return create_model(f"{self.schema_prefix}Patch", __base__=schemas.RowInput, **patch_fields)

    @cached_property
    def read_schema(self):
        return create_model(
            f"{self.schema_prefix}Read", id=(str if self.session_ids else int, ...),
            **({"account_id": (schemas.RowId | None, None)} if self.account_owned else {}),
            **{key: (field.annotation, ...) for key, field in self.create_schema.model_fields.items()},
            **{key: (datetime, ...) for key in self.timestamps},
        )

    @cached_property
    def page_schema(self):
        return create_model(
            f"{self.schema_prefix}Page", items=(list[self.read_schema], ...), limit=(int, ...), offset=(int, ...),
            has_more=(bool, ...),
        )

    @cached_property
    def filter_schema(self):
        fields = self.create_schema.model_fields
        filter_keys = ((self.owner,) if self.owner else ()) + self.filters
        return create_model(
            f"{self.schema_prefix}Filters", __base__=schemas.RowInput,
            limit=(int, Field(default=20, ge=1, le=100)), offset=(int, Field(default=0, ge=0, le=2**32 - 1)),
            **{key: (fields[key].rebuild_annotation() | None, None) for key in filter_keys},
        )


CREATED = ("created_at",)
AUDIT = (*CREATED, "updated_at")

CRUD_RESOURCES = (
    CrudResource("/projects", "novel_project", "小说项目", schemas.ProjectCreate,
                 filters=("status",), timestamps=AUDIT, account_owned=True),
    CrudResource("/design-sessions", "novel_design_session", "设定问答会话", schemas.DesignSessionCreate,
                 filters=("project_id", "status"), timestamps=AUDIT, session_ids=True, account_owned=True),
    CrudResource("/design-messages", "novel_design_message", "设定问答消息", schemas.DesignMessageCreate,
                 owner="session_id", order=("session_id", "sequence_no"), timestamps=CREATED),
    CrudResource("/worlds", "novel_world", "世界观版本", schemas.WorldCreate,
                 owner="project_id", filters=("is_current",), order=("project_id", "version_no"), timestamps=CREATED),
    CrudResource("/regions", "novel_region", "世界地域", schemas.RegionCreate, owner="world_id"),
    CrudResource("/power-systems", "novel_power_system", "力量体系", schemas.PowerSystemCreate, owner="world_id"),
    CrudResource("/power-ranks", "novel_power_rank", "力量等级", schemas.PowerRankCreate,
                 owner="power_system_id", order=("power_system_id", "rank_order")),
    CrudResource("/cheat-systems", "novel_cheat_system", "金手指设定", schemas.CheatSystemCreate, owner="world_id"),
    CrudResource("/factions", "novel_faction", "势力阵营", schemas.FactionCreate, owner="world_id"),
    CrudResource("/characters", "novel_character", "小说角色", schemas.CharacterCreate,
                 owner="project_id", filters=("faction_id", "role", "status"), timestamps=AUDIT),
    CrudResource("/character-relationships", "novel_character_relationship", "角色关系", schemas.CharacterRelationshipCreate,
                 owner="source_character_id", filters=("target_character_id",)),
    CrudResource("/outlines", "novel_outline", "小说大纲版本", schemas.OutlineCreate,
                 owner="project_id", filters=("status",), order=("project_id", "version_no"), timestamps=CREATED),
    CrudResource("/outline-volumes", "novel_outline_volume", "大纲分卷", schemas.OutlineVolumeCreate,
                 owner="outline_id", order=("outline_id", "volume_number")),
    CrudResource("/plot-beats", "novel_plot_beat", "剧情节点", schemas.PlotBeatCreate,
                 owner="volume_id", order=("volume_id", "sort_order")),
    CrudResource("/chapters", "novel_chapter", "小说章节", schemas.ChapterCreate,
                 owner="project_id", filters=("volume_id", "chapter_number", "status"),
                 order=("project_id", "chapter_number"), timestamps=AUDIT),
)
