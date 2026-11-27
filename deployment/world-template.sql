-- 世界观结构化模板列；数据库管理员执行一次。
ALTER TABLE novel_world ADD COLUMN template JSON NULL COMMENT '结构化世界观模板' AFTER cultures;
