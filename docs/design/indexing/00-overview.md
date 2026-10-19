# RAG 文档索引阶段总体设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-IDX-000 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 索引阶段总体架构、阶段契约、数据模型、状态模型、混合召回接口 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义 RAG 文档索引阶段的总体架构、阶段边界、数据契约、状态模型、部署约束和混合召回接口，作为上传、解析、向量化、Elasticsearch 索引四个子设计的上位文档，并作为研发、测试和运维团队的共同依据。

### 1.2 适用范围

本文档适用于以下工作：

- 文档索引链路的架构评审和接口评审；
- 各子模块的详细设计与开发拆分；
- 联调、测试、部署和故障排查；
- 后续检索阶段和问答阶段的接口对接。

本文档不替代各子模块的详细设计文档。接口字段、数据表结构和异常处理以子文档为准；子文档与本文档冲突时，以本文档确定的阶段契约和全局状态为准，并同步修订子文档。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| RAG | Retrieval-Augmented Generation，检索增强生成。 |
| Chunk | 文档切块后用于检索和 embedding 的最小文本单元。 |
| Embedding | 将文本转换为固定维度向量的过程。 |
| MinerU | 自建文档解析服务，负责 PDF/版式文档转 Markdown。 |
| SeaweedFS | 对象存储，保存原始文件、解析产物和临时分段文件。 |
| Milvus | 向量数据库，保存 chunk 向量并执行相似度检索。 |
| Elasticsearch / ES | 关键词检索引擎，保存 chunk 正文和元数据。 |
| BM25 | Elasticsearch 使用的关键词相关性评分算法。 |
| RRF | Reciprocal Rank Fusion，倒数排名融合。 |
| IK | Elasticsearch 中文分词插件。 |
| Multipart Upload | S3 分片上传协议，由对象存储完成最终合并。 |
| `chunk_id` | 同一 chunk 在 MySQL、ES、Milvus 中的统一标识。 |
| `parse_version` | 文档解析版本，重新解析时递增。 |

### 1.4 参考资料

- [大文件上传详细设计](./01-large-file-upload.md)
- [文档解析详细设计](./02-document-parsing.md)
- [向量化与 Milvus 存储详细设计](./03-vectorization-milvus.md)
- [Elasticsearch 索引详细设计](./04-elasticsearch-indexing.md)
- SeaweedFS S3 API 文档
- MinerU 自建服务 API 文档
- Milvus 官方文档
- Elasticsearch 8.x 官方文档及 IK 分词插件文档
- 阿里云 DashScope `text-embedding-v4` 接口文档

### 1.5 项目背景

当前系统已经具备：

- FastAPI 文档上传、文档列表、文档详情、删除接口；
- Celery + Redis 异步任务；
- MySQL 保存文档和任务；
- SeaweedFS 保存原始文件；
- 前端上传组件、上传队列和任务轮询。

当前链路的限制：

- `MAX_UPLOAD_BYTES` 为 20MB；
- 上传时把整个文件读入内存，再写临时文件；
- worker 处理时再次 `read_bytes()` 全量读取；
- 只完成对象存储和文档入库，没有解析、切块、向量化和关键词索引；
- 前端显示的“上传进度”实际是任务轮询进度，不是真实传输进度；
- 没有断点续传。

本设计把文档从上传到可检索的完整链路拆成四个可独立实现的阶段：

1. 文件上传（大文件处理）；
2. 解析层处理（MinerU + Excel/CSV 转 JSON）；
3. 向量化并写入 Milvus；
4. Elasticsearch 关键词索引。

四个阶段通过数据库状态和对象存储产物衔接。索引完成后，检索层可以同时使用 Milvus 向量召回和 ES 关键词召回，做 RRF 融合。

## 2. 目标与非目标

### 2.1 目标

- 支持单文件最大约 300MB；
- 分片上传、暂停、失败重试、刷新页面后重新选择同一文件继续上传；
- 上传完成后由异步任务完成解析、切块、embedding、双索引；
- PDF 通过自建 MinerU 解析为 Markdown/JSON；
- Excel/CSV 解析为 JSON，再投影为可检索文本；
- 同一份 chunk 同时写入 MySQL、Elasticsearch、Milvus；
- 任意一个索引失败可以重试，不重复写入；
- 支持向量 + 关键词混合召回，返回可引用的页码/工作表位置；
- 大文件处理不阻塞 API 进程，不把整个文件读入内存。

### 2.2 非目标

- 本阶段不实现用户认证、多租户和文档权限；
- 本阶段不实现最终答案生成和对话 UI；
- 本阶段不实现 rerank 模型，只预留接口；
- 本阶段不做 Elasticsearch/Milvus 集群高可用；
- 本阶段不支持 OCR 之外的图片理解、视频、音频；
- 本阶段不迁移历史数据，开发库直接重建。

### 2.3 需求与文档映射

| 需求编号 | 需求描述 | 主要设计文档 |
| --- | --- | --- |
| REQ-IDX-001 | 单文件约 300MB，分片上传并支持断点续传 | 01 大文件上传 |
| REQ-IDX-002 | 上传完成后异步解析 PDF/Word 为 Markdown | 02 文档解析 |
| REQ-IDX-003 | Excel/CSV 解析为结构化 JSON 并支持检索 | 02 文档解析 |
| REQ-IDX-004 | 解析结果按标题和 token 切块 | 03 向量化与 Milvus |
| REQ-IDX-005 | 使用阿里云 embedding 生成 1024 维向量 | 03 向量化与 Milvus |
| REQ-IDX-006 | 向量写入 Milvus，支持按文档/版本过滤 | 03 向量化与 Milvus |
| REQ-IDX-007 | chunk 写入 Elasticsearch，支持中文 BM25 | 04 Elasticsearch 索引 |
| REQ-IDX-008 | 向量与关键词两路召回，RRF 融合 | 00 总体设计、04 Elasticsearch 索引 |
| REQ-IDX-009 | 索引过程可重试、幂等、可观测 | 00 总体设计及各子文档 |
| REQ-IDX-010 | 删除文档时同步清理对象存储、MySQL、ES、Milvus | 00 总体设计及各子文档 |

## 3. 关键决策

| 主题 | 决策 | 理由 |
| --- | --- | --- |
| 上传传输 | SeaweedFS S3 Multipart Upload，8MiB 分片，3 并发 | 服务端自动合并成单对象；`ListParts` 天然支持续传；不占 API 内存和本地磁盘 |
| 上传状态 | MySQL `upload_sessions` + S3 `ListParts` | MySQL 提供持久会话；分片状态以 S3 为权威，不使用 Redis bitmap |
| 文件指纹 | `SHA-256(name + size + lastModified + 首尾各 1MiB)` | 刷新后重新选择文件可以匹配；避免读取 300MB 全量内容 |
| PDF 解析 | 自建 MinerU，按页/按大小拆分，异步提交和轮询 | 自建 MinerU 仍有单文件限制，且 job 状态不持久化 |
| Excel/CSV | 原生解析为 JSON，不经过 MinerU | MinerU 不负责结构化表格；原生解析更快、可控 |
| 切块 | 先合并/规范化，再按标题和 token 切块 | 避免 MinerU 分段边界产生半截语义块 |
| Embedding | 阿里云 `text-embedding-v4`，默认 1024 维，批量 10 | 官方限制单条 8192 token、每批最多 10 条 |
| 向量索引 | Milvus standalone，HNSW，COSINE | 本地部署、生态成熟，适合当前规模 |
| 关键词索引 | Elasticsearch 8.x 单节点 + IK 分词 | 中文 BM25 和关键词召回需要中文分词 |
| 混合召回 | 向量 top 50 + 关键词 top 50，RRF k=60，取 top 10 | 两路分数量纲不同，RRF 不需要分数归一化 |
| 索引一致性 | MySQL 为源数据，ES/Milvus 为派生索引 | 换 embedding 模型或重建索引不丢原文 |
| 任务隔离 | `parse` 和 `index` 两个 Celery 队列 | 大文件解析不能阻塞 embedding，GPU 解析和 API 调用限流互不影响 |

## 4. 总体架构

```text
┌──────────┐
│ 浏览器    │
│ 8MiB 分片 │
└────┬─────┘
     │ init / part / complete / abort
     ▼
┌──────────────────────┐        ┌──────────────────────────┐
│ FastAPI /api/uploads │───────▶│ SeaweedFS S3 Multipart   │
│ /api/documents       │        │ Complete -> 单个原始对象  │
│ /api/tasks           │        └──────────────────────────┘
└────┬─────────────────┘
     │ 写会话、文档、任务
     ▼
┌──────────────────────────────────────────────────────────┐
│ MySQL                                                     │
│ upload_sessions / documents / document_tasks / parse_jobs │
│ document_chunks                                           │
└────┬───────────────────────────────┬──────────────────────┘
     │ parse 队列                     │ index 队列
     ▼                               ▼
┌──────────────────────┐        ┌──────────────────────────┐
│ Celery parse worker  │        │ Celery index worker      │
│                      │        │                          │
│ 1. 下载原始文件       │        │ 1. 读取 merged.md/json    │
│ 2. 拆分 PDF/解析 Excel│        │ 2. 规范化、切 chunk      │
│ 3. 提交 MinerU        │        │ 3. 写 MySQL chunks       │
│ 4. 轮询、下载结果     │        │ 4. 写 ES                 │
│ 5. 合并 MD/JSON       │        │ 5. 阿里云 embedding      │
│ 6. 写回 SeaweedFS     │        │ 6. 写 Milvus             │
└──────────┬───────────┘        └──────────┬───────────────┘
           │                               │
           ▼                               ▼
┌──────────────────────┐        ┌──────────────────────────┐
│ MinerU GPU 机器       │        │ ES 8.x + IK              │
│ HTTP API，异步 job    │        │ Milvus standalone        │
└──────────────────────┘        │ etcd + MinIO             │
                                └──────────────────────────┘
```

## 5. 阶段与数据流

### 5.1 上传阶段

1. 前端计算文件指纹，调用 `POST /api/uploads/init`；
2. 后端创建 `upload_sessions` 和 S3 Multipart Upload；
3. 前端按 8MiB 切片，调用 `PUT /api/uploads/{id}/parts/{n}`；
4. 后端把分片写入 S3，返回 ETag；
5. 前端调用 `POST /api/uploads/{id}/complete`；
6. 后端完成 Multipart Upload，创建 `documents` 和 `document_tasks`，投递解析任务。

产物：

- SeaweedFS 单个原始文件对象；
- MySQL 文档记录；
- MySQL 文档处理任务，状态 `PENDING`。

### 5.2 解析阶段

1. parse worker 读取文档和源文件；
2. 按文件类型路由：
   - PDF：按页/按大小拆分，提交 MinerU；
   - DOC/DOCX：优先转 PDF 后拆分，或直接使用 MinerU 支持的格式；
   - XLS/XLSX：原生解析为 JSON；
   - CSV：原生解析为 JSON；
   - TXT/MD：原生读取和规范化；
3. 轮询 MinerU 任务，下载 zip，保存 MD/JSON/图片；
4. 按页码顺序合并，生成 `parsed/merged.md`、`parsed/merged.json`、`parsed/manifest.json`；
5. 更新文档状态为 `CHUNKING`，投递索引任务。

产物：

- SeaweedFS 解析结果对象；
- `parse_jobs` 分段记录；
- 文档 `page_count`、`parsed_md_key`、`parsed_json_key`。

### 5.3 索引阶段

1. 读取 `parsed/merged.md` / `merged.json`；
2. 规范化文本，按标题和 token 切 chunk；
3. 写入 MySQL `document_chunks`，生成确定性 `chunk_id`；
4. 批量写入 Elasticsearch；
5. 调用阿里云 embedding，批量写入 Milvus；
6. ES 和 Milvus 都完成后，文档状态置为 `READY`，写入 `chunk_count`。

产物：

- MySQL chunk 记录；
- ES 关键词索引；
- Milvus 向量索引；
- 可检索文档。

### 5.4 检索阶段

本阶段只实现检索服务接口，不实现最终答案生成：

1. 用户问题通过阿里云 embedding 转为查询向量；
2. 并行执行 Milvus top 50 和 ES BM25 top 50；
3. 按 `chunk_id` 去重；
4. RRF 融合，取 top 10；
5. 可选 rerank；
6. 返回 chunk、文档、页码/工作表位置和分数。

## 6. 存储职责

| 存储 | 职责 | 是否源数据 | 可重建 |
| --- | --- | --- | --- |
| MySQL | 文档元数据、上传会话、任务、chunk 正文 | 是 | 否 |
| SeaweedFS | 原始文件、解析 MD/JSON/图片、临时分段 PDF | 是 | 否 |
| Elasticsearch | chunk 关键词索引、高亮 | 否 | 是，从 MySQL 重建 |
| Milvus | chunk 向量索引 | 否 | 是，重新 embedding |
| Redis | Celery broker、锁、限流、MinerU 并发信号量 | 否 | 是 |

## 7. 数据模型

### 7.1 表关系

```text
documents 1 ─── N document_tasks
documents 1 ─── N parse_jobs
documents 1 ─── N document_chunks
upload_sessions 1 ─── 0..1 documents
document_tasks 1 ─── N parse_jobs
```

### 7.2 新增和调整的表

| 表 | 作用 | 详细设计 |
| --- | --- | --- |
| `upload_sessions` | 分片上传会话 | 上传设计文档 |
| `documents` | 文档主记录，新增页数、解析版本、解析产物 key | 总览 + 解析文档 |
| `document_tasks` | 文档级异步任务，保存阶段、进度、错误、检查点 | 总览 + 各阶段文档 |
| `parse_jobs` | MinerU 分段任务 | 解析设计文档 |
| `document_chunks` | chunk 正文和元数据，ES/Milvus 的源数据 | 向量化设计文档 |

现有 `processing_tasks` 由上传任务模型改为文档任务模型。由于项目仍处于开发阶段，直接替换为 `document_tasks`，不保留旧表数据。

### 7.3 关键 ID

- `upload_id`：上传会话 ID，32 位十六进制；
- `document_id`：文档 ID，32 位十六进制，在 init 阶段预生成；
- `task_id`：文档处理任务 ID，32 位十六进制；
- `chunk_id`：`sha256(document_id + ":" + version + ":" + chunk_index)[:32]`；
- `version`：解析版本，从 1 开始，每次重新解析递增。

`chunk_id` 同时作为 MySQL 主键、ES `_id`、Milvus 主键，保证双索引可去重、可幂等重试。

首次上传完成时 `parse_version = 1`；重新解析时递增。`document_tasks` 和 `parse_jobs` 都带 `version`，唯一键包含版本，避免新旧任务互相覆盖。

## 8. 状态与进度

### 8.1 上传会话状态

```text
INITIATED -> UPLOADING -> COMPLETED
                     └──> ABORTED
                     └──> EXPIRED
```

### 8.2 文档状态

```text
UPLOADED -> PARSING -> CHUNKING -> INDEXING -> READY
                                      └──────> FAILED
```

### 8.3 文档任务状态与阶段

任务 `status` 是粗粒度状态，`stage` 是细粒度阶段：

| status | stage | 进度范围 | 说明 |
| --- | --- | --- | --- |
| `PENDING` | `PENDING` | 0 | 等待解析 |
| `RUNNING` | `SPLITTING` | 0-5 | 检查文件、拆分 PDF/准备 Excel |
| `RUNNING` | `PARSING` | 5-70 | MinerU 分段解析或原生解析 |
| `RUNNING` | `MERGING` | 70-80 | 合并 Markdown/JSON/图片 |
| `RUNNING` | `CHUNKING` | 80-85 | 规范化、切块、写 MySQL |
| `RUNNING` | `INDEXING` | 85-100 | 写 ES、embedding、写 Milvus |
| `COMPLETED` | `DONE` | 100 | 完成 |
| `FAILED` | 当前阶段 | 当前进度 | 失败，保留错误信息 |

进度计算：

- 解析阶段：已完成页数 / 总页数，映射到 5-70；
- 合并阶段：固定 70-80；
- 切块阶段：已写入 chunk 数 / 总 chunk 数，映射到 80-85；
- 索引阶段：已索引 chunk 数 / 总 chunk 数，映射到 85-100。

### 8.4 MinerU 分段状态

```text
PENDING -> SUBMITTED -> RUNNING -> DOWNLOADING -> COMPLETED
                                      └──────────> FAILED
```

`parse_jobs.next_poll_at` 控制下一次轮询时间，避免所有任务同时打 MinerU。

## 9. 混合召回接口

### 9.1 召回流程

```text
query
  ├── 阿里云 embedding -> Milvus top 50
  └── ES multi_match -> top 50
          │
          ▼
      按 chunk_id 去重
          │
          ▼
      RRF: score = Σ 1 / (60 + rank_i)
          │
          ▼
      top 10（可选 rerank）
```

### 9.2 过滤条件

两路召回都支持：

- `document_ids`：限定文档；
- `version`：只取当前解析版本；
- `file_type`：限定文件类型；
- `page_start` / `page_end`：限定页码范围；
- 只检索 `documents.status = READY` 的文档。

### 9.3 响应结构

```json
{
  "query": "问题文本",
  "results": [
    {
      "chunk_id": "9f2c...",
      "document_id": "a1b2...",
      "document_name": "report.pdf",
      "file_type": "pdf",
      "content": "chunk 正文",
      "heading_path": "第三章 > 3.2 节",
      "locator": "page:12",
      "page_start": 12,
      "page_end": 12,
      "score": 0.032,
      "vector_rank": 3,
      "keyword_rank": 1
    }
  ],
  "degraded": false
}
```

`degraded=true` 表示 ES 或 Milvus 有一路不可用，结果只来自另一路。

### 9.4 检索服务接口

```python
class RetrievalService:
    def search(
        self,
        query: str,
        document_ids: list[str] | None = None,
        top_k: int = 10,
        candidates: int = 50,
        rerank: bool = False,
    ) -> RetrievalResult: ...
```

具体实现放在后续检索设计文档；本设计只固定数据结构和索引契约。

## 10. 一致性与幂等

### 10.1 写入顺序

```text
MySQL document_chunks
  -> Elasticsearch bulk
  -> 阿里云 embedding
  -> Milvus upsert
  -> documents.status = READY
```

- MySQL 是源数据，必须先写；
- ES 和 Milvus 是派生索引，允许重试；
- 两边都完成后才能把文档标记为 `READY`；
- 索引过程中文档状态为 `INDEXING`，检索层不返回该文档。

### 10.2 幂等策略

- 分片上传：同一 part number 重复上传直接覆盖；
- 完成上传：`upload_sessions.status = COMPLETED` 时直接返回已有文档和任务；
- 解析分段：`parse_jobs` 唯一键 `(document_id, part_index)`，重复提交前先查状态；
- chunk：确定性 `chunk_id`，MySQL 唯一键、ES `_id`、Milvus 主键都使用它；
- ES：`_bulk` 使用 `index` 操作，重复写入同一 `_id` 覆盖；
- Milvus：按 `chunk_id` upsert；
- 重新解析：递增 `version`，先删除旧版本的 ES 文档和 Milvus 向量，再写新版本。

### 10.3 检查点

`document_tasks.result_json` 保存索引检查点：

```json
{
  "page_count": 120,
  "parse_parts": 2,
  "chunk_count": 860,
  "es_indexed": true,
  "milvus_indexed_through": 640
}
```

任务重试时从检查点继续，避免重复调用阿里云 embedding。

## 11. 部署与配置

### 11.1 服务拓扑

| 服务 | 部署位置 | 说明 |
| --- | --- | --- |
| FastAPI | 后端机器 | API、上传会话、任务查询 |
| Celery parse worker | 后端机器 | 下载、拆分、MinerU 轮询、合并 |
| Celery index worker | 后端机器 | 切块、ES、embedding、Milvus |
| Celery beat | 后端机器 | MinerU 轮询、上传会话清理 |
| MySQL | 后端机器 | 元数据、任务、chunk |
| Redis | 后端机器 | broker、锁、限流 |
| SeaweedFS | 后端机器 | 原始文件、解析产物 |
| Elasticsearch | 后端机器 | 关键词索引 |
| Milvus + etcd + MinIO | 后端机器 | 向量索引 |
| MinerU | 独立 GPU 机器 | 文档解析，HTTP API |
| 阿里云 DashScope | 公网 | embedding API |

### 11.2 Celery 队列

| 队列 | 任务 | 并发建议 |
| --- | --- | --- |
| `parse` | `parse_document`、`poll_parse_jobs`、`merge_parse_results` | 2 |
| `index` | `chunk_and_index` | 2 |

启动示例：

```bash
celery -A main.celery_app worker -Q parse -c 2 --loglevel=info
celery -A main.celery_app worker -Q index -c 2 --loglevel=info
celery -A main.celery_app beat --loglevel=info
```

### 11.3 关键环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_DOCUMENT_BYTES` | `314572800` | 单文件上限，约 300MiB |
| `UPLOAD_CHUNK_BYTES` | `8388608` | 分片大小，8MiB |
| `UPLOAD_SESSION_TTL_HOURS` | `24` | 上传会话有效期 |
| `MINERU_API_URL` | 无 | 自建 MinerU 地址 |
| `MINERU_API_KEY` | 空 | MinerU API Key |
| `MINERU_CONCURRENCY` | `2` | 同时运行的 MinerU 分段任务数 |
| `MINERU_MAX_PAGES` | `180` | 单段最大页数 |
| `MINERU_MAX_BYTES` | `188743680` | 单段最大字节数，180MiB |
| `MINERU_POLL_INTERVAL_SECONDS` | `10` | 轮询间隔 |
| `MINERU_JOB_TIMEOUT_SECONDS` | `7200` | 单段解析超时 |
| `DASHSCOPE_API_KEY` | 无 | 阿里云 DashScope Key |
| `EMBEDDING_MODEL` | `text-embedding-v4` | embedding 模型 |
| `EMBEDDING_DIM` | `1024` | 向量维度 |
| `EMBEDDING_BATCH_SIZE` | `10` | 阿里云单批上限 |
| `MILVUS_URI` | `http://127.0.0.1:19530` | Milvus 地址 |
| `MILVUS_COLLECTION` | `document_chunks` | 向量集合 |
| `ES_URL` | `http://127.0.0.1:9200` | Elasticsearch 地址 |
| `ES_INDEX` | `document_chunks` | 关键词索引 |
| `ES_BULK_SIZE` | `500` | 批量写入条数 |

### 11.4 性能与容量

- 上传：单文件约 300MB，约 38 个 8MiB 分片，前端默认 3 并发；API 进程不加载完整文件。
- 解析：parse 队列默认 2 个 worker；MinerU 并发默认 2；单文档按 180 页/180MiB 拆分。
- 索引：index 队列默认 2 个 worker；MySQL 批量 500 条，ES bulk 500 条，Milvus 批量 200 条。
- 检索目标：BM25 top 50 目标 P95 < 200ms，向量 top 50 目标 P95 < 300ms，混合召回（不含 embedding）目标 P95 < 500ms。
- 容量估算：10 万 chunk 的原始文本约占 100-300MB；ES 索引按 1.5-2 倍膨胀估算；Milvus 1024 维向量原始数据约 400MB，另加 HNSW 索引开销。
- 以上为设计目标值，联调阶段根据实际文档、GPU 和网络情况校准。

### 11.5 安全与合规

- 当前系统没有用户认证，上传和文档接口不得直接暴露公网；生产前必须补齐认证、授权和上传会话归属。
- 上传文件名必须经过路径穿越和非法字符清洗；对象 key 由后端生成，不接受客户端传入。
- 分片大小、分片编号、文件总大小和文件类型必须严格校验；完成后可增加 magic bytes 校验。
- MinerU、Milvus、Elasticsearch 只监听内网；API Key 和数据库密码通过环境变量注入，不写入代码和日志。
- chunk 正文会发送到阿里云 DashScope；敏感文档场景切换自建 embedding 服务，并在产品文档中明确数据出境/出内网范围。
- 解析结果解压必须防止 zip slip、zip bomb 和超大文件；临时文件按保留策略清理。

## 12. 错误处理与清理

### 12.1 重试策略

| 阶段 | 可重试错误 | 最大次数 | 退避 |
| --- | --- | --- | --- |
| 上传分片 | 网络错误、5xx、429 | 3 | 1s、2s、4s |
| 完成上传 | S3 5xx、超时 | 3 | 2s、4s、8s |
| MinerU 提交/轮询 | 429、5xx、超时 | 3 | 5s、15s、30s |
| MinerU job 丢失 | 404、重启后状态丢失 | 2 次重新提交 | 立即 |
| ES bulk | 429、5xx、部分失败 | 3 | 2s、5s、10s |
| 阿里云 embedding | 429、5xx、超时 | 3 | 2s、4s、8s |
| Milvus 写入 | 超时、节点不可用 | 3 | 2s、5s、10s |

不可重试错误：文件类型不支持、文件损坏、加密 PDF、超出大小限制、embedding 维度配置错误、鉴权失败。

### 12.2 清理任务

- 每小时扫描 `upload_sessions.expires_at < now()`，调用 `AbortMultipartUpload`，标记 `EXPIRED`；
- 每天清理超过 7 天的 `parse/tmp/` 分段 PDF；
- 每天清理失败超过 7 天的临时解析产物；
- 删除文档时同步删除：
  - 原始对象；
  - 解析产物；
  - MySQL chunks；
  - ES 文档；
  - Milvus 向量；
  - 进行中的任务标记为取消。

### 12.3 日志与观测

所有日志带 `document_id`、`task_id`、`upload_id`、`parse_job_id`、`chunk_id`（适用时）。关键指标：

- 上传字节数、分片重试次数、完成耗时；
- 解析分段数、MinerU 耗时、失败率；
- chunk 数、embedding token 数、embedding 耗时；
- ES bulk 耗时、失败条数；
- Milvus 写入耗时；
- 检索两路耗时、降级次数。

## 13. 测试策略

### 13.1 单元测试

- 分片大小、总片数、最后一片大小计算；
- 文件指纹生成；
- PDF 拆分算法（页数、字节数、单页超限）；
- Excel/CSV 转 JSON；
- Markdown 切块；
- RRF 融合和去重；
- ES 查询构造；
- 各接口参数校验。

### 13.2 集成测试

- SeaweedFS Multipart Upload：Create/UploadPart/ListParts/Complete/Abort；
- MinerU mock：提交、轮询、下载、job 丢失、429；
- 阿里云 embedding mock：批量、超 Token、429；
- Elasticsearch：索引创建、bulk、关键词查询、高亮；
- Milvus：collection 创建、upsert、搜索、删除；
- 端到端：小 PDF、Excel、Markdown 从上传到 READY。

### 13.3 故障测试

- 上传中断后刷新页面，重新选择同一文件继续；
- 完成上传重复调用；
- MinerU 重启导致 job 丢失；
- embedding 中途失败，检查点续跑；
- ES 写入部分失败，重试不重复；
- Milvus 不可用时检索降级；
- 上传会话过期后的清理。

### 13.4 手工/压力测试

- 300MB PDF，1000 页；
- 300MB 扫描件 PDF，页数较少但单页很大；
- 200MB Excel，检查内存和超时；
- 3 个 300MB 文件并发上传；
- 10 万 chunk 的 ES/Milvus 检索延迟。

## 14. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-IDX-01 | 300MB 文件可以分片上传，刷新后重新选择同一文件可继续 | 集成测试、手工测试 |
| AC-IDX-02 | 上传完成后 SeaweedFS 只有一个原始对象，下载不需要合并 | 集成测试 |
| AC-IDX-03 | PDF 超过 MinerU 单文件限制时自动拆分并合并 | 集成测试、手工测试 |
| AC-IDX-04 | Excel/CSV 生成结构化 JSON，并可以切块检索 | 集成测试 |
| AC-IDX-05 | 同一份 chunk 在 MySQL、ES、Milvus 中的 `chunk_id` 一致 | 集成测试 |
| AC-IDX-06 | 向量召回和关键词召回可以并行执行，RRF 返回 top 10 | 集成测试 |
| AC-IDX-07 | ES 或 Milvus 单路故障时检索可以降级 | 故障注入测试 |
| AC-IDX-08 | 任意阶段失败都有明确错误、重试和清理策略 | 代码评审、故障注入测试 |
| AC-IDX-09 | 单元测试、集成测试和至少一次 300MB 手工测试通过 | 测试报告 |

## 15. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-IDX-01 | SeaweedFS S3 Multipart 兼容性 | 上传链路不可用 | 实现前用 boto3 对 Create/UploadPart/ListParts/Complete/Abort 做冒烟测试 |
| R-IDX-02 | MinerU 自建版本 API 差异 | 解析失败 | 固定镜像版本；封装 `MinerUClient`，用 mock 和真实服务各跑一遍 |
| R-IDX-03 | MinerU 状态不持久化 | job 丢失 | 检测 404 后按分段重新提交 |
| R-IDX-04 | 阿里云 embedding 限流/成本 | 索引慢或费用高 | 批量 10、并发 2、重试退避、记录 token 用量 |
| R-IDX-05 | 阿里云数据出内网 | 合规风险 | 敏感文档场景切换自建 embedding；接口层保持可替换 |
| R-IDX-06 | ES IK 插件版本不匹配 | 中文检索质量差或启动失败 | 固定 ES 版本和 IK 插件版本，镜像内安装 |
| R-IDX-07 | Milvus 资源占用 | 与 MySQL/ES 争内存 | 单机至少 16GB，Milvus 限额，必要时拆机 |
| R-IDX-08 | PDF 跨页表格被拆分 | 解析质量下降 | 分段边界按页，必要时启用 1 页重叠并在合并时去重 |
| R-IDX-09 | Excel 超大文件内存 | worker OOM | `read_only` 流式读取，限制行数/单元格数，超限拒绝 |
| R-IDX-10 | 当前没有鉴权 | 上传/文档接口可被匿名访问 | 生产前补认证；当前用不可猜测 ID，接口不暴露公网 |

## 16. 子设计文档

- [大文件上传详细设计](./01-large-file-upload.md)
- [文档解析详细设计](./02-document-parsing.md)
- [向量化与 Milvus 存储详细设计](./03-vectorization-milvus.md)
- [Elasticsearch 索引详细设计](./04-elasticsearch-indexing.md)

每个子文档包含该阶段的详细 API、数据表、错误处理、配置和测试。总览文档负责阶段之间的契约和全局状态。

## 17. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为索引阶段实施的基线版本。
