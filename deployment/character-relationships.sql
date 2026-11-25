-- Run once when upgrading an existing installation that predates the
-- character relationship table. Fresh installations already get it from db/schema.sql.
CREATE TABLE IF NOT EXISTS novel_character_relationship (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '关系记录主键',
    source_character_id BIGINT UNSIGNED NOT NULL COMMENT '关系发起角色 ID',
    target_character_id BIGINT UNSIGNED NOT NULL COMMENT '关系目标角色 ID',
    relationship_type VARCHAR(100) NOT NULL COMMENT '关系类型',
    description TEXT NOT NULL COMMENT '关系描述',
    changed_at_chapter INT UNSIGNED NULL COMMENT '关系变化所在章节号',
    PRIMARY KEY (id),
    UNIQUE KEY uk_character_relationship (source_character_id, target_character_id, relationship_type),
    CONSTRAINT fk_relationship_source FOREIGN KEY (source_character_id) REFERENCES novel_character(id) ON DELETE CASCADE,
    CONSTRAINT fk_relationship_target FOREIGN KEY (target_character_id) REFERENCES novel_character(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='角色关系表';
