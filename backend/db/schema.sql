-- 文件元数据。文件内容在对象存储，一行对应一个已上传文件。
CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    name VARCHAR(255) NOT NULL COMMENT '原始文件名',
    file_type VARCHAR(32) NOT NULL COMMENT '扩展名',
    size_bytes BIGINT NOT NULL COMMENT '文件字节数',
    chunk_count INT NOT NULL COMMENT '分块数，未解析时为 0',
    status VARCHAR(32) NOT NULL COMMENT 'UPLOADED、PROCESSING、READY、FAILED',
    object_key VARCHAR(768) NOT NULL COMMENT '对象存储键',
    file_url VARCHAR(1024) NOT NULL COMMENT '原始文件链接',
    previewable TINYINT(1) NOT NULL COMMENT '是否可预览',
    preview_url VARCHAR(2048) NULL COMMENT '预览链接，不可预览时为空',
    uploaded_at DATETIME(6) NOT NULL COMMENT '上传时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '记录更新时间，UTC',
    PRIMARY KEY (id),
    KEY idx_documents_uploaded_at (uploaded_at),
    KEY idx_documents_name (name),
    KEY idx_documents_size (size_bytes),
    KEY idx_documents_type_uploaded (file_type, uploaded_at),
    KEY idx_documents_status_uploaded (status, uploaded_at),
    FULLTEXT KEY ft_documents_name (name) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 文档异步处理任务。对象存储、分块和向量化都由任务执行。
CREATE TABLE IF NOT EXISTS processing_tasks (
    id VARCHAR(32) NOT NULL COMMENT '任务 ID',
    name VARCHAR(255) NOT NULL COMMENT '原始文件名',
    file_type VARCHAR(32) NOT NULL COMMENT '扩展名',
    size_bytes BIGINT NOT NULL COMMENT '文件字节数',
    temp_path VARCHAR(1024) NOT NULL COMMENT '请求暂存路径，任务结束后删除',
    status VARCHAR(32) NOT NULL COMMENT 'PENDING、STORING、STORED、FAILED',
    document_id VARCHAR(32) NULL COMMENT '入库后的文档 ID',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    PRIMARY KEY (id),
    KEY idx_processing_tasks_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci

-- 大文件上传会话。与 documents 一一对应，分片状态以 S3 ListParts 为准。
CREATE TABLE IF NOT EXISTS upload_sessions (
    id VARCHAR(32) NOT NULL COMMENT '上传会话 ID（upload_id）',
    document_id VARCHAR(32) NOT NULL COMMENT '预生成的文档 ID',
    fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT '全文件 SHA-256',
    status VARCHAR(32) NOT NULL
        COMMENT 'UPLOADING/COMPLETING/COMPLETED/ABORTING/ABORTED/EXPIRED/FAILED',
    active_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (
            CASE
                WHEN status IN ('UPLOADING', 'COMPLETING') THEN fingerprint
                ELSE NULL
            END
        ) STORED COMMENT '活跃会话指纹，非活跃为 NULL',
    filename VARCHAR(255) NOT NULL COMMENT '规范化后的文件名',
    file_type VARCHAR(32) NOT NULL COMMENT '扩展名',
    size_bytes BIGINT NOT NULL COMMENT '文件总字节数',
    chunk_size INT NOT NULL COMMENT '分片大小',
    total_parts INT NOT NULL COMMENT '分片总数',
    object_key VARCHAR(1024) NOT NULL COMMENT '最终对象 key',
    s3_upload_id VARCHAR(1024) NOT NULL COMMENT 'S3 Multipart Upload ID',
    abort_reason VARCHAR(16) NULL COMMENT 'USER/EXPIRED',
    claim_version INT NOT NULL DEFAULT 0 COMMENT '接管 fencing',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    expires_at DATETIME(6) NOT NULL COMMENT '过期时间，UTC',
    completed_at DATETIME(6) NULL COMMENT '完成时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_upload_sessions_document (document_id),
    UNIQUE KEY uk_upload_sessions_active_fingerprint (active_fingerprint),
    KEY idx_upload_sessions_fingerprint (fingerprint, created_at),
    KEY idx_upload_sessions_status_expires (status, expires_at),
    KEY idx_upload_sessions_status_updated (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 文档处理任务。重新解析时 version 递增。
CREATE TABLE IF NOT EXISTS document_tasks (
    id VARCHAR(32) NOT NULL COMMENT '任务 ID（task_id）',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    version INT NOT NULL COMMENT '解析版本，从 1 开始',
    status VARCHAR(32) NOT NULL
        COMMENT 'PENDING/RUNNING/COMPLETED/FAILED',
    stage VARCHAR(32) NOT NULL
        COMMENT 'PENDING/SPLITTING/PARSING/MERGING/CHUNKING/INDEXING/DONE',
    progress INT NOT NULL DEFAULT 0 COMMENT '0-100',
    step VARCHAR(255) NOT NULL DEFAULT '' COMMENT '当前步骤描述',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    result_json JSON NULL COMMENT '阶段检查点和结果',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_document_tasks_document_version (document_id, version),
    KEY idx_document_tasks_status_updated (status, updated_at),
    KEY idx_document_tasks_document (document_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
