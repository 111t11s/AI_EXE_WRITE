-- 章节规划分配的出场角色；数据库管理员执行一次。
CREATE TABLE IF NOT EXISTS novel_chapter_character (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    chapter_id BIGINT UNSIGNED NOT NULL,
    character_id BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_chapter_character (chapter_id, character_id),
    CONSTRAINT fk_chapter_character_chapter FOREIGN KEY (chapter_id) REFERENCES novel_chapter(id) ON DELETE CASCADE,
    CONSTRAINT fk_chapter_character_person FOREIGN KEY (character_id) REFERENCES novel_character(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='章节出场角色表';
