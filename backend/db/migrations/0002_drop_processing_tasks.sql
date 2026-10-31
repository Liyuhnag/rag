-- 已有数据库执行一次：旧的小文件上传任务表不再使用。
-- 新建数据库由 db/schema.sql 直接创建，不需要执行本文件。

DROP TABLE IF EXISTS processing_tasks;
