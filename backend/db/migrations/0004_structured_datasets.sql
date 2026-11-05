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
