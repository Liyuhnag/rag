# 向量化与 Milvus 存储详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-IDX-030 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 索引阶段——文本切块、阿里云 embedding、Milvus 向量写入与检索 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义文本切块、阿里云 embedding 调用、Milvus collection 设计、向量写入、版本清理和向量检索接口，作为向量化模块开发、测试和容量评估的依据。

### 1.2 适用范围

本文档适用于：

- 解析产物到 chunk 的转换；
- 阿里云 `text-embedding-v4` 的调用和限流处理；
- Milvus standalone 的 collection、索引和写入；
- 向量检索接口和过滤条件；
- 重新解析、重新索引和模型切换。

本文档不覆盖 Elasticsearch 索引写入和混合召回编排；后者见 Elasticsearch 索引详细设计。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| Chunk | 文档切块后的最小检索单元。 |
| Token | 模型分词后的最小单位，用于控制 chunk 和 embedding 长度。 |
| Embedding | 将文本转换为固定维度向量的过程。 |
| Collection | Milvus 中具有固定 schema 和索引的向量集合。 |
| HNSW | 一种近似最近邻向量索引结构。 |
| COSINE | 余弦相似度度量。 |
| Upsert | 按主键写入或覆盖向量记录。 |
| `chunk_id` | chunk 在 MySQL、ES、Milvus 中的统一标识。 |

### 1.4 设计依据

- [RAG 文档索引阶段总体设计](./00-overview.md)
- [文档解析详细设计](./02-document-parsing.md)
- [Elasticsearch 索引详细设计](./04-elasticsearch-indexing.md)
- 阿里云 DashScope `text-embedding-v4` 接口文档
- Milvus 官方文档

### 1.5 设计目标

把解析后的 Markdown/JSON 转换为可检索的 chunk，并写入 Milvus：

- 按标题和 token 切块，保留页码、工作表位置和标题路径；
- 使用阿里云 `text-embedding-v4` 生成向量；
- 批量写入 Milvus，支持重试和断点续跑；
- 同一份 chunk 同时供 Elasticsearch 和 Milvus 使用；
- 支持按文档、版本、文件类型过滤；
- 重新解析时清理旧向量，避免重复召回。

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

- MySQL `document_chunks`：chunk 正文和元数据；
- Milvus `document_chunks`：向量和过滤字段；
- Elasticsearch `document_chunks`：关键词索引（见 ES 设计文档）；
- 文档 `chunk_count`、状态 `READY`。

## 4. 索引流程

```text
chunk_and_index(document_id, task_id)
  │
  ├── 读取 parsed/merged.md / merged.json
  ├── 文本规范化
  ├── 按标题和 token 切 chunk
  ├── 删除旧版本 chunks/向量/ES 文档
  ├── 批量写 MySQL document_chunks
  ├── 批量写 Elasticsearch
  ├── 阿里云 embedding
  ├── 批量写 Milvus
  ├── 更新 documents.chunk_count
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

## 6. 切块策略

### 6.1 标题感知

1. 解析 Markdown 标题，维护标题栈；
2. 遇到新标题时结束当前 chunk；
3. `heading_path` 记录完整路径，例如：

   ```text
   第三章 财务分析 > 3.2 收入结构
   ```

4. 如果标题层级跳跃，按实际层级记录，不强制补全。

### 6.2 Token 限制

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `CHUNK_MAX_TOKENS` | `800` | 单 chunk 最大 token |
| `CHUNK_TARGET_TOKENS` | `500` | 目标长度 |
| `CHUNK_OVERLAP_TOKENS` | `80` | 相邻 chunk 重叠 |
| `CHUNK_MIN_TOKENS` | `80` | 过短 chunk 合并到相邻块 |

阿里云 `text-embedding-v4` 单条上限 8192 token，800 token 留足余量。token 估算使用与 Qwen 系列兼容的 tokenizer；如果暂时没有 tokenizer，按“中文 1 字≈1 token、英文 4 字符≈1 token”保守估算，并在上线前用真实 tokenizer 校准。

### 6.3 切分顺序

1. 按标题切；
2. 标题内按段落切；
3. 单段超过 `CHUNK_MAX_TOKENS` 时按句子切；
4. 仍超长时按字符窗口切，保留 overlap；
5. 合并小于 `CHUNK_MIN_TOKENS` 的相邻块；
6. 不跨标题合并，除非标题下内容过短且父级相同。

### 6.4 表格

- 小于 `CHUNK_MAX_TOKENS` 的表格整体保留；
- 大表格按行拆分，每个 chunk 重复表头；
- 表格 chunk 的 `content` 保留 Markdown 表格；
- `heading_path` 加上表格前的最近标题；
- `locator` 记录表格所在页或工作表范围。

### 6.5 代码块和公式

- 代码块尽量整体保留；
- 公式保留 LaTeX；
- 超长代码块按函数/空行拆分；
- 不把代码块和正文混在一个 chunk。

### 6.6 Excel/CSV

Excel/CSV 不直接对 JSON 做 embedding，使用解析阶段生成的文本投影：

```text
工作表：销售数据
列：日期、区域、金额
第 2-51 行：
日期=2026-01-01；区域=华东；金额=100
...
```

- 每个 chunk 重复表头；
- 每 20-50 行或达到 token 上限切一块；
- `heading_path = "销售数据"`；
- `locator = "sheet:销售数据!A2:C51"`；
- `page_start` / `page_end` 为空；
- 如果行内容很长，按列分组，仍保留表头。

### 6.7 图片

- 图片本身不做 embedding；
- Markdown 图片链接保留；
- 如果 MinerU 提供图片说明/OCR 文本，作为正文的一部分；
- `content` 中保留 `![alt](url)`，供预览和引用；
- 后续如果接入多模态模型，再单独设计图片向量。

## 7. Chunk 数据模型

### 7.1 `document_chunks`

```sql
CREATE TABLE IF NOT EXISTS document_chunks (
    id VARCHAR(64) NOT NULL COMMENT 'chunk ID，确定性哈希',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    version INT NOT NULL COMMENT '解析版本',
    chunk_index INT NOT NULL COMMENT '文档内序号，从 0 开始',
    file_type VARCHAR(32) NOT NULL COMMENT '文件类型',
    page_start INT NULL COMMENT '起始页，Excel 为空',
    page_end INT NULL COMMENT '结束页，Excel 为空',
    locator VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'page:12 或 sheet:名称!A1:C20',
    heading_path VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '标题路径',
    content MEDIUMTEXT NOT NULL COMMENT 'chunk 正文',
    token_count INT NOT NULL COMMENT '估算 token 数',
    embedding_model VARCHAR(128) NOT NULL COMMENT '生成向量的模型',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_document_chunks_version_index (document_id, version, chunk_index),
    KEY idx_document_chunks_document_version (document_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 7.2 Chunk ID

```text
chunk_id = sha256(document_id + ":" + version + ":" + chunk_index)[:32]
```

- 32 位十六进制，128 bit；
- MySQL 主键、ES `_id`、Milvus 主键共用；
- 重新解析时版本变化，chunk_id 变化，不会覆盖旧版本；
- 同一版本重试时 chunk_id 不变，天然幂等。

### 7.3 元数据

| 字段 | 用途 |
| --- | --- |
| `document_id` | 过滤、引用 |
| `version` | 只检索当前版本 |
| `chunk_index` | 顺序、上下文扩展 |
| `file_type` | 过滤 |
| `page_start` / `page_end` | 页码引用 |
| `locator` | PDF 页码或 Excel 单元格范围 |
| `heading_path` | 章节引用、embedding 前缀 |
| `token_count` | 成本、切块质量 |

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

chunk 正文会发送到阿里云 DashScope。需要：

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
description: 文档 chunk 向量
```

字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chunk_id` | VARCHAR(64) | 主键 |
| `document_id` | VARCHAR(32) | 过滤 |
| `version` | INT | 版本过滤 |
| `chunk_index` | INT | 顺序 |
| `file_type` | VARCHAR(32) | 文件类型过滤 |
| `page_start` | INT | 页码 |
| `page_end` | INT | 页码 |
| `heading_path` | VARCHAR(1024) | 标题路径 |
| `locator` | VARCHAR(512) | 页码/单元格位置 |
| `vector` | FLOAT_VECTOR(1024) | 向量 |

不把 `content` 存进 Milvus：

- 正文在 MySQL 和 ES；
- Milvus 只负责向量检索；
- 检索命中后按 `chunk_id` 回查 MySQL 或 ES。

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

- 按 `chunk_id` upsert；
- 批次大小 100-500；
- 每批写入后记录检查点；
- 全部写完后 `flush()`；
- 一致性级别使用 `Bounded`；
- 如果需要“写入后立刻可查”，在测试环境使用 `Strong`。

### 9.6 删除与重建

重新解析时：

1. 递增 `documents.parse_version`；
2. 删除 MySQL 旧版本 chunks；
3. 删除 ES 旧版本文档；
4. 删除 Milvus 中 `document_id` 对应的旧向量；
5. 写入新版本；
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
1. 删除旧版本
2. MySQL 批量插入 chunks
3. ES 批量写入
4. 阿里云 embedding
5. Milvus 批量 upsert
6. flush Milvus
7. 更新 documents.chunk_count / status = READY
8. 任务 COMPLETED
```

MySQL 必须先写，因为它是源数据；ES 和 Milvus 可以重试。

### 10.2 检查点

`document_tasks.result_json`：

```json
{
  "chunk_count": 860,
  "es_indexed": true,
  "milvus_indexed_through": 640,
  "embedding_tokens": 215000
}
```

- ES 写入成功后置 `es_indexed=true`；
- Milvus 每批成功后更新 `milvus_indexed_through`；
- 重试时跳过已完成的步骤；
- 如果 chunk 内容变化，chunk_id 变化，检查点失效，重新开始。

### 10.3 进度

| 阶段 | 进度 |
| --- | --- |
| 读取 + 规范化 | 80-82 |
| 切块 + MySQL | 82-86 |
| ES bulk | 86-90 |
| embedding + Milvus | 90-99 |
| flush + READY | 100 |

进度按已处理 chunk 数 / 总 chunk 数计算。

## 11. 向量检索接口

```python
class VectorSearchResult:
    chunk_id: str
    document_id: str
    version: int
    chunk_index: int
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
- 只检索 `READY` 文档，由上层先取允许的文档 ID 列表，或在索引写入时保证只有 READY 文档存在。

## 12. 重新索引

触发方式：

- 文档重新解析；
- 更换 embedding 模型；
- 切块参数调整；
- 索引损坏修复。

流程：

1. 生成新的 `parse_version`；
2. 重新解析或复用已有 `merged.md`；
3. 删除旧 chunks、ES 文档、Milvus 向量；
4. 重新切块、embedding、写入；
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
| `CHUNK_MAX_TOKENS` | `800` | 最大 token |
| `CHUNK_TARGET_TOKENS` | `500` | 目标 token |
| `CHUNK_OVERLAP_TOKENS` | `80` | 重叠 token |
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
| 单条超 token | `EMBEDDING_INPUT_TOO_LONG` | 是 | 拆分 chunk 后重试 |
| 阿里云超时 | `EMBEDDING_TIMEOUT` | 是 | 重试 |
| Milvus 不可用 | `MILVUS_UNAVAILABLE` | 是 | 重试 |
| Milvus 维度不匹配 | `MILVUS_DIMENSION_MISMATCH` | 否 | 新建 collection |
| MySQL 写入失败 | `CHUNK_STORE_FAILED` | 是 | 重试 |
| 解析产物缺失 | `PARSED_ARTIFACT_MISSING` | 否 | 回到解析阶段 |
| 切块为空 | `EMPTY_DOCUMENT` | 否 | 文档 FAILED，提示无文本 |

失败时：

- 保留已写入的 MySQL chunks，便于排查；
- 删除或覆盖 ES/Milvus 的部分写入，重试时幂等；
- 任务 `FAILED`，记录 `milvus_indexed_through` 检查点；
- 文档 `FAILED`，不参与检索。

## 15. 测试

### 15.1 单元测试

- Markdown 标题解析；
- token 切块、overlap、短块合并；
- 表格拆分和表头重复；
- Excel 文本投影切块；
- chunk_id 确定性；
- RRF 融合（与 ES 联合测试）；
- 过滤表达式构造和转义。

### 15.2 集成测试

- 阿里云 embedding mock：正常、429、超 token、鉴权失败；
- Milvus 测试实例：collection 创建、upsert、搜索、删除；
- MySQL chunk 写入和版本唯一键；
- 检查点续跑：ES 成功后 Milvus 失败，重试不重复写 ES；
- 重新索引删除旧向量；
- 维度变化时拒绝复用 collection。

### 15.3 检索测试

- 构造已知向量，验证 top-k 和过滤；
- 文档 ID、版本、文件类型过滤；
- 空结果；
- Milvus 不可用时上层降级。

## 16. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-VEC-01 | 300MB PDF 解析出的 Markdown 可以切块并写入 MySQL/Milvus | 集成测试、手工测试 |
| AC-VEC-02 | `chunk_id` 在 MySQL、ES、Milvus 中一致 | 集成测试 |
| AC-VEC-03 | 阿里云 embedding 批量 10、失败可重试 | 单元测试、故障注入测试 |
| AC-VEC-04 | Milvus 向量检索按文档和版本过滤正确 | 集成测试 |
| AC-VEC-05 | 重新索引不产生重复向量 | 集成测试 |
| AC-VEC-06 | 索引失败可从中断的 batch 继续 | 故障注入测试 |
| AC-VEC-07 | 向量召回接口返回 `chunk_id`、分数、页码和标题路径 | 接口测试 |
| AC-VEC-08 | 10 万 chunk 的检索延迟在可接受范围（目标 < 300ms，不含 embedding） | 性能测试 |

## 17. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-VEC-01 | 阿里云 embedding 限流 | 索引变慢或失败 | 批量 10、并发 2、指数退避，记录 429 |
| R-VEC-02 | 阿里云 embedding 成本 | 索引成本超预算 | 控制 chunk 数、避免重复 embedding、记录 token 用量 |
| R-VEC-03 | 数据出内网 | 合规风险 | 敏感场景切换自建 embedding，接口层保持可替换 |
| R-VEC-04 | embedding 维度变更 | 旧 collection 不可用 | 新建 collection，全量重建，不混用不同模型向量 |
| R-VEC-05 | Milvus 内存占用 | 与 MySQL/ES 争内存 | 单机限额，至少 16GB 总内存，必要时拆机 |
| R-VEC-06 | Milvus 写入后不可查 | 索引完成但检索无结果 | 写入后 `flush()`，检索使用 `Bounded`，测试使用 `Strong` |
| R-VEC-07 | 切块破坏语义 | 召回质量下降 | 标题感知、表格整体保留、overlap，用真实文档评估 |

## 18. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为向量化模块实施的基线版本。
