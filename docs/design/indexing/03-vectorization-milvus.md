# 向量化与 Milvus 存储详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-IDX-030 |
| 文档版本 | V0.9.1（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 索引阶段——父子分块、阿里云 embedding、Milvus 向量写入与检索 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |
| V0.9.1 | 2026-10-19 | 架构组 | 评审前修订：补充父子分块设计，子块用于 embedding 与召回，父块作为最终返回上下文；Milvus 只写入子块向量。 |

## 1. 概述

### 1.1 编写目的

本文档定义父子分块、子块文本切分、阿里云 embedding 调用、Milvus collection 设计、子块向量写入、版本清理和向量检索接口，作为向量化模块开发、测试和容量评估的依据。

### 1.2 适用范围

本文档适用于：

- 解析产物到父块、子块的转换；
- 阿里云 `text-embedding-v4` 的子块调用和限流处理；
- Milvus standalone 的 collection、索引和子块写入；
- 子块向量检索接口和过滤条件；
- 父块聚合、重新解析、重新索引和模型切换。

本文档不覆盖 Elasticsearch 索引写入和混合召回编排；后者见 Elasticsearch 索引详细设计。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| Parent Chunk / 父块 | 语义完整的大块，作为最终返回给上层模型的上下文单元。 |
| Child Chunk / 子块 | 父块切分后的小块，作为 embedding 和召回的单元。 |
| Parent-Child Chunking | 父子分块：向量化使用子块，最终检索结果使用父块。 |
| Chunk | 文档切块后的检索单元；未特别说明时指子块。 |
| Token | 模型分词后的最小单位，用于控制 chunk 和 embedding 长度。 |
| Embedding | 将文本转换为固定维度向量的过程。 |
| Collection | Milvus 中具有固定 schema 和索引的向量集合。 |
| HNSW | 一种近似最近邻向量索引结构。 |
| COSINE | 余弦相似度度量。 |
| Upsert | 按主键写入或覆盖向量记录。 |
| `chunk_id` | 子块在 MySQL、ES、Milvus 中的统一标识；父块使用 `parent_chunk_id`。 |

### 1.4 设计依据

- [RAG 文档索引阶段总体设计](./00-overview.md)
- [文档解析详细设计](./02-document-parsing.md)
- [Elasticsearch 索引详细设计](./04-elasticsearch-indexing.md)
- 阿里云 DashScope `text-embedding-v4` 接口文档
- Milvus 官方文档

### 1.5 设计目标

把解析后的 Markdown/JSON 转换为可检索的父子 chunk，并写入 Milvus：

- 采用父子分块：父块保留完整语义，子块用于 embedding 和召回；
- 父块目标 1200-2000 token，子块目标 300-500 token；
- 使用阿里云 `text-embedding-v4` 为子块生成向量；
- 子块写入 Milvus 和 Elasticsearch，父块写入 MySQL；
- 检索时先召回子块，再按 `parent_id` 聚合为父块，最终返回父块；
- 批量写入 Milvus，支持重试和断点续跑；
- 支持按文档、版本、文件类型过滤；
- 重新解析时清理旧父块、子块和向量，避免重复召回。

## 2. 非目标

- 不做 rerank 模型；
- 不做多模态向量（图片、表格截图）；
- 不做 HyDE、多查询扩展；
- 不做向量压缩、量化；
- 不做 Milvus 集群和跨机房容灾。

## 3. 输入与输出

### 3.1 输入

来自解析阶段：

```text
documents/{document_id}/parsed/merged.md
documents/{document_id}/parsed/merged.json
documents/{document_id}/parsed/manifest.json
```

### 3.2 输出

- MySQL `document_chunks`：父块和子块正文、层级关系及元数据；
- Milvus `document_chunks`：仅子块向量和过滤字段；
- Elasticsearch `document_chunks`：仅子块的关键词索引（见 ES 设计文档）；
- 文档 `parent_chunk_count`、`chunk_count`（子块数）和状态 `READY`。

## 4. 索引流程

```text
chunk_and_index(document_id, task_id)
  │
  ├── 读取 parsed/merged.md / merged.json
  ├── 文本规范化
  ├── 按标题和 token 生成父块
  ├── 将每个父块切分为子块
  ├── 删除旧版本父块/子块/向量/ES 文档
  ├── 批量写 MySQL document_chunks（先父块，后子块）
  ├── 批量写 Elasticsearch（仅子块）
  ├── 阿里云 embedding（仅子块）
  ├── 批量写 Milvus（仅子块）
  ├── 更新 documents.parent_chunk_count / chunk_count
  └── 文档状态 READY，任务 COMPLETED
```

索引任务在 `index` 队列执行，和 `parse` 队列隔离。

## 5. 文本规范化

在切块前做轻量规范化：

- 合并连续空行；
- 去除每页重复的页眉、页脚、页码（如果可识别）；
- 统一换行符；
- 保留 Markdown 标题层级；
- 保留表格、代码块、公式；
- 重写图片链接为对象存储地址；
- 不删除正文中的列表和引用。

页眉页脚识别规则：

- 同一行文本在超过 50% 的页面重复出现；
- 行长度小于 100 字符；
- 出现在页面首部或尾部；
- 命中时从正文移除，但保留在原始 `merged.md` 中，便于回溯。

规范化只影响索引文本，不修改解析产物原文。

## 6. 父子分块策略

### 6.1 分块模型

采用两级分块：

```text
父块 Parent Chunk（1200-2000 token，最终上下文）
  ├── 子块 Child Chunk（300-500 token，embedding / BM25 / 向量召回）
  ├── 子块
  └── 子块
```

设计约束：

- 父块是最终返回给上层模型的上下文单元，必须语义完整、可独立阅读；
- 子块是 embedding 和召回的单元，必须足够短，以保证向量语义集中；
- 每个子块有且只有一个父块，父块通过 `parent_id` 关联多个子块；
- 父块不生成向量，不写入 Milvus，不写入 Elasticsearch；
- 子块写入 MySQL、Elasticsearch 和 Milvus；
- 父块内容必须覆盖其所有子块内容，允许子块之间有重叠文本；
- 最终检索结果按父块返回，子块只作为命中依据和引用片段。

### 6.2 父块生成

1. 解析 Markdown 标题，维护标题栈；
2. 标题下一个完整章节作为父块候选；
3. 如果章节不超过 `PARENT_MAX_TOKENS`（默认 2000），直接作为一个父块；
4. 如果章节超过上限，按段落切分为 1200-2000 token 的多个父块；
5. 父块之间默认不重叠，避免最终结果重复；
6. 章节内容少于 `PARENT_MIN_TOKENS`（默认 200）时，优先与同一父标题下的相邻章节合并；无法合并时保留为独立父块；
7. 父块 `heading_path` 记录完整标题路径，例如：

   ```text
   第三章 财务分析 > 3.2 收入结构
   ```

8. 父块记录 `page_start`、`page_end`、`locator`，供最终引用；
9. 父块 `content` 保留规范化后的原始正文，不添加 embedding 前缀。

### 6.3 子块生成

1. 在每个父块内部按段落切分；
2. 单段超过 `CHILD_MAX_TOKENS`（默认 600）时按句子切分；
3. 仍超长时按字符窗口切分，保留 `CHILD_OVERLAP_TOKENS`（默认 80）重叠；
4. 子块目标长度 `CHILD_TARGET_TOKENS`（默认 400）；
5. 子块不得跨父块边界；
6. 过短子块（小于 `CHILD_MIN_TOKENS`，默认 80）优先与同父块相邻子块合并；
7. 父块本身不超过 600 token 时，仍然生成一个子块，保持父子结构统一；
8. 子块记录 `parent_id`、`parent_index`、`child_order` 和全局 `chunk_index`；
9. 子块 `content` 保存原始子块文本；生成 embedding 时使用：

   ```text
   embedding_text = heading_path + "\n" + child_content
   ```

10. 子块和父块都写入 MySQL；只有子块写入 ES/Milvus。

### 6.4 Token 限制

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `PARENT_MAX_TOKENS` | `2000` | 父块最大 token |
| `PARENT_TARGET_TOKENS` | `1600` | 父块目标 token |
| `PARENT_MIN_TOKENS` | `200` | 父块过短时尝试合并 |
| `CHILD_MAX_TOKENS` | `600` | 子块最大 token |
| `CHILD_TARGET_TOKENS` | `400` | 子块目标 token |
| `CHILD_OVERLAP_TOKENS` | `80` | 子块重叠 token |
| `CHILD_MIN_TOKENS` | `80` | 子块过短时合并 |

阿里云 `text-embedding-v4` 单条上限 8192 token，子块加标题前缀控制在 800 token 以内，留足余量。token 估算使用与 Qwen 系列兼容的 tokenizer；如果暂时没有 tokenizer，按“中文 1 字≈1 token、英文 4 字符≈1 token”保守估算，并在上线前用真实 tokenizer 校准。

### 6.5 切分顺序

1. 按标题生成父块；
2. 父块内按段落生成子块；
3. 子块超过上限时按句子切；
4. 仍超长时按字符窗口切，保留 overlap；
5. 合并过短的子块；
6. 不跨父块合并子块；
7. 不跨顶级标题合并父块。

### 6.6 表格

- 小于 `CHILD_MAX_TOKENS` 的表格整体作为一个子块；
- 大于 `CHILD_MAX_TOKENS` 的表格按行拆分，每个子块重复表头；
- 父块保留表格完整上下文，但父块超过 `PARENT_MAX_TOKENS` 时按行拆分为多个父块；
- 表格的 `heading_path` 使用表格前的最近标题；
- `locator` 记录表格所在页或工作表范围；
- 子块和父块都保留 Markdown 表格结构。

### 6.7 代码块和公式

- 代码块尽量整体保留；
- 代码块不超过 `CHILD_MAX_TOKENS` 时作为一个子块；
- 超长代码块按函数/空行拆分，父块保留完整代码上下文；
- 公式保留 LaTeX；
- 不把代码块和正文混在一个子块。

### 6.8 Excel/CSV

Excel/CSV 不直接对 JSON 做 embedding，使用解析阶段生成的文本投影：

```text
工作表：销售数据
列：日期、区域、金额
第 2-51 行：
日期=2026-01-01；区域=华东；金额=100
...
```

- 父块按 1200-2000 token 的行组生成；
- 子块按 300-500 token 的行组生成，每个子块重复表头；
- `heading_path = "销售数据"`；
- 父块 `locator = "sheet:销售数据!A1:F200"`；
- 子块 `locator = "sheet:销售数据!A2:C51"`；
- `page_start` / `page_end` 为空；
- 如果行内容很长，按列分组，仍保留表头。

### 6.9 图片

- 图片本身不做 embedding；
- Markdown 图片链接保留在父块和子块中；
- 如果 MinerU 提供图片说明/OCR 文本，作为正文的一部分；
- `content` 中保留 `![alt](url)`，供预览和引用；
- 后续如果接入多模态模型，再单独设计图片向量。

## 7. 父子 Chunk 数据模型

### 7.1 `document_chunks`

父块和子块存放在同一张表，通过 `chunk_level` 区分，避免两套内容表产生同步问题。

```sql
CREATE TABLE IF NOT EXISTS document_chunks (
    id VARCHAR(64) NOT NULL COMMENT 'chunk ID，确定性哈希',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    version INT NOT NULL COMMENT '解析版本',
    chunk_level VARCHAR(16) NOT NULL COMMENT 'PARENT/CHILD',
    parent_id VARCHAR(64) NULL COMMENT '子块所属父块 ID；父块为 NULL',
    parent_index INT NULL COMMENT '子块所属父块序号；父块为 NULL',
    child_order INT NULL COMMENT '子块在父块内的序号；父块为 NULL',
    chunk_index INT NOT NULL COMMENT '同层级序号：父块为父块序号，子块为全局子块序号',
    child_count INT NOT NULL DEFAULT 0 COMMENT '父块包含的子块数；子块为 0',
    file_type VARCHAR(32) NOT NULL COMMENT '文件类型',
    page_start INT NULL COMMENT '起始页，Excel 为空',
    page_end INT NULL COMMENT '结束页，Excel 为空',
    locator VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'page:12 或 sheet:名称!A1:C20',
    heading_path VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '标题路径',
    content MEDIUMTEXT NOT NULL COMMENT 'chunk 正文',
    token_count INT NOT NULL COMMENT '估算 token 数',
    embedding_model VARCHAR(128) NULL COMMENT '子块向量模型；父块为 NULL',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_document_chunks_level_index (document_id, version, chunk_level, chunk_index),
    KEY idx_document_chunks_document_version (document_id, version),
    KEY idx_document_chunks_parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 7.2 字段语义

| 字段 | 父块 | 子块 |
| --- | --- | --- |
| `chunk_level` | `PARENT` | `CHILD` |
| `parent_id` | `NULL` | 父块 `id` |
| `parent_index` | `NULL` | 父块序号 |
| `child_order` | `NULL` | 子块在父块内的序号，从 0 开始 |
| `chunk_index` | 父块序号，从 0 开始 | 文档内全局子块序号，从 0 开始 |
| `child_count` | 子块数量 | `0` |
| `embedding_model` | `NULL` | 生成向量的模型 |
| `content` | 完整父块正文 | 子块正文 |
| `token_count` | 父块 token 数 | 子块 token 数 |

### 7.3 Chunk ID

```text
parent_chunk_id = sha256(document_id + ":" + version + ":PARENT:" + parent_index)[:32]
child_chunk_id  = sha256(document_id + ":" + version + ":CHILD:" + chunk_index)[:32]
```

- 32 位十六进制，128 bit；
- MySQL 主键、ES `_id`、Milvus 主键共用子块 ID；
- 父块 ID 只用于 MySQL 和最终结果聚合，不写入 ES/Milvus；
- 重新解析时版本变化，父子 chunk ID 都变化，不会覆盖旧版本；
- 同一版本重试时 chunk ID 不变，天然幂等。

### 7.4 元数据

| 字段 | 用途 |
| --- | --- |
| `document_id` | 过滤、引用 |
| `version` | 只检索当前版本 |
| `chunk_level` | 区分父块和子块 |
| `parent_id` | 子块聚合到父块 |
| `parent_index` | 父块顺序、上下文扩展 |
| `child_order` | 子块在父块内顺序 |
| `chunk_index` | 同层级顺序 |
| `file_type` | 过滤 |
| `page_start` / `page_end` | 页码引用 |
| `locator` | PDF 页码或 Excel 单元格范围 |
| `heading_path` | 章节引用、embedding 前缀 |
| `token_count` | 成本、切块质量 |

### 7.5 数据完整性约束

- 每个 `CHILD` 必须有 `parent_id`，且父块必须是同一 `document_id`、同一 `version` 的 `PARENT`；
- 每个 `PARENT` 的 `child_count` 必须等于实际子块数量；
- 子块不得跨父块边界；
- 父块 `content` 必须覆盖其所有子块内容；
- 不允许子块引用其他文档的父块；
- 索引完成后执行一次一致性校验：`PARENT` 数量、`CHILD` 数量、孤立子块数量必须为 0。

## 8. 阿里云 Embedding

### 8.1 模型

默认：

```text
model = text-embedding-v4
dimension = 1024
batch_size = 10
```

选择理由：

- 阿里云官方同步接口每批最多 10 条；
- 单条文本最多 8192 token；
- 1024 维在检索质量、Milvus 存储和查询延迟之间比较平衡；
- 支持中英文和多语言。

本设计只对 `CHILD` 子块生成向量，`PARENT` 父块不调用 embedding。调用输入为子块的 `embedding_text`：

```text
embedding_text = heading_path + "\n" + child_content
```

`embedding_text` 只用于生成向量，不写入 `document_chunks.content`；这样既保留标题语义，又避免修改原始正文。

如果后续追求更高质量，可以改为 1536 或 2048 维，但必须：

- 新建 Milvus collection；
- 全量重新 embedding；
- 更新 `EMBEDDING_DIM` 和 collection 名称；
- 不要直接改维度复用旧 collection。

### 8.2 调用方式

使用 DashScope SDK 或 OpenAI 兼容接口：

```python
# 伪代码
response = dashscope.TextEmbedding.call(
    model="text-embedding-v4",
    input=texts,          # 最多 10 条
    dimension=1024,
    api_key=settings.dashscope_api_key,
)
vectors = [item["embedding"] for item in response.output["embeddings"]]
```

要求：

- 批量大小不超过 10；
- 并发 2-3；
- 连接超时 10 秒，读取超时 60 秒；
- 429/5xx 指数退避重试 3 次；
- 401/403 立即失败，提示配置错误；
- 400 且提示 token 超限时，拆分该 chunk 后重试；
- 记录每次请求的 token 用量和耗时。

### 8.3 查询向量

检索时对用户问题单独调用一次 embedding：

- 批量大小为 1；
- 使用相同模型和维度；
- 结果缓存可选，按问题哈希缓存 5 分钟；
- 查询向量不写入 MySQL。

### 8.4 数据合规

子块正文会发送到阿里云 DashScope。需要：

- 在文档中明确告知；
- 敏感文档场景切换自建 embedding 服务；
- `EmbeddingProvider` 接口保持可替换：

  ```python
  class EmbeddingProvider:
      def embed(self, texts: list[str]) -> list[list[float]]: ...
  ```

## 9. Milvus 设计

### 9.1 部署

- Milvus standalone；
- etcd 保存元数据；
- MinIO 保存 segment 和索引文件；
- 与后端同机，默认端口 `19530`；
- 不对公网暴露；
- 数据卷持久化。

不建议复用 SeaweedFS 作为 Milvus 的对象存储：

- Milvus 对 S3 兼容性有特定要求；
- 与业务对象存储混用会增加排障成本；
- 单独 MinIO 更可控。

### 9.2 Collection

```text
collection: document_chunks
description: 文档子块向量（父子分块，仅子块）
```

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chunk_id` | VARCHAR(64) | 子块主键 |
| `parent_id` | VARCHAR(64) | 父块 ID，检索后聚合 |
| `parent_index` | INT | 父块序号 |
| `child_order` | INT | 子块在父块内序号 |
| `document_id` | VARCHAR(32) | 过滤 |
| `version` | INT | 版本过滤 |
| `chunk_index` | INT | 全局子块序号 |
| `file_type` | VARCHAR(32) | 文件类型过滤 |
| `page_start` | INT | 页码 |
| `page_end` | INT | 页码 |
| `heading_path` | VARCHAR(1024) | 标题路径 |
| `locator` | VARCHAR(512) | 页码/单元格位置 |
| `vector` | FLOAT_VECTOR(1024) | 子块向量 |

Milvus 只保存子块向量：

- 父块正文在 MySQL，最终检索时按 `parent_id` 回查；
- 子块正文在 MySQL 和 ES，用于引用和高亮；
- Milvus 不保存 `content`，减少存储和写入开销；
- 向量检索命中子块后，由检索层聚合到父块。

### 9.3 索引

```text
index_type = HNSW
metric_type = COSINE
params = {"M": 16, "efConstruction": 200}
```

检索参数：

```text
params = {"ef": 64}
limit = 50
```

说明：

- 1024 维、十万级 chunk 使用 HNSW 足够；
- `ef` 越大召回越高、延迟越高，先 64，后续根据评估调整；
- COSINE 对文本 embedding 更常用；
- 向量写入前做 L2 归一化可选，COSINE 本身会归一化。

### 9.4 集合创建

启动时或首次索引时检查 collection 是否存在：

1. 不存在：创建 schema、索引、load；
2. 存在：校验维度与 `EMBEDDING_DIM` 一致；
3. 维度不一致：拒绝启动索引任务，提示新建 collection；
4. collection 名称可通过 `MILVUS_COLLECTION` 配置。

### 9.5 写入

- 仅写入 `CHILD` 子块，父块不写入 Milvus；
- 按子块 `chunk_id` upsert；
- 每批携带 `parent_id`、`parent_index`、`child_order`；
- 批次大小 100-500；
- 每批写入后记录检查点；
- 全部写完后 `flush()`；
- 一致性级别使用 `Bounded`；
- 如果需要“写入后立刻可查”，在测试环境使用 `Strong`。

### 9.6 删除与重建

重新解析时：

1. 递增 `documents.parse_version`；
2. 删除 MySQL 旧版本父块和子块；
3. 删除 ES 旧版本子块文档；
4. 删除 Milvus 中 `document_id` 对应的旧子块向量；
5. 写入新版本父块、子块和子块向量；
6. 全部完成后文档 `READY`。

删除表达式：

```text
document_id == "{document_id}"
```

如果未来需要保留多版本，可改为同时过滤 `version`，但本阶段只保留当前版本。

## 10. 索引编排与检查点

### 10.1 写入顺序

```text
0. documents.status = INDEXING
1. 删除旧版本父块、子块、ES 文档和 Milvus 向量
2. MySQL 批量插入父块，再插入子块
3. ES 批量写入子块
4. 阿里云 embedding（仅子块）
5. Milvus 批量 upsert（仅子块）
6. flush Milvus
7. 更新 documents.parent_chunk_count / chunk_count / status = READY
8. 任务 COMPLETED
```

MySQL 必须先写，因为它是源数据；ES 和 Milvus 可以重试。父块必须先于子块写入，保证子块 `parent_id` 可追溯。

### 10.2 检查点

`document_tasks.result_json`：

```json
{
  "parent_count": 120,
  "chunk_count": 860,
  "es_indexed": true,
  "milvus_indexed_through": 640,
  "embedding_tokens": 215000
}
```

- `parent_count` 是父块数量，`chunk_count` 是子块数量；
- ES 写入成功后置 `es_indexed=true`；
- Milvus 每批成功后更新 `milvus_indexed_through`；
- 重试时跳过已完成的步骤；
- 如果子块内容变化，子块 chunk_id 变化，检查点失效，重新开始。

### 10.3 进度

| 阶段 | 进度 |
| --- | --- |
| 读取 + 规范化 | 80-81 |
| 父块生成 | 81-83 |
| 子块生成 + MySQL | 83-86 |
| ES bulk（子块） | 86-90 |
| embedding + Milvus（子块） | 90-99 |
| flush + READY | 100 |

进度按已处理子块数 / 总子块数计算；父块生成阶段按章节数估算。

## 11. 向量检索接口

> 本节定义索引阶段的向量检索契约和 Milvus 调用参数；检索阶段的编排、父块聚合和降级见[检索阶段设计文档集](../retrieval/README.md)。

```python
class VectorSearchResult:
    chunk_id: str          # 子块 ID
    parent_id: str         # 父块 ID
    parent_index: int
    child_order: int
    document_id: str
    version: int
    chunk_index: int       # 全局子块序号
    score: float
    page_start: int | None
    page_end: int | None
    heading_path: str
    locator: str


class VectorStore:
    def search(
        self,
        vector: list[float],
        top_k: int = 50,
        document_ids: list[str] | None = None,
        version: int | None = None,
        file_type: str | None = None,
    ) -> list[VectorSearchResult]: ...
```

过滤表达式示例：

```text
document_id in ["a1b2", "c3d4"] and version == 1 and file_type == "pdf"
```

注意：

- 字符串需要转义；
- `document_ids` 为空列表时不要拼 `in []`，直接不加过滤；
- 只检索 `READY` 文档，由上层先取允许的文档 ID 列表，或在索引写入时保证只有 READY 文档存在；
- Milvus 只返回子块，向量检索层不直接返回 `content`；
- 检索编排层按 `parent_id` 聚合父块，并从 MySQL 批量读取父块正文。

## 12. 重新索引

触发方式：

- 文档重新解析；
- 更换 embedding 模型；
- 切块参数调整；
- 索引损坏修复。

流程：

1. 生成新的 `parse_version`；
2. 重新解析或复用已有 `merged.md`；
3. 删除旧父块、子块、ES 文档、Milvus 向量；
4. 重新生成父块和子块，对子块 embedding，写入 MySQL、ES、Milvus；
5. 文档 `READY`。

更换 embedding 模型时：

- 维度变化：新建 collection，例如 `document_chunks_v2`；
- 维度相同但模型变化：可以复用 collection，但必须全量重写；
- 检索层通过配置切换 collection/alias；
- 不要混用不同模型的向量。

## 13. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 无 | 阿里云 API Key |
| `DASHSCOPE_REGION` | `cn-beijing` | 区域 |
| `EMBEDDING_MODEL` | `text-embedding-v4` | 模型 |
| `EMBEDDING_DIM` | `1024` | 维度 |
| `EMBEDDING_BATCH_SIZE` | `10` | 批量 |
| `EMBEDDING_CONCURRENCY` | `2` | 并发 |
| `EMBEDDING_TIMEOUT_SECONDS` | `60` | 超时 |
| `PARENT_MAX_TOKENS` | `2000` | 父块最大 token |
| `PARENT_TARGET_TOKENS` | `1600` | 父块目标 token |
| `PARENT_MIN_TOKENS` | `200` | 父块最小 token |
| `CHILD_MAX_TOKENS` | `600` | 子块最大 token |
| `CHILD_TARGET_TOKENS` | `400` | 子块目标 token |
| `CHILD_OVERLAP_TOKENS` | `80` | 子块重叠 token |
| `CHILD_MIN_TOKENS` | `80` | 子块最小 token |
| `MILVUS_URI` | `http://127.0.0.1:19530` | Milvus 地址 |
| `MILVUS_COLLECTION` | `document_chunks` | collection |
| `MILVUS_INDEX_TYPE` | `HNSW` | 索引类型 |
| `MILVUS_METRIC_TYPE` | `COSINE` | 距离度量 |
| `MILVUS_BATCH_SIZE` | `200` | 写入批次 |

## 14. 错误处理

| 错误 | code | 可重试 | 处理 |
| --- | --- | --- | --- |
| 阿里云鉴权失败 | `EMBEDDING_AUTH_FAILED` | 否 | 检查 API Key |
| 阿里云限流 | `EMBEDDING_RATE_LIMITED` | 是 | 退避 |
| 单条超 token | `EMBEDDING_INPUT_TOO_LONG` | 是 | 拆分子块后重试 |
| 阿里云超时 | `EMBEDDING_TIMEOUT` | 是 | 重试 |
| Milvus 不可用 | `MILVUS_UNAVAILABLE` | 是 | 重试 |
| Milvus 维度不匹配 | `MILVUS_DIMENSION_MISMATCH` | 否 | 新建 collection |
| MySQL 写入失败 | `CHUNK_STORE_FAILED` | 是 | 重试 |
| 解析产物缺失 | `PARSED_ARTIFACT_MISSING` | 否 | 回到解析阶段 |
| 父块为空 | `PARENT_CHUNK_EMPTY` | 否 | 文档 FAILED，提示无文本 |
| 子块为空 | `CHILD_CHUNK_EMPTY` | 否 | 文档 FAILED，提示无文本 |
| 子块无父块 | `ORPHAN_CHILD_CHUNK` | 否 | 索引任务 FAILED，按数据完整性问题排查 |
| 父块超过上限 | `PARENT_CHUNK_TOO_LARGE` | 否 | 索引任务 FAILED，检查切块配置 |

失败时：

- 保留已写入的 MySQL 父块和子块，便于排查；
- 删除或覆盖 ES/Milvus 的部分写入，重试时幂等；
- 任务 `FAILED`，记录 `milvus_indexed_through` 检查点；
- 文档 `FAILED`，不参与检索。

## 15. 测试

### 15.1 单元测试

- Markdown 标题解析和父块生成；
- 子块切分、overlap、短块合并；
- 子块不跨父块边界；
- 父块 `content` 覆盖全部子块内容；
- 表格拆分和表头重复；
- Excel 文本投影父子分块；
- `chunk_id` 确定性，父块和子块 ID 不冲突；
- 父块级 RRF 聚合和去重；
- 过滤表达式构造和转义。

### 15.2 集成测试

- 阿里云 embedding mock：正常、429、超 token、鉴权失败；
- 只对子块调用 embedding，父块不调用；
- Milvus 测试实例：collection 创建、upsert、搜索、删除；
- MySQL 父子 chunk 写入、层级唯一键和 `parent_id` 完整性；
- ES 只写入子块，父块不在 ES；
- 检查点续跑：ES 成功后 Milvus 失败，重试不重复写 ES；
- 重新索引删除旧父块、子块和向量；
- 维度变化时拒绝复用 collection。

### 15.3 检索测试

- 构造已知向量，验证子块 top-k 和过滤；
- 多个子块命中同一父块时只聚合为一个父块结果；
- 父块结果包含 `parent_id`、`matched_children` 和父块正文；
- 文档 ID、版本、文件类型过滤；
- 空结果；
- Milvus 不可用时上层降级。

## 16. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-VEC-01 | 300MB PDF 解析出的 Markdown 可以生成父块和子块，并写入 MySQL | 集成测试、手工测试 |
| AC-VEC-02 | 子块 `chunk_id` 在 MySQL、ES、Milvus 中一致 | 集成测试 |
| AC-VEC-03 | 只对子块调用 embedding，批量 10，失败可重试 | 单元测试、故障注入测试 |
| AC-VEC-04 | Milvus 向量检索按文档和版本过滤正确，并返回 `parent_id` | 集成测试 |
| AC-VEC-05 | 重新索引不产生重复父块、子块和向量 | 集成测试 |
| AC-VEC-06 | 索引失败可从中断的 batch 继续 | 故障注入测试 |
| AC-VEC-07 | 向量召回接口返回子块 `chunk_id`、`parent_id`、分数、页码和标题路径 | 接口测试 |
| AC-VEC-08 | 检索编排返回父块正文，并附带命中子块片段 | 集成测试 |
| AC-VEC-09 | 10 万子块的向量检索延迟在可接受范围（目标 < 300ms，不含 embedding） | 性能测试 |

## 17. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-VEC-01 | 阿里云 embedding 限流 | 索引变慢或失败 | 批量 10、并发 2、指数退避，记录 429 |
| R-VEC-02 | 阿里云 embedding 成本 | 索引成本超预算 | 控制 chunk 数、避免重复 embedding、记录 token 用量 |
| R-VEC-03 | 数据出内网 | 合规风险 | 敏感场景切换自建 embedding，接口层保持可替换 |
| R-VEC-04 | embedding 维度变更 | 旧 collection 不可用 | 新建 collection，全量重建，不混用不同模型向量 |
| R-VEC-05 | Milvus 内存占用 | 与 MySQL/ES 争内存 | 单机限额，至少 16GB 总内存，必要时拆机 |
| R-VEC-06 | Milvus 写入后不可查 | 索引完成但检索无结果 | 写入后 `flush()`，检索使用 `Bounded`，测试使用 `Strong` |
| R-VEC-07 | 切块破坏语义 | 召回质量下降 | 父子分块，父块保留完整语义，子块加标题前缀，表格整体保留，用真实文档评估 |
| R-VEC-08 | 父块过大导致上下文超限 | 上层模型无法放入完整父块 | `PARENT_MAX_TOKENS` 限制为 2000，超长章节拆分为多个父块 |
| R-VEC-09 | 父子关系不一致 | 子块无法聚合为父块 | 写入前校验每个子块有父块，索引完成后检查孤立子块数量为 0 |

## 18. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为向量化模块实施的基线版本。
