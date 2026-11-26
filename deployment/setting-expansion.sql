-- 设定补全结果表；数据库管理员执行一次即可。
CREATE TABLE IF NOT EXISTS app_setting_expansion (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '补全记录主键',
    account_id BIGINT UNSIGNED NOT NULL COMMENT '所属账号',
    file_names TEXT NOT NULL COMMENT '文件名 JSON 数组',
    input_chars INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '读取的字符数',
    instruction VARCHAR(2000) NOT NULL DEFAULT '' COMMENT '补全要求',
    report LONGTEXT NOT NULL COMMENT '补全报告 JSON',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_setting_expansion_account_created (account_id, created_at),
    CONSTRAINT fk_setting_expansion_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='设定补全结果表';
