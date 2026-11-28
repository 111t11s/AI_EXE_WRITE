-- 分卷伏笔与字数规划字段；数据库管理员执行一次。
SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='novel_outline_volume' AND COLUMN_NAME='min_foreshadows')=0,
  'ALTER TABLE novel_outline_volume ADD COLUMN min_foreshadows INT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''本卷最低伏笔数量'' AFTER ending_hook',
  'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='novel_outline_volume' AND COLUMN_NAME='foreshadow_categories')=0,
  'ALTER TABLE novel_outline_volume ADD COLUMN foreshadow_categories JSON NULL COMMENT ''本卷要求伏笔类型'' AFTER min_foreshadows',
  'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='novel_outline_volume' AND COLUMN_NAME='target_word_min')=0,
  'ALTER TABLE novel_outline_volume ADD COLUMN target_word_min INT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''本卷目标字数下限'' AFTER foreshadow_categories',
  'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='novel_outline_volume' AND COLUMN_NAME='target_word_max')=0,
  'ALTER TABLE novel_outline_volume ADD COLUMN target_word_max INT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''本卷目标字数上限'' AFTER target_word_min',
  'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='novel_outline_volume' AND COLUMN_NAME='plot_progression')=0,
  'ALTER TABLE novel_outline_volume ADD COLUMN plot_progression LONGTEXT NULL COMMENT ''本卷剧情进展规划'' AFTER target_word_max',
  'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
