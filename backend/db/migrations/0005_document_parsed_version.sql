-- 已有数据库执行一次；新库由 schema.sql 创建。
-- 解析产物不带版本，新增 parsed_version，与 parsed_md_key / parsed_json_key 同一条条件更新写入。
-- 下游以 parsed_version = task.version 且指针非空作为解析已完成的证据。
-- 历史行无法证明指针属于哪个版本，保留 NULL，不能用 parse_version 回填；
-- 这些文档在 CHUNKING 恢复时会被判定为产物缺失并重新解析。
ALTER TABLE documents ADD COLUMN parsed_version INT NULL COMMENT '解析产物所属版本，与 parsed_md_key / parsed_json_key 同一条更新写入' AFTER parsed_json_key;
