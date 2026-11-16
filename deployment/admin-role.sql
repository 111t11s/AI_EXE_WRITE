-- Run with a schema administrator: sudo mysql AI_TEXT < deployment/admin-role.sql
-- Idempotent; does not grant application-level privileges to any account.
SET @aitxt_admin_ddl = IF(
    EXISTS(SELECT 1 FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'app_account' AND COLUMN_NAME = 'is_admin'),
    'SELECT ''is_admin already exists'' AS result',
    'ALTER TABLE app_account ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE'
);
PREPARE aitxt_admin_statement FROM @aitxt_admin_ddl;
EXECUTE aitxt_admin_statement;
DEALLOCATE PREPARE aitxt_admin_statement;
