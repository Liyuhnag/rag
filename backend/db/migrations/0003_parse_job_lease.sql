-- 已有数据库执行一次；新库由 schema.sql 创建。
-- 部署前先排空在途解析/合并/切分任务，再停止旧 worker 和轮询调度。
-- 历史分段无法证明所属领取批次，保留 NULL，不能回填当前任务的 token。
ALTER TABLE parse_jobs ADD COLUMN lease_token VARCHAR(32) NULL AFTER version;

ALTER TABLE parse_jobs
    DROP INDEX uk_parse_jobs_document_version_part,
    ADD UNIQUE KEY uk_parse_jobs_document_version_part
        (document_id, version, lease_token, part_index);
