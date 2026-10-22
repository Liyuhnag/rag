-- 已有数据库执行一次：为 documents 增加解析相关字段。
-- 新建数据库由 db/schema.sql 直接创建，不需要执行本文件。

ALTER TABLE documents
    ADD COLUMN page_count INT NOT NULL DEFAULT 0 COMMENT '页数，未解析时为 0',
    ADD COLUMN parent_chunk_count INT NOT NULL DEFAULT 0 COMMENT '父块数量',
    ADD COLUMN parse_version INT NOT NULL DEFAULT 0 COMMENT '当前解析版本',
    ADD COLUMN parsed_md_key VARCHAR(768) NULL COMMENT '解析后的 Markdown key',
    ADD COLUMN parsed_json_key VARCHAR(768) NULL COMMENT '解析后的 JSON key',
    ADD COLUMN source_sha256 CHAR(64) NULL COMMENT '源文件 SHA-256',
    ADD COLUMN error_message VARCHAR(1024) NULL COMMENT '解析或索引失败原因';
