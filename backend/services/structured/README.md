# 结构化数据 schema 落库

从 `StructuredSchemaService.run()` 阅读主流程：读取已发布解析产物流 → 扫描各工作表的字段和行数 → 按文档版本事务替换 `structured_datasets`。

- 每个 XLSX 工作表一条数据集目录记录；CSV 使用解析标题作为表名。
- `schema_json` 是字段数组，每项包含 `name`、`type`、`nullable`。类型为 JSON 值的 `null/boolean/integer/number/string/mixed`，不是可直接执行的 SQL DDL。整数与小数合并为 number，其他异类值合并为 mixed，不从日期字符串猜类型。
- nullable 仅表示当前解析数据中观察到缺失或 null；空表字段为 null 类型且 nullable=true。它不是未来写入约束。行数按实际记录计算，不能累计 CSV 每个块里重复的总行数。
- 空表依靠解析块 `metadata.headers` 保留列名。旧的空表产物缺少表头时应重新解析，不能编造列名。
- 仅保留每个工作表的列统计，一次解码一个有界块，不累积所有数据行。最多 50 个工作表、每表 256 列；读取复用现有 S3 产物流管理。
- `model/structured_dataset/orm.py` 负责全部 SQL。写入前锁定 documents 并检查 parse_version；同版本先删除后插入，异常回滚，不覆盖其他版本。确定性 ID 关联文档、版本和工作表。
- `SCHEMA_READY` 只表示结构与来源已保存。`storage_table` 保持 NULL；不导入实际数据行，不生成 SQL，不设置 documents.READY。
- 任务复用 CHUNKING 阶段的产物处理和超时恢复，步骤显示“表结构保存中”，成功为“结构已保存，待数据入库”。失败按任务边界记录且保留已上传产物。
- `source_json_key` 指向完整解析产物；多个工作表共享此对象，结合 sheet_name 识别其中的数据。

已有数据库升级执行 `db/migrations/0004_structured_datasets.sql`；新环境使用 `db/schema.sql`。已有数据库还需具备 `0001_document_parsing_fields.sql` 的文档版本及产物字段。

本轮远端 app 库已应用 0001 和 0004，前一轮已应用 0003。验证使用真实 MySQL、独立事务内的临时文档，检查 chunk/schema 写入、同版本重放和旧版本拒绝后回滚，不留下测试记录。该验证不等于完整 Celery/解析器/S3 到数据库的端到端验收。
