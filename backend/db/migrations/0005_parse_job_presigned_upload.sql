-- 已有数据库执行一次；新库由 schema.sql 创建。
-- 预签名上传链接及其过期时间；mineru_job_id 继续存 MinerU 批次编号。
ALTER TABLE parse_jobs
    ADD COLUMN upload_url VARCHAR(2048) NULL COMMENT '预签名上传链接' AFTER result_key,
    ADD COLUMN upload_expires_at DATETIME(6) NULL COMMENT '上传链接过期时间，UTC' AFTER upload_url;
