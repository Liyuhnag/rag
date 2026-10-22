# 文档解析服务

本目录实现解析、切分、落库的核心业务；任务领取、状态流转、异常重试和 Celery 适配由 `task/parse_document.py` 编排。

## 文件

| 文件 | 职责 |
| --- | --- |
| `models.py` | `ParseRequest`、`ParseInput`、`ParsedBlock`、`ParsedDocument`、`ParseResult` |
| `ports.py` | `ObjectReader`、`DocumentStore`、`TaskStore`、`ChunkStore`、`DocumentParser`、`DocumentChunker` |
| `service.py` | `DocumentParsingService`：解析、切分、落库 |
| `dispatch.py` | 只负责把 `document_tasks` 投递到解析 Worker |
| `errors.py` | 解析和切分业务异常 |

## 解析器

```text
parsers/
├── base.py           # BaseParser，模板方法
├── registry.py       # ParserRegistry，注册表和默认工厂
├── txt.py            # TxtParser
├── markdown.py       # MarkdownParser
├── csv.py            # CsvParser
├── xlsx.py           # XlsxParser
├── xls.py            # XlsParser
├── docx.py           # DocxParser
├── doc.py            # DocParser
└── pdf.py            # PdfParser
```

## 外部适配器

```text
adapters/
├── mineru.py         # MinerU 客户端
└── libreoffice.py    # LibreOffice 转换器
```

解析器只依赖适配器接口，不直接实现外部服务调用。

设计模式：

- Strategy：所有解析器实现 `DocumentParser`；
- Template Method：`BaseParser.parse()` 固定入口，子类实现 `_parse()`；
- Registry：`ParserRegistry` 按扩展名查找；
- Factory：`build_default_registry()` 组装默认解析器；
- Adapter：`MinerUClient`、`LibreOfficeConverter`。

适配器由解析器内部默认创建，调用方不需要传；测试或替换实现时可以通过构造函数注入。

当前所有 `_parse()` 都抛出 `FeatureNotReady`，具体解析逻辑后续实现。

## 模型层

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
    -> ParserRegistry
    -> DocumentChunker
    -> ChunkStore
    -> ObjectReader
```

## 待实现

1. 实现各解析器的 `_parse()`；
2. 实现父子块 `DocumentChunker`；
3. 实现 `ChunkStore.replace_for_version()`；
4. 在 `task/parse_document.py` 中完成任务编排。
