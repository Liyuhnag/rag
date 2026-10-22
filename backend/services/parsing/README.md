# 文档解析服务

本目录只定义解析服务的边界和依赖，不包含具体解析器、切分器、MySQL 或 S3 实现。

## 文件

| 文件 | 职责 |
| --- | --- |
| `models.py` | `ParseRequest`、`ParsedBlock`、`ParsedDocument`、`DocumentChunk`、`ParseResult` |
| `ports.py` | `ObjectReader`、`DocumentStore`、`TaskStore`、`ChunkStore`、`DocumentParser`、`DocumentChunker` |
| `service.py` | `DocumentParsingService` 编排入口 |
| `errors.py` | 解析和切分业务异常 |

## 依赖方向

```text
worker / task
    -> DocumentParsingService
        -> DocumentParser（PDF / Office / 文本 / 表格）
        -> DocumentChunker
        -> ObjectReader
        -> DocumentStore
        -> TaskStore
        -> ChunkStore
```

## 待实现

1. 按文件类型注册具体 `DocumentParser`；
2. 实现 PDF、DOCX、TXT、Markdown、Excel、CSV 解析器；
3. 实现父子块 `DocumentChunker`；
4. 实现 MySQL `document_chunks` 的 `ChunkStore`；
5. 实现任务领取、进度更新、完成和失败；
6. 在 `task/parse_document.py` 中注入依赖并调用服务。

当前 `DocumentParsingService` 的方法统一抛出 `FeatureNotReady`。
