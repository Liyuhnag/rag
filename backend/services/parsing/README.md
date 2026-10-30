# 文档解析服务

本目录把源文件解析成 `ParsedDocument`，再上传解析产物并写入数据库。父子块切分在 `services/chunking`。任务领取、状态流转和 Celery 适配由 `task/parse_document.py` 编排。完整过程见 [文件解析实现说明](../../../docs/design/文件解析实现说明.md)。

当前生产链路采用块迭代器和磁盘暂存，大内容不再通过完整 bytes 在模块间传递。设计、兼容性变化、内存实测及限制见 [文档转换有界内存重构](../../../docs/design/文档转换有界内存重构.md)。

## 任务版本门禁

首次上传在同一事务写入文档 `parse_version=1` 与任务 `version=1`。Worker 只执行与当前文档版本相等的任务，开始解析时通过 `begin_parse` 条件更新为 `PROCESSING`；Worker 不分配或递增文档版本。

解析领取后、MinerU 分段进入合并时，以及切分或表结构保存因版本不符而拒绝写入时，统一通过 `services/document/versioning.py` 使用该任务的 `lease_token` 标记 `FAILED`。版本失败只结束任务，不修改当前文档的状态和产物。解析产物发布与派生数据落库继续保留各自的版本条件，避免计算期间的版本变化覆盖当前结果。INDEXING 的版本不匹配仍然显式失败。

## 文件

| 文件 | 职责 |
| --- | --- |
| `models.py` | `ParseRequest`、`ParseInput`、`ParsedBlock`、`ParsedDocument`、`ParseSubmission` |
| `ports.py` | `ObjectReader`、`DocumentStore`、`TaskStore`、`DocumentParser` |
| `service.py` | `DocumentParsingService`：解析、上传、落库 |
| `dispatch.py` | 只负责把 `document_tasks` 投递到解析 Worker |
| `errors.py` | 解析业务异常 |
| `merge.py` | 按页码把分段 Markdown 合成一份 |
| `local_artifacts.py` | 单遍消费本地块，增量写产物并上传 |
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

官方接口协议见 [MinerU API 文档](https://mineru.net/doc/docs/index.html)。配置 `MINERU_API_URL=https://mineru.net`（域名，不含 `/api/v4`），`MINERU_API_KEY` 使用 MinerU 签发的 Bearer Token，不是 OpenXLab AK/SK。

每个分段创建一个单文件批次：`POST /api/v4/file-urls/batch`（`model_version=vlm`）→ 向签名链接流式 `PUT` 原始文件 → 上传完成自动提交 → `GET /api/v4/extract-results/batch/{batch_id}` 轮询 → 从 `full_zip_url` 下载 ZIP。签名上传与下载不携带 API Token；数据传输有界且可重试。

内部保留原任务字段：`mineru_job_id` 保存官方 `batch_id`，`mineru_file_id` 保存提交的 `data_id`（源文件 SHA-256）。waiting-file/pending 映射 queued，converting/running 映射 running，done 映射 completed。业务错误码也会校验，不把 HTTP 200 等同于成功。

官方 ZIP 优先使用 `full.md`，保留 JSON 结果文件并在分段 manifest 的 `json_keys` 中记录来源；图片按相对路径发布到 S3。合并 JSON 是分段索引，chunk 正文读取合并 Markdown。

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

## 官方服务验证

2026-10-28 使用生成的单页 PDF，调用现有 `parse_document.run`、`poll_job`、`download_job`，真实访问 MinerU 官方 API、S3 和 MySQL，验证任务达到 COMPLETED（“解析与切分完成”），产物读回及正文匹配成功，落库 1 个父块、1 个子块。验证文档 ID 为 `bde5467d411a4d80bcf3f76032557538`，独立测试记录与产物保留用于检查。

本次验证直接调用任务处理函数并同步触发下载，未覆盖 Redis 消息投递、Celery Worker/Beat 常驻调度、浏览器上传或大文件验收。后端回归为 306 passed，Ruff 检查通过。

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
