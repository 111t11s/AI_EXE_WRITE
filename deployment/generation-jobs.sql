-- 后台生成任务持久化表：任务中心在服务重启后保留历史，由用户手动删除。
-- 数据库管理员执行一次即可；表已存在时不会重复创建。
CREATE TABLE IF NOT EXISTS app_generation_job (
    id VARCHAR(32) NOT NULL PRIMARY KEY COMMENT '任务主键',
    account_id BIGINT UNSIGNED NOT NULL COMMENT '所属账号',
    job_key VARCHAR(200) NOT NULL COMMENT '去重键，同一目标生成中不重复创建',
    kind VARCHAR(40) NOT NULL COMMENT '任务类型',
    title VARCHAR(200) NOT NULL COMMENT '任务标题',
    project_id BIGINT UNSIGNED NULL COMMENT '关联小说项目 ID',
    resource_id VARCHAR(80) NULL COMMENT '关联资源 ID',
    status VARCHAR(20) NOT NULL COMMENT '任务状态：running/completed/failed',
    message VARCHAR(500) NOT NULL DEFAULT '' COMMENT '最近一条进度说明',
    text LONGTEXT NULL COMMENT '生成正文的实时快照',
    thinking LONGTEXT NULL COMMENT '模型思考内容快照',
    truncated BOOLEAN NOT NULL DEFAULT FALSE COMMENT '快照是否被截断',
    result LONGTEXT NULL COMMENT '任务结果 JSON 文本',
    error LONGTEXT NULL COMMENT '失败原因 JSON 文本',
    event_sequence INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '事件序号',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    finished_at DATETIME NULL COMMENT '完成时间',
    KEY idx_generation_job_account_created (account_id, created_at),
    KEY idx_generation_job_status (status),
    CONSTRAINT fk_generation_job_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='后台生成任务表';
