-- 仅当上一版已经创建 app_analysis_dimension、但还没有 category 列时执行一次。
-- 全新安装直接用 deployment/novel-analysis.sql，不需要执行本文件。
ALTER TABLE app_analysis_dimension
    ADD COLUMN category VARCHAR(20) NOT NULL DEFAULT 'outline' AFTER label;
