# 文档父子块切分

从 `service.py` 的 `DocumentChunkingService.run()` 阅读功能：检查版本 → 打开 S3 产物流 → LlamaIndex 生成父子节点 → 转为业务块 → ORM 事务替换。

## 目录职责

- `service.py`：主流程编排、非空校验。响应流和事务的生命周期都能从入口读到。
- `token.py`：调用 LlamaIndex 原生层级解析器，将节点关系映射为 `DocumentChunk`；保留版本、确定性 ID 和来源信息。
- `source.py`：直接消费 S3 响应流，增量解码解析 JSON/Markdown；上下文退出即关闭响应体。
- `common/models.py`、`common/errors.py`：参数与异常。参数直接对应框架：父块 1600 token、子块 400 token、子块重叠 80 token。

切分使用 `llama-index-core` 的 `HierarchicalNodeParser` 和 `SentenceSplitter`。句子边界、递归切分、重叠、父子节点关系由框架处理。业务层不继承框架私有方法，不自行实现字符窗口、重叠补齐、短尾合并或 Markdown 语法识别。

## 行为边界

- XLSX/CSV 在解析任务中分流：保存 JSON 解析产物后，当前任务以“解析完成，待结构化入库”结束，不进入 CHUNKING、不创建父子块、不标记文档 READY。结构化入库与 Text2SQL 尚未实现；不能把解析任务完成理解为数据已可查询。PDF 等文档中的表格仍随正文进入 RAG。
- 采用框架原生文本处理行为，包括去除块首尾空白。块内容用于检索，不承诺逐字符重建原文件。
- token 计数使用 `cl100k_base`，不再使用中文/ASCII 字符估算。首次初始化可能由 tiktoken 下载词表，离线环境应在构建阶段预热相同缓存。它并不等同于目标 embedding 模型 tokenizer，接入模型时应统一计数器。句子 tokenizer 使用 NLTK 原生 `PunktSentenceTokenizer`，无需运行时下载 NLTK 数据。
- 来源信息按输入组保守继承，不声称子块具有精确字符偏移；不会跨不同页、工作表或已有标题路径混合来源。
- 不再承诺自定义 Markdown 标题隔离、代码块完整、表格子块重复表头或短尾合并规则。这些不是框架原生保证，若确有检索需求，应独立验收后再扩展。
- 同一文档版本、参数和输入顺序生成相同业务块 ID。框架内部节点 ID 仅用于解析父子关系，不作为数据库主键。

## 存储和资源边界

输入直接从 S3 响应流有界读取，UTF-8 跨网络分片由增量解码器处理，不写临时文件。切分按有界输入组调用框架，避免一次构造完整文档的节点列表；ORM 每 200 条批量写入。

写入前锁定 `documents` 并复核 `parse_version`，块和计数在同一事务提交。网络中断、解码错误或切分失败使事务回滚，重试从对象开头重放。流式读取发生在替换事务内，网络耗时也会延长文档行锁持有时间。

单条持久化 JSON 记录上限为 4Mi 字符。已有数据库须先执行 `backend/db/migrations/0003_chunk_heading_text.sql`；本次没有连接或修改真实数据库。

非结构化文档的 Celery 和 MinerU 两条路径均先发布解析产物，再切分，成功后完成当前任务。文档 `READY` 留给后续索引完成。Worker 在 CHUNKING 中断后，维护任务补投超过 10 分钟的切分任务并重放已存产物；不覆盖更早的 PARSING/MERGING 恢复。

## 验证

从 `backend` 运行：

```bash
.venv/bin/python -m pytest tests/test_chunking.py tests/test_chunking_service.py tests/test_chunk_source.py tests/test_chunk_store.py tests/test_parse_flow.py tests/test_poll_parse.py -q
```

测试使用存储替身验证算法调用、业务映射及编排，不替代真实 MySQL、S3、MinerU 和大文件端到端验证。
