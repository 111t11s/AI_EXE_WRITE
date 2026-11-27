-- 世界观结构化模板列；可重复执行。
SET @column_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'novel_world' AND COLUMN_NAME = 'template'
);
SET @sql = IF(@column_exists = 0,
    'ALTER TABLE novel_world ADD COLUMN template JSON NULL COMMENT ''结构化世界观模板'' AFTER cultures',
    'SELECT ''novel_world.template already exists'' AS message');
PREPARE statement FROM @sql;
EXECUTE statement;
DEALLOCATE PREPARE statement;
