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
