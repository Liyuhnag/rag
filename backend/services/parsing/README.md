# 文档解析服务

本目录把源文件解析成 `ParsedDocument`，再上传解析产物并写入数据库。父子块切分在 `services/chunking`。任务领取、状态流转和 Celery 适配由 `task/parse_document.py` 编排。

## 文件

| 文件 | 职责 |
| --- | --- |
| `models.py` | `ParseRequest`、`ParseInput`、`ParsedBlock`、`ParsedDocument`、`ParseSubmission` |
| `ports.py` | `ObjectReader`、`DocumentStore`、`TaskStore`、`DocumentParser` |
| `service.py` | `DocumentParsingService`：解析、上传、落库 |
| `dispatch.py` | 只负责把 `document_tasks` 投递到解析 Worker |
| `errors.py` | 解析业务异常 |

## 解析器

```text
parsers/
├── base.py           # BaseParser，模板方法
├── registry.py       # ParserRegistry，注册表和默认工厂
├── txt.py            # TxtParser
├── markdown.py       # MarkdownParser
├── csv.py            # CsvParser
├── xlsx.py           # XlsxParser
├── docx.py           # DocxParser
├── doc.py            # DocParser
├── pdf.py            # PdfParser
└── pdf_split.py      # 按页和大小拆分 PDF
```

## MinerU

```text
mineru/
├── client.py         # 提交、查询、下载
└── submit.py         # 把整文件提交出去
```

## 外部适配器

```text
adapters/
└── object_reader.py  # 对象存储读写
```

解析器只依赖适配器接口，不直接实现外部服务调用。

设计模式：

- Strategy：所有解析器实现 `DocumentParser`；
- Template Method：`BaseParser.parse()` 固定入口，子类实现 `_parse()`；
- Registry：`ParserRegistry` 按扩展名查找；
- Factory：`build_default_registry()` 组装默认解析器；
- Adapter：`services.parsing.mineru`、`StorageObjectReader`。

适配器由解析器内部默认创建，调用方不需要传；测试或替换实现时可以通过构造函数注入。

Markdown 原样保留文本。TXT 把标题作为一级标题，正文按空行保留段落。CSV 和 XLSX 按表头转成 JSON 对象数组。PDF 先下载到本地，超过 180 页或 180MiB 就按页拆开，再逐段交给 MinerU。DOC 和 DOCX 超过 180MiB 直接失败。提交后不等待解析结束。

Worker 已接入解析服务。解析、上传或落库抛出 `FeatureNotReady` 时，暂时把任务标成完成，避免上传链路回归。实现具体解析器后移除该兜底。

## 模型层

- `model/parse_job/`：`parse_jobs` 的模型和 ORM；
- `model/document/orm.py`：解析状态、解析产物 key 的更新函数。

## 分层

```text
task/parse_document.py
    -> 领取任务、更新状态、捕获异常
    -> DocumentParsingService.parse()
    -> 解析已完成时：upload()、persist()
    -> 分段已提交时：写入 parse_jobs，记录进度并返回

DocumentParsingService
    -> ParserRegistry
    -> 解析器自行读取对象存储
```

## 待实现

1. MinerU 轮询和分段结果合并；
2. 实现解析产物上传和落库。
