-- 文件选题分析结果表；数据库管理员执行一次即可。
CREATE TABLE IF NOT EXISTS app_document_topic (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '选题记录主键',
    account_id BIGINT UNSIGNED NOT NULL COMMENT '所属账号',
    source_type VARCHAR(20) NOT NULL COMMENT '来源类型：files/text/mixed',
    file_names TEXT NOT NULL COMMENT '文件名 JSON 数组',
    input_chars INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '读取的字符数',
    instruction VARCHAR(2000) NOT NULL DEFAULT '' COMMENT '补充要求',
    genre_preference VARCHAR(40) NOT NULL DEFAULT '' COMMENT '类型偏好',
    audience VARCHAR(40) NOT NULL DEFAULT '' COMMENT '目标读者',
    report LONGTEXT NOT NULL COMMENT '选题报告 JSON',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_document_topic_account_created (account_id, created_at),
    CONSTRAINT fk_document_topic_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='文件选题分析结果表';
