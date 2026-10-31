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
    page_count INT NOT NULL DEFAULT 0 COMMENT '页数，未解析时为 0',
    parent_chunk_count INT NOT NULL DEFAULT 0 COMMENT '父块数量',
    parse_version INT NOT NULL DEFAULT 0 COMMENT '当前解析版本',
    parsed_md_key VARCHAR(768) NULL COMMENT '解析后的 Markdown key',
    parsed_json_key VARCHAR(768) NULL COMMENT '解析后的 JSON key',
    parsed_version INT NULL COMMENT '解析产物所属版本，与 parsed_md_key / parsed_json_key 同一条更新写入',
    source_sha256 CHAR(64) NULL COMMENT '源文件 SHA-256',
    error_message VARCHAR(1024) NULL COMMENT '解析或索引失败原因',
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
    KEY idx_upload_sessions_status_updated (status, updated_at),
    KEY idx_upload_sessions_s3_upload_id (s3_upload_id(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 文档处理任务。重新解析时 version 递增。
CREATE TABLE IF NOT EXISTS document_tasks (
    id VARCHAR(32) NOT NULL COMMENT '任务 ID（task_id）',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    version INT NOT NULL COMMENT '解析版本，从 1 开始',
    status VARCHAR(32) NOT NULL
        COMMENT 'PENDING/RUNNING/COMPLETED/FAILED/SUPERSEDED',
    stage VARCHAR(32) NOT NULL
        COMMENT 'PENDING/PARSING/MERGING/CHUNKING/INDEXING/DONE',
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

-- 父子块。父块用于最终上下文，子块用于 embedding 和召回。
CREATE TABLE IF NOT EXISTS document_chunks (
    id VARCHAR(64) NOT NULL COMMENT 'chunk ID，确定性哈希',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    version INT NOT NULL COMMENT '文档版本',
    chunk_level VARCHAR(16) NOT NULL COMMENT 'PARENT/CHILD',
    parent_id VARCHAR(64) NULL COMMENT '子块所属父块 ID；父块为 NULL',
    parent_index INT NULL COMMENT '子块所属父块序号；父块为 NULL',
    child_order INT NULL COMMENT '子块在父块内序号；父块为 NULL',
    chunk_index INT NOT NULL COMMENT '同层级序号',
    child_count INT NOT NULL DEFAULT 0 COMMENT '父块子块数；子块为 0',
    file_type VARCHAR(32) NOT NULL COMMENT '文件类型',
    page_start INT NULL COMMENT '起始页',
    page_end INT NULL COMMENT '结束页',
    locator VARCHAR(512) NOT NULL DEFAULT '' COMMENT '页码或表格位置',
    heading_path TEXT NOT NULL COMMENT '标题路径',
    content MEDIUMTEXT NOT NULL COMMENT 'chunk 正文',
    token_count INT NOT NULL COMMENT '估算 token 数',
    embedding_model VARCHAR(128) NULL COMMENT '子块向量模型；父块为 NULL',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_document_chunks_level_index (
        document_id, version, chunk_level, chunk_index
    ),
    KEY idx_document_chunks_document_version (document_id, version),
    KEY idx_document_chunks_parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- PDF 分段解析任务。一个文档版本可以拆成多个 MinerU 分段。
CREATE TABLE IF NOT EXISTS parse_jobs (
    id VARCHAR(32) NOT NULL COMMENT '分段任务 ID',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    task_id VARCHAR(32) NOT NULL COMMENT '文档任务 ID',
    lease_token VARCHAR(32) NULL,
    version INT NOT NULL COMMENT '解析版本',
    part_index INT NOT NULL COMMENT '分段序号，从 1 开始',
    page_start INT NOT NULL COMMENT '起始页，1-based',
    page_end INT NOT NULL COMMENT '结束页，包含',
    source_key VARCHAR(768) NOT NULL COMMENT '拆分后的临时 PDF key',
    mineru_file_id VARCHAR(128) NULL COMMENT 'MinerU file id',
    mineru_job_id VARCHAR(128) NULL COMMENT 'MinerU job id',
    status VARCHAR(32) NOT NULL
        COMMENT 'PENDING/SUBMITTED/RUNNING/DOWNLOADING/RESUBMITTING/COMPLETED/FAILED',
    attempts INT NOT NULL DEFAULT 0 COMMENT '已尝试次数',
    next_poll_at DATETIME(6) NULL COMMENT '下次轮询时间，UTC',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    result_key VARCHAR(768) NULL COMMENT '分段结果 manifest key',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_parse_jobs_document_version_part (
        document_id, version, lease_token, part_index
    ),
    KEY idx_parse_jobs_status_poll (status, next_poll_at),
    KEY idx_parse_jobs_document (document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 结构化数据目录；仅保存 schema 和来源，实际业务数据尚未导入。
CREATE TABLE IF NOT EXISTS structured_datasets (
    id VARCHAR(36) NOT NULL COMMENT '数据集 ID，按文档版本与工作表确定生成',
    document_id VARCHAR(32) NOT NULL COMMENT '来源文档 ID',
    version INT NOT NULL COMMENT '来源文档解析版本',
    sheet_name VARCHAR(255) NOT NULL COMMENT '工作表名称；CSV 使用解析标题',
    file_type VARCHAR(20) NOT NULL COMMENT 'csv/xlsx',
    schema_json JSON NOT NULL COMMENT '字段数组：name/type/nullable，类型来自 JSON 值',
    row_count BIGINT NOT NULL DEFAULT 0 COMMENT '扫描解析记录得到的数据行数',
    status VARCHAR(32) NOT NULL DEFAULT 'SCHEMA_READY'
        COMMENT 'SCHEMA_READY 仅表示结构保存完成，不表示 SQL 可查询',
    storage_table VARCHAR(255) NULL COMMENT '未来实际数据表标识；未导入时为空',
    source_json_key VARCHAR(1024) NOT NULL COMMENT '完整 JSON 解析产物 key',
    source_md_key VARCHAR(1024) NOT NULL COMMENT '对应文本解析产物 key',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_structured_datasets_version_sheet (document_id, version, sheet_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
