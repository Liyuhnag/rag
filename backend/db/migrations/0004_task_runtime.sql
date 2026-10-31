-- 先停止旧 worker / beat，排空旧消息，再执行迁移并启动新版。
UPDATE document_tasks SET stage = 'PARSING' WHERE stage = 'SPLITTING';

UPDATE document_tasks
SET status = 'SUPERSEDED',
    result_json = JSON_REMOVE(COALESCE(result_json, JSON_OBJECT()), '$.lease_token')
WHERE status = 'FAILED' AND error_message IN (
    '文档版本不匹配，解析任务已过期',
    '文档版本已变化，索引任务已过期'
);

ALTER TABLE document_tasks
    MODIFY COLUMN status VARCHAR(32) NOT NULL DEFAULT 'PENDING'
        COMMENT 'PENDING/RUNNING/COMPLETED/FAILED/SUPERSEDED',
    MODIFY COLUMN stage VARCHAR(32) NOT NULL DEFAULT 'PENDING'
        COMMENT 'PENDING/PARSING/MERGING/CHUNKING/INDEXING/DONE';
