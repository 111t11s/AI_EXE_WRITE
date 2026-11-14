-- Novel project schema v1.1 (MySQL 8.0+, InnoDB, utf8mb4)
-- For existing installations run: python -m db.migrate_accounts

CREATE TABLE IF NOT EXISTS app_account (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '账号主键',
    username VARCHAR(32) NOT NULL COMMENT '登录用户名，统一小写',
    display_name VARCHAR(80) NOT NULL COMMENT '显示昵称',
    password_hash VARCHAR(255) NOT NULL COMMENT '加盐密码哈希',
    is_active BOOLEAN NOT NULL DEFAULT TRUE COMMENT '账号是否启用',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_account_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='创作账号';

CREATE TABLE IF NOT EXISTS app_auth_session (
    token_hash CHAR(64) NOT NULL COMMENT '登录令牌 SHA256 摘要',
    account_id BIGINT UNSIGNED NOT NULL COMMENT '所属账号',
    expires_at DATETIME NOT NULL COMMENT 'UTC 过期时间',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (token_hash),
    KEY ix_app_auth_session_account_id (account_id),
    KEY ix_app_auth_session_expires_at (expires_at),
    CONSTRAINT fk_auth_account FOREIGN KEY (account_id) REFERENCES app_account(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='登录会话';

CREATE TABLE IF NOT EXISTS novel_project (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '项目主键',
    account_id BIGINT UNSIGNED NULL COMMENT '所属账号，旧项目待迁移',
    title VARCHAR(200) NOT NULL DEFAULT '待命名小说' COMMENT '小说名称',
    genre VARCHAR(80) NOT NULL COMMENT '小说类型',
    setting VARCHAR(500) NOT NULL COMMENT '时代与背景',
    tone VARCHAR(200) NOT NULL COMMENT '故事基调',
    target_length VARCHAR(120) NOT NULL COMMENT '篇幅规划',
    premise TEXT NULL COMMENT '故事前提或一句话梗概',
    protagonist_goal VARCHAR(500) NOT NULL COMMENT '主角核心目标',
    status ENUM('planning','writing','completed','paused') NOT NULL DEFAULT 'planning' COMMENT '项目状态：规划中/写作中/已完成/暂停',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    KEY idx_project_status_updated (status, updated_at),
    KEY idx_project_account (account_id),
    CONSTRAINT fk_project_account FOREIGN KEY (account_id) REFERENCES app_account(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='小说项目表';

CREATE TABLE IF NOT EXISTS novel_design_session (
    id CHAR(36) NOT NULL COMMENT '会话 UUID',
    account_id BIGINT UNSIGNED NULL COMMENT '所属账号，旧会话待迁移',
    project_id BIGINT UNSIGNED NULL COMMENT '关联小说项目 ID',
    status ENUM('collecting','ready','generating','completed','failed') NOT NULL DEFAULT 'collecting' COMMENT '会话状态：收集中/待生成/生成中/已完成/失败',
    collected_data JSON NOT NULL COMMENT '问答阶段收集的设定 JSON',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    KEY idx_design_project (project_id),
    KEY idx_design_account (account_id),
    KEY idx_design_status_updated (status, updated_at),
    CONSTRAINT fk_design_project FOREIGN KEY (project_id) REFERENCES novel_project(id) ON DELETE SET NULL,
    CONSTRAINT fk_design_account FOREIGN KEY (account_id) REFERENCES app_account(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='设定问答会话表';

CREATE TABLE IF NOT EXISTS novel_design_message (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '消息主键',
    session_id CHAR(36) NOT NULL COMMENT '所属问答会话 UUID',
    sequence_no INT UNSIGNED NOT NULL COMMENT '会话内消息序号',
    speaker ENUM('user','assistant','system') NOT NULL COMMENT '发言者：用户/助手/系统',
    content TEXT NOT NULL COMMENT '消息内容',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发送时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_design_message_sequence (session_id, sequence_no),
    CONSTRAINT fk_design_message_session FOREIGN KEY (session_id) REFERENCES novel_design_session(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='设定问答消息表';

CREATE TABLE IF NOT EXISTS novel_world (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '世界观主键',
    project_id BIGINT UNSIGNED NOT NULL COMMENT '关联小说项目 ID',
    version_no INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '世界观版本号',
    title VARCHAR(200) NOT NULL COMMENT '世界观名称',
    summary TEXT NOT NULL COMMENT '世界观简介',
    era VARCHAR(500) NOT NULL COMMENT '时代背景',
    history LONGTEXT NULL COMMENT '历史沿革',
    rules LONGTEXT NOT NULL COMMENT '世界运行规则与约束说明',
    cultures LONGTEXT NOT NULL COMMENT '文化与社会结构说明',
    is_current BOOLEAN NOT NULL DEFAULT TRUE COMMENT '是否为当前版本',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_world_version (project_id, version_no),
    KEY idx_world_current (project_id, is_current),
    CONSTRAINT fk_world_project FOREIGN KEY (project_id) REFERENCES novel_project(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='世界观版本表';

CREATE TABLE IF NOT EXISTS novel_region (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '地域主键',
    world_id BIGINT UNSIGNED NOT NULL COMMENT '所属世界观 ID',
    name VARCHAR(160) NOT NULL COMMENT '地域名称',
    description TEXT NOT NULL COMMENT '地域描述',
    climate VARCHAR(160) NOT NULL DEFAULT '' COMMENT '气候特征',
    resources LONGTEXT NOT NULL COMMENT '地域资源说明',
    locations LONGTEXT NOT NULL COMMENT '重要地点与地理说明',
    PRIMARY KEY (id),
    UNIQUE KEY uk_region_name (world_id, name),
    CONSTRAINT fk_region_world FOREIGN KEY (world_id) REFERENCES novel_world(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='世界地域表';

CREATE TABLE IF NOT EXISTS novel_power_system (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '力量体系主键',
    world_id BIGINT UNSIGNED NOT NULL COMMENT '所属世界观 ID',
    name VARCHAR(160) NOT NULL COMMENT '力量体系名称',
    source TEXT NOT NULL COMMENT '力量来源',
    rules LONGTEXT NOT NULL COMMENT '力量运行规则与机制说明',
    limitations LONGTEXT NOT NULL COMMENT '力量体系限制说明',
    growth_rules LONGTEXT NOT NULL COMMENT '力量成长规则说明',
    PRIMARY KEY (id),
    UNIQUE KEY uk_power_name (world_id, name),
    CONSTRAINT fk_power_world FOREIGN KEY (world_id) REFERENCES novel_world(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='力量体系表';

CREATE TABLE IF NOT EXISTS novel_power_rank (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '力量等级主键',
    power_system_id BIGINT UNSIGNED NOT NULL COMMENT '所属力量体系 ID',
    rank_order INT UNSIGNED NOT NULL COMMENT '等级顺序，从低到高',
    name VARCHAR(160) NOT NULL COMMENT '等级名称',
    description TEXT NOT NULL COMMENT '等级描述',
    breakthrough_condition TEXT NOT NULL COMMENT '突破条件',
    capabilities LONGTEXT NOT NULL COMMENT '该等级能力说明',
    PRIMARY KEY (id),
    UNIQUE KEY uk_power_rank_order (power_system_id, rank_order),
    UNIQUE KEY uk_power_rank_name (power_system_id, name),
    CONSTRAINT fk_rank_power FOREIGN KEY (power_system_id) REFERENCES novel_power_system(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='力量等级表';

CREATE TABLE IF NOT EXISTS novel_cheat_system (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '金手指主键',
    world_id BIGINT UNSIGNED NOT NULL COMMENT '所属世界观 ID',
    name VARCHAR(160) NOT NULL COMMENT '金手指名称',
    cheat_type VARCHAR(120) NOT NULL COMMENT '金手指类型',
    origin TEXT NOT NULL COMMENT '金手指来源',
    functions LONGTEXT NOT NULL COMMENT '功能与使用方式说明',
    limitations LONGTEXT NOT NULL COMMENT '使用限制说明',
    costs LONGTEXT NOT NULL COMMENT '使用代价说明',
    growth_path LONGTEXT NOT NULL COMMENT '成长路径说明',
    story_secret TEXT NOT NULL COMMENT '与主线相关的隐藏秘密',
    PRIMARY KEY (id),
    UNIQUE KEY uk_cheat_name (world_id, name),
    CONSTRAINT fk_cheat_world FOREIGN KEY (world_id) REFERENCES novel_world(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='金手指设定表';

CREATE TABLE IF NOT EXISTS novel_faction (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '势力主键',
    world_id BIGINT UNSIGNED NOT NULL COMMENT '所属世界观 ID',
    name VARCHAR(160) NOT NULL COMMENT '势力名称',
    faction_type VARCHAR(120) NOT NULL DEFAULT '' COMMENT '势力类型',
    summary TEXT NOT NULL COMMENT '势力简介',
    goal TEXT NOT NULL COMMENT '势力目标',
    leader VARCHAR(160) NOT NULL DEFAULT '' COMMENT '势力领袖',
    territory VARCHAR(300) NOT NULL DEFAULT '' COMMENT '势力领地',
    strengths LONGTEXT NOT NULL COMMENT '势力优势说明',
    weaknesses LONGTEXT NOT NULL COMMENT '势力弱点说明',
    relationships LONGTEXT NOT NULL COMMENT '与其他势力的关系说明',
    PRIMARY KEY (id),
    UNIQUE KEY uk_faction_name (world_id, name),
    CONSTRAINT fk_faction_world FOREIGN KEY (world_id) REFERENCES novel_world(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='势力阵营表';

CREATE TABLE IF NOT EXISTS novel_character (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '角色主键',
    project_id BIGINT UNSIGNED NOT NULL COMMENT '所属小说项目 ID',
    faction_id BIGINT UNSIGNED NULL COMMENT '所属势力 ID',
    name VARCHAR(160) NOT NULL COMMENT '角色姓名',
    role VARCHAR(80) NOT NULL COMMENT '角色定位',
    summary TEXT NOT NULL COMMENT '角色简介',
    personality LONGTEXT NOT NULL COMMENT '性格特征与行为倾向说明',
    background LONGTEXT NOT NULL COMMENT '角色背景经历',
    goal TEXT NOT NULL COMMENT '角色目标',
    motivation TEXT NOT NULL COMMENT '行动动机',
    weakness TEXT NOT NULL COMMENT '角色弱点',
    character_arc TEXT NOT NULL COMMENT '角色成长弧线',
    abilities LONGTEXT NOT NULL COMMENT '角色能力与技能说明',
    first_appearance_chapter INT UNSIGNED NULL COMMENT '首次出场章节号',
    status ENUM('active','departed','dead') NOT NULL DEFAULT 'active' COMMENT '角色状态：活跃/退场/死亡',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_character_project_name (project_id, name),
    KEY idx_character_role (project_id, role),
    CONSTRAINT fk_character_project FOREIGN KEY (project_id) REFERENCES novel_project(id) ON DELETE CASCADE,
    CONSTRAINT fk_character_faction FOREIGN KEY (faction_id) REFERENCES novel_faction(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='小说角色表';

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

CREATE TABLE IF NOT EXISTS novel_outline (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '大纲主键',
    project_id BIGINT UNSIGNED NOT NULL COMMENT '所属小说项目 ID',
    version_no INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '大纲版本号',
    summary LONGTEXT NOT NULL COMMENT '整体大纲摘要',
    central_conflict TEXT NOT NULL COMMENT '全书核心冲突',
    themes LONGTEXT NOT NULL COMMENT '作品主题与表达方向说明',
    status ENUM('draft','approved','active','archived') NOT NULL DEFAULT 'draft' COMMENT '大纲状态：草稿/已确认/执行中/已归档',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_outline_version (project_id, version_no),
    KEY idx_outline_status (project_id, status),
    CONSTRAINT fk_outline_project FOREIGN KEY (project_id) REFERENCES novel_project(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='小说大纲版本表';

CREATE TABLE IF NOT EXISTS novel_outline_volume (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '分卷主键',
    outline_id BIGINT UNSIGNED NOT NULL COMMENT '所属大纲版本 ID',
    volume_number INT UNSIGNED NOT NULL COMMENT '卷序号',
    title VARCHAR(200) NOT NULL COMMENT '卷标题',
    summary LONGTEXT NOT NULL COMMENT '本卷内容摘要',
    protagonist_goal TEXT NOT NULL COMMENT '本卷主角目标',
    main_conflict TEXT NOT NULL COMMENT '本卷主要冲突',
    climax TEXT NOT NULL COMMENT '本卷高潮事件',
    ending_hook TEXT NOT NULL COMMENT '本卷结尾悬念',
    PRIMARY KEY (id),
    UNIQUE KEY uk_outline_volume_number (outline_id, volume_number),
    CONSTRAINT fk_volume_outline FOREIGN KEY (outline_id) REFERENCES novel_outline(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='大纲分卷表';

CREATE TABLE IF NOT EXISTS novel_plot_beat (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '剧情节点主键',
    volume_id BIGINT UNSIGNED NOT NULL COMMENT '所属分卷 ID',
    sort_order INT UNSIGNED NOT NULL COMMENT '本卷内排序序号',
    title VARCHAR(200) NOT NULL COMMENT '剧情节点标题',
    summary TEXT NOT NULL COMMENT '剧情节点内容',
    beat_type VARCHAR(80) NOT NULL DEFAULT 'plot' COMMENT '节点类型',
    foreshadowing LONGTEXT NOT NULL COMMENT '相关伏笔及回收计划说明',
    PRIMARY KEY (id),
    UNIQUE KEY uk_volume_beat_order (volume_id, sort_order),
    CONSTRAINT fk_beat_volume FOREIGN KEY (volume_id) REFERENCES novel_outline_volume(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='剧情节点表';

CREATE TABLE IF NOT EXISTS novel_chapter (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '章节主键',
    project_id BIGINT UNSIGNED NOT NULL COMMENT '所属小说项目 ID',
    volume_id BIGINT UNSIGNED NULL COMMENT '所属分卷 ID',
    chapter_number INT UNSIGNED NOT NULL COMMENT '全书章节序号',
    title VARCHAR(200) NOT NULL DEFAULT '' COMMENT '章节标题',
    summary TEXT NOT NULL COMMENT '章节摘要',
    content LONGTEXT NOT NULL COMMENT '章节正文',
    status ENUM('planned','draft','reviewed','published') NOT NULL DEFAULT 'planned' COMMENT '章节状态：计划中/草稿/已审阅/已发布',
    word_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '正文总字数',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_project_chapter_number (project_id, chapter_number),
    KEY idx_chapter_volume (volume_id, chapter_number),
    CONSTRAINT fk_chapter_project FOREIGN KEY (project_id) REFERENCES novel_project(id) ON DELETE CASCADE,
    CONSTRAINT fk_chapter_volume FOREIGN KEY (volume_id) REFERENCES novel_outline_volume(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='小说章节表';
