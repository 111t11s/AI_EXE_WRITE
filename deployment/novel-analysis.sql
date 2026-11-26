-- 小说章节拆解结果表；数据库管理员执行一次即可。
CREATE TABLE IF NOT EXISTS app_novel_analysis (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '拆解记录主键',
    account_id BIGINT UNSIGNED NOT NULL COMMENT '所属账号',
    file_names TEXT NOT NULL COMMENT '文件名 JSON 数组',
    input_chars INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '读取的字符数',
    instruction VARCHAR(2000) NOT NULL DEFAULT '' COMMENT '补充要求',
    report LONGTEXT NOT NULL COMMENT '拆解报告 JSON',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_novel_analysis_account_created (account_id, created_at),
    CONSTRAINT fk_novel_analysis_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='小说章节拆解结果表';

CREATE TABLE IF NOT EXISTS app_analysis_dimension (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_id BIGINT UNSIGNED NOT NULL,
    dimension_key VARCHAR(40) NOT NULL,
    label VARCHAR(40) NOT NULL,
    category VARCHAR(20) NOT NULL DEFAULT 'outline',
    description VARCHAR(600) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_analysis_dimension_account_key (account_id, dimension_key),
    CONSTRAINT fk_analysis_dimension_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='已批准的自定义拆解维度';

CREATE TABLE IF NOT EXISTS app_analysis_candidate (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_id BIGINT UNSIGNED NOT NULL,
    analysis_id BIGINT UNSIGNED NOT NULL,
    dimension_key VARCHAR(40) NOT NULL,
    dimension_label VARCHAR(40) NOT NULL,
    dimension_reason VARCHAR(600) NOT NULL DEFAULT '',
    name VARCHAR(80) NOT NULL,
    summary VARCHAR(600) NOT NULL,
    mechanism VARCHAR(1200) NOT NULL DEFAULT '',
    reuse VARCHAR(1000) NOT NULL DEFAULT '',
    evidence LONGTEXT NOT NULL,
    confidence VARCHAR(10) NOT NULL DEFAULT 'medium',
    applicable_genres TEXT NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'pending',
    reviewer_id BIGINT UNSIGNED NULL,
    reviewed_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_analysis_candidate_account_status (account_id, status),
    KEY idx_analysis_candidate_analysis (analysis_id),
    CONSTRAINT fk_analysis_candidate_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE,
    CONSTRAINT fk_analysis_candidate_analysis FOREIGN KEY (analysis_id) REFERENCES app_novel_analysis(id) ON DELETE CASCADE,
    CONSTRAINT fk_analysis_candidate_reviewer FOREIGN KEY (reviewer_id) REFERENCES app_account(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='待审核的新维度技法';

CREATE TABLE IF NOT EXISTS app_analysis_technique (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    account_id BIGINT UNSIGNED NOT NULL,
    analysis_id BIGINT UNSIGNED NOT NULL,
    candidate_id BIGINT UNSIGNED NULL,
    dimension_key VARCHAR(40) NOT NULL,
    dimension_label VARCHAR(40) NOT NULL,
    name VARCHAR(80) NOT NULL,
    summary VARCHAR(600) NOT NULL,
    mechanism VARCHAR(1200) NOT NULL DEFAULT '',
    reuse VARCHAR(1000) NOT NULL DEFAULT '',
    evidence LONGTEXT NOT NULL,
    confidence VARCHAR(10) NOT NULL DEFAULT 'medium',
    applicable_genres TEXT NOT NULL,
    source VARCHAR(20) NOT NULL DEFAULT 'standard',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_analysis_technique_account (account_id, created_at),
    KEY idx_analysis_technique_analysis (analysis_id),
    CONSTRAINT fk_analysis_technique_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE,
    CONSTRAINT fk_analysis_technique_analysis FOREIGN KEY (analysis_id) REFERENCES app_novel_analysis(id) ON DELETE CASCADE,
    CONSTRAINT fk_analysis_technique_candidate FOREIGN KEY (candidate_id) REFERENCES app_analysis_candidate(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='已审核通过的写作技法表';
