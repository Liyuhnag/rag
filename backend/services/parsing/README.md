# 文档解析服务

本目录把源文件解析成 `ParsedDocument`，再上传解析产物并写入数据库。父子块切分在 `services/chunking`。任务领取、状态流转和 Celery 适配由 `task/parse_document.py` 编排。完整过程见 [文件解析实现说明](../../../docs/design/文件解析实现说明.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| `models.py` | `ParseRequest`、`ParseInput`、`ParsedBlock`、`ParsedDocument`、`ParseSubmission` |
| `ports.py` | `ObjectReader`、`DocumentStore`、`TaskStore`、`DocumentParser` |
| `service.py` | `DocumentParsingService`：解析、上传、落库 |
| `dispatch.py` | 只负责把 `document_tasks` 投递到解析 Worker |
| `errors.py` | 解析业务异常 |
| `merge.py` | 按页码把分段 Markdown 合成一份 |
| `collect.py` | 收集分段结果，合并后生成对象存储文件 |
| `poll.py` | 轮询 MinerU、重新提交、下载并在分段到齐后合并 |

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
├── office.py         # Word 下载后整份提交
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

Markdown 原样保留文本。TXT 把标题作为一级标题，正文按空行保留段落。CSV 和 XLSX 按表头转成 JSON 对象数组。PDF 先下载到本地，超过 180 页或 180MiB 就按页拆开，再逐段交给 MinerU。DOC 和 DOCX 同样先下载，不超过 180MiB 就整份提交，超过则失败。PDF 分段和 Word 的 Markdown 都按起始页合成一份，并在每页前写入 `<!-- page: N -->`。提交后不等待解析结束。

Worker 已接入解析服务。本地解析结果会写入 `documents/{文档}/parsed/{版本}/merged.md`。MinerU 分段提交后由维护任务每 10 秒轮询。状态为完成时，解析 Worker 下载 `result.zip`。同一版本的分段都下载后合并写入同一位置，并带上图片。MinerU 失败或任务丢失时，最多再提交两次。

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

task/poll_parse.py
    -> 每 10 秒查询 SUBMITTED、RUNNING 的分段
    -> 完成后下载 result.zip
    -> 分段到齐后 DocumentParsingService.collect()

DocumentParsingService
    -> ParserRegistry
    -> 解析器自行读取对象存储
```
