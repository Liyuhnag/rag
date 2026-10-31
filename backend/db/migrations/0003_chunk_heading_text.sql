-- 保留完整标题路径；长 ATX 标题和多层标题可能超过原 VARCHAR(1024)。
-- 升级已有环境时执行；新环境直接使用 schema.sql。
ALTER TABLE document_chunks
    MODIFY COLUMN heading_path TEXT NOT NULL COMMENT '标题路径';
