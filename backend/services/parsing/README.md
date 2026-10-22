# 文档解析服务

本目录只实现解析、切分、落库的核心业务。

任务领取、状态流转、异常重试和 Celery 适配由 `task/parse_document.py` 编排。

## 文件

| 文件 | 职责 |
| --- | --- |
| `models.py` | `ParseRequest`、`ParsedBlock`、`ParsedDocument`、`ParseResult` |
| `ports.py` | `ObjectReader`、`DocumentStore`、`TaskStore`、`ChunkStore`、`DocumentParser`、`DocumentChunker` |
| `service.py` | `DocumentParsingService`：解析、切分、落库 |
| `dispatch.py` | 只负责把 `document_tasks` 投递到解析 Worker |
| `errors.py` | 解析和切分业务异常 |

## 模型层

持久化模型已经放在：

- `model/document_chunk/`：`document_chunks` 的模型和 ORM；
- `model/parse_job/`：`parse_jobs` 的模型和 ORM；
- `model/document/orm.py`：解析状态、解析产物 key 的更新函数。

## 分层

```text
task/parse_document.py
    -> 领取任务、更新状态、捕获异常
    -> DocumentParsingService.parse()
    -> DocumentParsingService.split()
    -> DocumentParsingService.persist()

DocumentParsingService
    -> DocumentParser
    -> DocumentChunker
    -> ChunkStore
    -> ObjectReader
```

## 待实现

1. 按文件类型注册具体 `DocumentParser`；
2. 实现 PDF、DOCX、TXT、Markdown、Excel、CSV 解析器；
3. 实现父子块 `DocumentChunker`；
4. 实现 `ChunkStore.replace_for_version()` 的具体适配器；
5. 在 `task/parse_document.py` 中完成任务编排。

当前 `DocumentParsingService` 的方法统一抛出 `FeatureNotReady`。
