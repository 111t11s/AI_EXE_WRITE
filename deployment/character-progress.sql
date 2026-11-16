-- Additive and idempotent. Run with a schema administrator if the app lacks CREATE.
CREATE TABLE IF NOT EXISTS novel_character_progress (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '正文人物进展主键',
    project_id BIGINT UNSIGNED NOT NULL COMMENT '所属小说项目 ID',
    chapter_id BIGINT UNSIGNED NOT NULL COMMENT '来源章节 ID',
    character_id BIGINT UNSIGNED NOT NULL COMMENT '对应人物 ID',
    content_hash CHAR(64) NOT NULL COMMENT '来源正文 SHA256，防止旧事实用于已改正文',
    actions TEXT NOT NULL COMMENT '本章已发生的行动与结果',
    current_state TEXT NOT NULL COMMENT '截至本章的人物状态',
    relationships TEXT NOT NULL COMMENT '截至本章的人物关系与本章变化',
    evidence TEXT NOT NULL COMMENT '本章正文原句数组的 JSON 文本',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_progress_chapter_character (chapter_id, character_id),
    KEY ix_novel_character_progress_project_id (project_id),
    KEY ix_novel_character_progress_character_id (character_id),
    CONSTRAINT fk_progress_project FOREIGN KEY (project_id) REFERENCES novel_project(id) ON DELETE CASCADE,
    CONSTRAINT fk_progress_chapter FOREIGN KEY (chapter_id) REFERENCES novel_chapter(id) ON DELETE CASCADE,
    CONSTRAINT fk_progress_character FOREIGN KEY (character_id) REFERENCES novel_character(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='正文人物进展档案表';
