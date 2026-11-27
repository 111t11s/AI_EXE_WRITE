-- 正文新角色/新势力审核表；数据库管理员执行一次即可。
CREATE TABLE IF NOT EXISTS app_entity_review (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '审核记录主键',
    account_id BIGINT UNSIGNED NOT NULL COMMENT '所属账号',
    project_id BIGINT UNSIGNED NOT NULL COMMENT '所属小说',
    chapter_id BIGINT UNSIGNED NOT NULL COMMENT '来源章节',
    entity_type VARCHAR(16) NOT NULL COMMENT 'character/faction',
    name VARCHAR(160) NOT NULL COMMENT '正文中的新名称',
    details LONGTEXT NOT NULL COMMENT '候选详情 JSON',
    evidence LONGTEXT NOT NULL COMMENT '正文证据 JSON',
    status VARCHAR(10) NOT NULL DEFAULT 'pending' COMMENT 'pending/approved/replaced/rejected',
    existing_id BIGINT UNSIGNED NULL COMMENT '替换时使用的现有角色/势力 ID',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at DATETIME NULL,
    PRIMARY KEY (id),
    KEY idx_entity_review_project_status (project_id, status),
    KEY idx_entity_review_chapter (chapter_id),
    CONSTRAINT fk_entity_review_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE,
    CONSTRAINT fk_entity_review_project FOREIGN KEY (project_id) REFERENCES novel_project(id) ON DELETE CASCADE,
    CONSTRAINT fk_entity_review_chapter FOREIGN KEY (chapter_id) REFERENCES novel_chapter(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='正文新角色/新势力审核表';
