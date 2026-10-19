# 文档解析详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-IDX-020 |
| 文档版本 | V0.9.1（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 索引阶段——PDF/Word 解析、Excel/CSV 转 JSON、解析结果合并 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |
| V0.9.1 | 2026-10-19 | 架构组 | 评审前修订：documents 表增加 parent_chunk_count，明确 chunk_count 为子块数量。 |

## 1. 概述

### 1.1 编写目的

本文档定义文档解析阶段的分流规则、MinerU 调用方式、PDF 拆分算法、Excel/CSV 转 JSON 规则、解析结果合并方式、异常处理和验收标准，作为解析模块开发和测试的依据。

### 1.2 适用范围

本文档适用于：

- PDF、DOC、DOCX、XLS、XLSX、CSV、TXT、Markdown 的解析；
- 自建 MinerU 服务的提交、轮询和结果下载；
- 解析产物在 SeaweedFS 中的组织；
- 解析任务状态和进度计算。

本文档不覆盖上传、切块、embedding 和索引写入。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| MinerU | 自建文档解析服务，负责版式分析和 Markdown/JSON 输出。 |
| OCR | Optical Character Recognition，光学字符识别。 |
| Parse Job | 一份文档拆分后的单个 MinerU 解析分段任务。 |
| 解析产物 | `merged.md`、`merged.json`、图片资源和清单文件。 |
| 文本投影 | 将 Excel/CSV 结构化数据转换为可检索文本的过程。 |
| `parse_version` | 文档解析版本，重新解析时递增。 |

### 1.4 设计依据

- [RAG 文档索引阶段总体设计](./00-overview.md)
- [大文件上传详细设计](./01-large-file-upload.md)
- [向量化与 Milvus 存储详细设计](./03-vectorization-milvus.md)
- MinerU 自建服务 API 文档
- PyMuPDF、openpyxl、LibreOffice 官方文档

### 1.5 设计目标

把上传后的原始文件转换为统一的、可切块的中间产物：

- PDF：通过自建 MinerU 解析为 Markdown + JSON + 图片资源；
- DOC/DOCX：优先使用 MinerU 支持的格式，或转换为 PDF 后解析；
- XLS/XLSX：原生解析为结构化 JSON；
- CSV：原生解析为 JSON；
- TXT/MD：原生读取、规范化；
- 大文件按 MinerU 限制自动拆分，解析后按页码顺序合并；
- 保存解析产物到 SeaweedFS，供索引阶段切块；
- 解析过程异步、可重试、可观测进度。

## 2. 非目标

- 不做 OCR 模型自研；
- 不做图片内容理解、图表问答；
- 不做 Excel 公式重算，只读取缓存值；
- 不做 PDF 手动校对 UI；
- 不做 MinerU 模型训练和调参；
- 不做多文件合并解析。

## 3. 文件类型路由

| 扩展名 | 解析方式 | 说明 |
| --- | --- | --- |
| `pdf` | MinerU | 按页/按大小拆分 |
| `doc` | LibreOffice 转 PDF 后走 MinerU | 旧 Word 格式 |
| `docx` | MinerU 直接解析或转 PDF | 以部署的 MinerU 版本支持为准 |
| `xls` | LibreOffice 转 xlsx 后原生解析 | 旧 Excel 格式 |
| `xlsx` | openpyxl 原生解析 | 输出结构化 JSON |
| `csv` | 原生解析 | 输出 JSON 记录 |
| `txt` | 原生读取 | UTF-8/GBK 探测 |
| `md` / `markdown` | 原生读取 | 保留标题结构 |

路由在 parse worker 中根据 `documents.file_type` 决定。未知类型返回不可重试错误。

## 4. 解析流程

```text
parse_document(document_id, task_id)
  │
  ├── 读取 document，状态置 PARSING
  ├── 从 SeaweedFS 下载原始文件到 worker 临时目录
  ├── 按 file_type 路由
  │     ├── PDF/DOC/DOCX -> MinerU 分段解析
  │     ├── XLSX/XLS/CSV -> 原生 JSON
  │     └── TXT/MD -> 原生文本
  ├── 合并/生成 parsed/merged.md + merged.json + manifest.json
  ├── 更新 documents.page_count / parsed_*_key
  ├── 更新 documents.previewable = 1，preview_url 指向 merged.md
  ├── 文档状态置 CHUNKING
  └── 投递 chunk_and_index 到 index 队列
```

### 4.1 临时目录

```text
{temporary_dir}/rag-parse/{document_id}/{task_id}/
  ├── source.pdf
  ├── parts/
  │     ├── part-0001.pdf
  │     └── part-0002.pdf
  ├── mineru/
  │     ├── part-0001/
  │     └── part-0002/
  └── merged/
```

任务结束或失败后由 `finally` 清理；失败时保留 7 天用于排查，由定时任务删除。

### 4.2 下载原始文件

- 使用 boto3 `download_file` 或流式写入临时文件；
- 300MB 文件写入 worker 本地磁盘；
- 检查磁盘剩余空间，低于 `PARSE_MIN_FREE_DISK_BYTES` 时失败并提示；
- 下载超时 300 秒，失败重试 3 次。

## 5. PDF / MinerU 解析

### 5.1 MinerU 部署前提

- MinerU 部署在独立 GPU 机器；
- 通过 HTTP API 访问，后端配置 `MINERU_API_URL` 和 `MINERU_API_KEY`；
- MinerU 只暴露内网地址，不直接暴露公网；
- 后端不依赖 MinerU 的 job 状态持久化，因为自建 V1 API 的任务索引在进程内，重启会丢失。

### 5.2 单文件限制

自建 MinerU V1 API 的量级限制：

- 单文件约 200MiB；
- 单批最多 100 个文件；
- 具体页数限制以部署版本为准，按 200 页预留。

为了安全，系统使用更保守的目标：

- 单段最大 180 页；
- 单段最大 180MiB；
- 两者任一超限就继续拆分。

### 5.3 拆分算法

使用 PyMuPDF（`pymupdf`，导入名 `fitz`）：

1. 打开 PDF，读取 `page_count` 和文件大小；
2. 如果 `page_count <= 180` 且 `size <= 180MiB`，直接作为一段；
3. 否则估算每页平均大小：

   ```text
   avg_page_bytes = file_size / page_count
   pages_by_size = floor(180MiB / avg_page_bytes)
   pages_per_part = max(1, min(180, pages_by_size))
   ```

4. 按 `pages_per_part` 生成分段，写入临时 PDF；
5. 逐段检查实际大小；
6. 如果某段超过 180MiB，将该段页数减半，递归拆分；
7. 如果单页本身超过 180MiB，返回不可重试错误 `PDF_PAGE_TOO_LARGE`，提示用户压缩或拆分源文件；
8. 段编号从 1 开始，记录 `page_start`、`page_end`；
9. 每段计算 SHA-256，用于 MinerU 文件复用和幂等。

拆分时保留原始页面顺序，不重新排版。默认不加重叠页；如果开启 `PARSE_PAGE_OVERLAP=1`，每段包含上一段最后一页，合并时按页码去重。

### 5.4 加密和损坏 PDF

- 先尝试空密码打开；
- 加密且无法打开：返回 `PDF_ENCRYPTED`，不可重试；
- 损坏或页数为 0：返回 `PDF_CORRUPTED`，不可重试；
- 单页超限：返回 `PDF_PAGE_TOO_LARGE`。

### 5.5 MinerU 客户端

封装 `MinerUClient`：

```python
class MinerUClient:
    def submit(self, file_path: Path, filename: str, sha256: str) -> MinerUJob: ...
    def status(self, job_id: str) -> MinerUStatus: ...
    def download(self, job_id: str, target_dir: Path) -> Path: ...
```

调用生命周期：

1. 创建上传资源；
2. 上传分段 PDF 字节；
3. 提交解析任务，保存 `file_id`、`job_id`；
4. 轮询任务状态；
5. 完成后下载结果 zip；
6. 解析 zip，提取 Markdown、JSON、图片。

要求：

- `Authorization: Bearer {MINERU_API_KEY}`；
- 连接超时 10 秒，读取超时 60 秒；
- 上传和下载超时 300 秒；
- 429/5xx 退避重试；
- 4xx 中除 408/429 外不重试；
- 记录 MinerU 返回的 `job_id` 和错误信息；
- 不把 MinerU API Key 写进日志。

### 5.6 轮询调度

MinerU 任务不在 Celery worker 中同步等待，而由 beat 调度：

```text
poll_parse_jobs（每 10 秒）
  -> SELECT parse_jobs
     WHERE status IN ('SUBMITTED', 'RUNNING')
       AND next_poll_at <= now()
     ORDER BY next_poll_at
     LIMIT 50
     FOR UPDATE SKIP LOCKED
  -> 逐个查询 MinerU 状态
  -> 更新 parse_jobs
```

状态处理：

- `queued` / `running`：更新 `next_poll_at = now + 10s`；
- `completed`：状态置 `DOWNLOADING`，投递 `download_parse_result`；
- `failed`：按次数重试；超过最大次数置 `FAILED`；
- 404 / job 不存在：视为 MinerU 重启导致状态丢失，重新提交该分段；
- 429 / 5xx：指数退避，`next_poll_at = now + min(60, 2^attempt * 5)`。

### 5.7 MinerU 并发控制

- `MINERU_CONCURRENCY` 控制同时运行的分段任务数，默认 2；
- 使用 Redis 信号量或 ZSET 记录活跃 job；
- 提交前获取槽位，任务终止后释放；
- 槽位带 TTL，防止 worker 崩溃导致永久占用；
- 超出并发时分段保持 `PENDING`，由调度器稍后提交。

### 5.8 结果下载与解压

MinerU 结果通常是 zip，包含：

- Markdown；
- 内容 JSON；
- 图片资源；
- 可能的页面/版面信息。

安全要求：

- 限制 zip 解压后总大小，例如 `PARSE_MAX_RESULT_BYTES=1GiB`；
- 拒绝绝对路径和 `..`，防止 zip slip；
- 限制文件数量，例如 10000；
- 只允许预期扩展名；
- 解压后写入 `{temp}/mineru/part-{n}/`。

保存到 SeaweedFS：

```text
documents/{document_id}/parse/parts/{part_index}/content.md
documents/{document_id}/parse/parts/{part_index}/content.json
documents/{document_id}/parse/parts/{part_index}/images/...
documents/{document_id}/parse/parts/{part_index}/manifest.json
```

`parse_jobs.result_key` 保存该分段的 manifest key。

## 6. Excel / CSV 转 JSON

### 6.1 解析方式选型

- MinerU 主要面向版面文档，不擅长结构化表格；
- Excel 需要保留工作表、表头、行、列、单元格类型；
- 原生解析更快、更可控、成本更低；
- 后续可以按行/按列生成自然语言文本，供 embedding 和 BM25 使用。

### 6.2 Excel 读取

使用 `openpyxl`：

```python
load_workbook(
    filename=path,
    read_only=True,
    data_only=True,
    keep_links=False,
)
```

- `read_only=True`：按行流式读取，降低内存；
- `data_only=True`：读取公式的缓存值；没有缓存值时输出 `null`；
- `keep_links=False`：不加载外部链接；
- 不加载宏，`xlsm` 不在允许类型内；
- `.xls` 先用 LibreOffice 转 `.xlsx`，再读取。

### 6.3 JSON 结构

每个工作表生成一个 JSON 对象，多个工作表写入 `sheets` 数组：

```json
{
  "type": "excel",
  "filename": "sales.xlsx",
  "parsed_at": "2026-10-19T12:00:00Z",
  "sheets": [
    {
      "name": "销售数据",
      "index": 0,
      "headers": ["日期", "区域", "金额"],
      "rows": [
        {"日期": "2026-01-01", "区域": "华东", "金额": 100},
        {"日期": "2026-01-02", "区域": "华南", "金额": 200}
      ],
      "row_count": 2,
      "column_count": 3,
      "truncated": false,
      "merged_ranges": ["A1:C1"]
    }
  ]
}
```

规则：

- 第一行非空行作为表头；
- 表头为空或重复时，使用 `column_1`、`column_2` 或 Excel 列名 `A`、`B`；
- 空单元格输出 `null`，不省略，保证行结构稳定；
- 日期时间转为 ISO 8601 字符串；
- 数字保持 JSON number；
- 布尔值保持 boolean；
- 公式读取缓存值；无缓存值时输出 `null`，并在 `formula_cells` 中记录单元格坐标；
- 合并单元格保留 `merged_ranges`，文本投影时使用左上角值填充；
- 单文件超过限制时拒绝解析，返回明确错误，不静默截断。

### 6.4 大文件限制

| 限制 | 默认值 | 行为 |
| --- | --- | --- |
| 最大工作表数 | 50 | 超过拒绝 |
| 单表最大行数 | 200000 | 超过拒绝 |
| 单表最大列数 | 256 | 超过拒绝 |
| 单文件最大单元格数 | 2000000 | 超过拒绝 |
| 解压后最大字节数 | 2GiB | 超过拒绝 |

Excel 是 zip 格式，需要防止 zip bomb：读取前检查压缩包解压后大小，或设置上述限制。

### 6.5 CSV 读取

- 编码探测：优先 `utf-8-sig`，失败后尝试 `gbk`；
- 分隔符探测：逗号、制表符、分号；
- 第一行作为表头；
- 输出与 Excel 相同的 `headers`/`rows` 结构；
- 单文件超过 200000 行或 2000000 单元格时拒绝；
- 流式读取，避免一次性加载。

### 6.6 Excel/CSV 的文本投影

索引阶段不直接对 JSON 原文做 embedding，而是生成文本投影：

```text
工作表：销售数据
列：日期、区域、金额
第 2-51 行：
日期=2026-01-01；区域=华东；金额=100
日期=2026-01-02；区域=华南；金额=200
...
```

规则：

- 每个 chunk 重复表头；
- 按行分组，每组不超过 800 token；
- `heading_path = "销售数据"`；
- `locator = "sheet:销售数据!A2:C51"`；
- `page_start` / `page_end` 为空；
- 合并单元格在投影时填充左上角值。

### 6.7 输出对象

```text
documents/{document_id}/parsed/merged.json          # 完整结构化 JSON
documents/{document_id}/parsed/excel/sheet-0.json   # 单表 JSON（可选）
documents/{document_id}/parsed/merged.md            # 文本投影/Markdown
documents/{document_id}/parsed/manifest.json        # 产物清单
```

`merged.md` 由 JSON 投影生成，供统一 chunker 使用；`merged.json` 保留结构化数据，供后续结构化检索或重新投影。

## 7. DOC / DOCX

优先判断 MinerU 是否直接支持 DOCX：

- 支持：直接提交，按 MinerU 返回的页数/页范围拆分；
- 不支持：使用 LibreOffice headless 转为 PDF：

  ```bash
  libreoffice --headless --convert-to pdf --outdir {temp} source.docx
  ```

转换后按 PDF 流程拆分和解析。转换失败返回 `DOC_CONVERT_FAILED`。

限制：

- 转换超时 300 秒；
- 转换后的 PDF 同样受 180 页/180MiB 限制；
- 临时 PDF 解析完成后删除。

## 8. TXT / Markdown

- TXT：编码探测后读取，按空行/段落保留结构；
- Markdown：保留标题、列表、表格、代码块；
- 输出 `merged.md`，`merged.json` 保存原始文本和统计信息；
- 不经过 MinerU；
- 超大文本按 10MB 分段读取，但最终产物仍是单对象。

## 9. 合并解析结果

### 9.1 合并顺序

按 `parse_jobs.page_start` 升序合并：

```text
part-0001 (pages 1-100)
part-0002 (pages 101-200)
part-0003 (pages 201-300)
```

### 9.2 页码标记

在每个分段 Markdown 前插入：

```html
<!-- page: 1 -->
```

如果 MinerU 的 JSON 提供逐页内容，按页插入标记；否则按分段起始页插入。页码标记用于后续 chunk 的 `page_start`/`page_end` 和引用。

### 9.3 重叠去重

如果启用 `PARSE_PAGE_OVERLAP=1`：

- 分段之间重叠 1 页；
- 合并时按页码标记去重；
- 只保留第一次出现的页面内容；
- 在 manifest 中记录重叠页。

### 9.4 图片和附件

- 每段的 `images/` 上传到：
  `documents/{document_id}/parsed/assets/part-{n}/...`；
- Markdown 中的相对图片链接重写为对象存储 URL 或统一相对路径；
- 保留原始文件名，冲突时加前缀；
- 图片不参与文本 embedding，但保留供预览和后续多模态扩展。

### 9.5 JSON 合并

- 每段 `content.json` 按页/块合并；
- 保留 `page_number`、`block_type`、`text`、`bbox`（如有）；
- 生成 `parsed/merged.json`：

  ```json
  {
    "type": "pdf",
    "page_count": 300,
    "parts": [
      {"part_index": 1, "page_start": 1, "page_end": 100},
      {"part_index": 2, "page_start": 101, "page_end": 200}
    ],
    "blocks": [
      {"page": 1, "type": "text", "text": "..."}
    ]
  }
  ```

### 9.6 解析产物清单

`parsed/manifest.json`：

```json
{
  "document_id": "a1b2...",
  "file_type": "pdf",
  "page_count": 300,
  "parse_version": 1,
  "source_key": "documents/a1b2.../source/report.pdf",
  "merged_md_key": "documents/a1b2.../parsed/merged.md",
  "merged_json_key": "documents/a1b2.../parsed/merged.json",
  "parts": [
    {
      "part_index": 1,
      "page_start": 1,
      "page_end": 100,
      "mineru_job_id": "...",
      "result_key": "documents/a1b2.../parse/parts/1/manifest.json"
    }
  ],
  "created_at": "2026-10-19T12:00:00Z"
}
```

## 10. 数据模型

### 10.1 `parse_jobs`

```sql
CREATE TABLE IF NOT EXISTS parse_jobs (
    id VARCHAR(32) NOT NULL COMMENT '分段任务 ID',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    task_id VARCHAR(32) NOT NULL COMMENT '文档任务 ID',
    version INT NOT NULL COMMENT '解析版本，重新解析时递增',
    part_index INT NOT NULL COMMENT '分段序号，从 1 开始',
    page_start INT NOT NULL COMMENT '起始页，1-based',
    page_end INT NOT NULL COMMENT '结束页，包含',
    source_key VARCHAR(768) NOT NULL COMMENT '拆分后的临时 PDF key',
    mineru_file_id VARCHAR(128) NULL COMMENT 'MinerU file id',
    mineru_job_id VARCHAR(128) NULL COMMENT 'MinerU job id',
    status VARCHAR(32) NOT NULL COMMENT 'PENDING/SUBMITTED/RUNNING/DOWNLOADING/COMPLETED/FAILED',
    attempts INT NOT NULL DEFAULT 0 COMMENT '已尝试次数',
    next_poll_at DATETIME(6) NULL COMMENT '下次轮询时间，UTC',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    result_key VARCHAR(768) NULL COMMENT '分段结果 manifest key',
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_parse_jobs_document_version_part (document_id, version, part_index),
    KEY idx_parse_jobs_status_poll (status, next_poll_at),
    KEY idx_parse_jobs_document (document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 10.2 `documents` 新增字段

```sql
ALTER TABLE documents
    ADD COLUMN page_count INT NOT NULL DEFAULT 0,
    ADD COLUMN parent_chunk_count INT NOT NULL DEFAULT 0,
    ADD COLUMN parse_version INT NOT NULL DEFAULT 0,
    ADD COLUMN parsed_md_key VARCHAR(768) NULL,
    ADD COLUMN parsed_json_key VARCHAR(768) NULL,
    ADD COLUMN source_sha256 CHAR(64) NULL,
    ADD COLUMN error_message VARCHAR(1024) NULL;
```

`chunk_count` 表示子块数量（向量化和召回单元），`parent_chunk_count` 表示父块数量（最终返回单元）。首次上传完成时 `parse_version = 1`。重新解析时递增，并在 `document_tasks` 和 `parse_jobs` 中写入同一个版本号，避免新旧任务互相覆盖。

### 10.3 文档任务阶段

解析阶段更新 `document_tasks`：

| stage | progress | step 示例 |
| --- | --- | --- |
| `SPLITTING` | 0-5 | 正在拆分 PDF |
| `PARSING` | 5-70 | 已解析 5/12 段 |
| `MERGING` | 70-80 | 正在合并 Markdown |
| `CHUNKING` | 80-85 | 交给索引阶段 |

## 11. 进度计算

PDF：

```text
parse_progress = completed_parts_weight / total_parts_weight
progress = 5 + floor(parse_progress * 65)
```

每段权重按页数计算，而不是按段数，避免大段和小段权重相同。

Excel/CSV/TXT/MD：

- 读取阶段：5-40；
- JSON/文本生成阶段：40-70；
- 合并/清单阶段：70-80。

MinerU 轮询过程中每完成一段更新一次进度；不要在每个轮询周期都写库，按状态变化更新。

## 12. 错误处理

| 错误 | code | 可重试 | 处理 |
| --- | --- | --- | --- |
| 文件类型不支持 | `UNSUPPORTED_FILE_TYPE` | 否 | 文档 FAILED |
| PDF 加密 | `PDF_ENCRYPTED` | 否 | 提示用户解密 |
| PDF 损坏 | `PDF_CORRUPTED` | 否 | 文档 FAILED |
| 单页超限 | `PDF_PAGE_TOO_LARGE` | 否 | 提示压缩/拆分 |
| MinerU 429/5xx | `MINERU_UNAVAILABLE` | 是 | 退避重试 |
| MinerU job 丢失 | `MINERU_JOB_LOST` | 是 | 重新提交分段 |
| MinerU 解析失败 | `MINERU_PARSE_FAILED` | 视情况 | 重试后 FAILED |
| 结果 zip 非法 | `INVALID_PARSE_RESULT` | 否 | 文档 FAILED |
| Excel 超限 | `EXCEL_TOO_LARGE` | 否 | 提示拆分文件 |
| Excel 损坏 | `EXCEL_CORRUPTED` | 否 | 文档 FAILED |
| DOC 转换失败 | `DOC_CONVERT_FAILED` | 是 | 重试后 FAILED |
| 磁盘不足 | `INSUFFICIENT_DISK` | 是 | 告警并重试 |
| 下载超时 | `SOURCE_DOWNLOAD_TIMEOUT` | 是 | 重试 |

失败时：

- `parse_jobs.status = FAILED`，记录 `error_message`；
- `document_tasks.status = FAILED`，保留当前 stage；
- `documents.status = FAILED`，记录错误；
- 已成功分段的结果保留，便于重试；
- 支持后续增加“重新解析”接口，递增 `parse_version`。

## 13. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MINERU_API_URL` | 无 | MinerU HTTP 地址 |
| `MINERU_API_KEY` | 空 | MinerU API Key |
| `MINERU_CONCURRENCY` | `2` | 并发分段数 |
| `MINERU_MAX_PAGES` | `180` | 单段最大页数 |
| `MINERU_MAX_BYTES` | `188743680` | 单段最大字节数，180MiB |
| `MINERU_POLL_INTERVAL_SECONDS` | `10` | 轮询间隔 |
| `MINERU_JOB_TIMEOUT_SECONDS` | `7200` | 单段解析超时 |
| `MINERU_MAX_ATTEMPTS` | `3` | 单段最大尝试次数 |
| `PARSE_PAGE_OVERLAP` | `0` | 分段重叠页数，0 或 1 |
| `PARSE_MIN_FREE_DISK_BYTES` | `1073741824` | worker 最低剩余磁盘 |
| `PARSE_MAX_RESULT_BYTES` | `1073741824` | 解压结果上限 |
| `PARSE_TMP_TTL_DAYS` | `7` | 临时产物保留天数 |
| `EXCEL_MAX_ROWS` | `200000` | 单表最大行数 |
| `EXCEL_MAX_COLUMNS` | `256` | 单表最大列数 |
| `EXCEL_MAX_CELLS` | `2000000` | 单文件最大单元格数 |

## 14. 测试

### 14.1 单元测试

- PDF 拆分：页数、字节数、单页超限、递归拆分；
- 页码范围和分段顺序；
- Excel 表头识别、空单元格、合并单元格、日期、公式缓存值；
- CSV 编码和分隔符探测；
- JSON 结构和文本投影；
- Markdown 合并、页码标记、重叠去重；
- 错误码映射。

### 14.2 集成测试

- MinerU mock：
  - 正常提交、轮询、下载；
  - 429/5xx；
  - job 404 后重新提交；
  - 结果 zip 含图片和 JSON；
  - zip slip 和超大结果拒绝；
- LibreOffice DOCX 转 PDF；
- Excel 大文件读取；
- 解析产物写入 SeaweedFS；
- 文档状态和任务进度更新。

### 14.3 手工测试

- 300MB / 1000 页 PDF；
- 扫描件 PDF；
- 加密 PDF；
- 200MB Excel；
- 多工作表 Excel；
- 扫描版 PDF 分段合并后的页码连续性。

## 15. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-PARSE-01 | 超过 200 页或 200MiB 的 PDF 自动拆分并成功合并 | 集成测试、手工测试 |
| AC-PARSE-02 | MinerU 重启后，丢失的 job 能重新提交 | 故障注入测试 |
| AC-PARSE-03 | Excel/CSV 输出稳定 JSON，并能生成文本投影 | 单元测试、集成测试 |
| AC-PARSE-04 | 合并后的 Markdown 保留页码标记和图片链接 | 集成测试 |
| AC-PARSE-05 | 解析产物写入对象存储，文档状态更新为 `CHUNKING` | 集成测试 |
| AC-PARSE-06 | 解析失败有明确错误码，成功后临时文件被清理 | 单元测试、故障注入测试 |
| AC-PARSE-07 | 300MB PDF 从上传到解析完成 | 手工测试 |

## 16. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-PARSE-01 | MinerU 版本 API 差异 | 解析调用失败或结果格式不兼容 | 固定镜像版本，封装 `MinerUClient`，用真实服务做契约测试 |
| R-PARSE-02 | MinerU 状态不持久化 | 服务重启后 job 丢失 | 检测 404 后按分段重新提交，`parse_jobs` 保留分段信息 |
| R-PARSE-03 | PDF 跨页表格被拆断 | 表格语义不完整 | 默认按页拆分；必要时开启 1 页重叠并在合并时去重 |
| R-PARSE-04 | 扫描件单页过大 | 无法提交 MinerU | 单页超限时明确拒绝，提示压缩或拆分源文件 |
| R-PARSE-05 | Excel 超大导致 OOM | worker 崩溃 | `read_only` 流式读取，限制行数、列数和单元格数 |
| R-PARSE-06 | LibreOffice 转换慢 | 解析超时 | 独立超时和重试，必要时拆到单独队列 |
| R-PARSE-07 | 解压结果过大或路径非法 | 磁盘占满、zip slip | 限制结果大小、文件数量和路径，拒绝非法条目 |

## 17. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为解析模块实施的基线版本。
