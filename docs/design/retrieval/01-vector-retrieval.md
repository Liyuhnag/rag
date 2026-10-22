# 向量检索详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-RET-010 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 检索阶段——查询向量化、Milvus 子块向量检索、过滤表达式、结果结构和错误处理 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义检索阶段的查询向量化和 Milvus 向量检索，包括模型选择、检索参数、过滤表达式、返回结构和降级策略，作为向量检索模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- 用户问题转换为查询向量；
- Milvus `document_chunks` collection 的子块向量检索；
- 文档、类型、页码过滤表达式；
- 向量命中的结果结构和分数处理；
- 向量检索的超时、重试和降级。

本文档不覆盖：

- 索引阶段的向量写入（见 RAG-IDX-030）；
- 关键词检索和 RRF 融合（见 RAG-RET-020、RAG-RET-030）；
- rerank（见 RAG-RET-040）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| Query Embedding | 将用户问题转换为查询向量的过程。 |
| Vector Hit | Milvus 返回的一条子块命中。 |
| HNSW | 分层可导航小世界图索引，Milvus 向量索引类型。 |
| COSINE | 余弦相似度度量，分数越高表示越相似。 |
| ef | HNSW 检索阶段的候选队列大小，越大越准但越慢。 |
| 过滤表达式 | Milvus 的布尔表达式，用于限制检索范围。 |

### 1.4 设计依据

- [检索阶段总体设计](./00-overview.md)
- [向量化与 Milvus 存储详细设计](../indexing/03-vectorization-milvus.md)
- Milvus 官方文档
- 阿里云 DashScope 官方文档

### 1.5 设计目标

- 查询向量与索引向量使用完全相同的模型和维度；
- 单次检索默认返回 50 个子块候选；
- 支持文档、类型、页码过滤，过滤条件下推 Milvus；
- 返回完整的父块定位信息，供父块聚合使用；
- 向量检索失败或超时不影响关键词检索；
- 查询向量缓存降低延迟和阿里云调用量。

## 2. 非目标

- 不做查询改写、多查询扩展、HyDE；
- 不做查询向量持久化；
- 不做多向量或多模态检索；
- 不做向量压缩、量化或 GPU 加速检索；
- 不做跨 collection 联邦检索。

## 3. 输入输出

### 3.1 输入

```python
class VectorRetrievalInput:
    query: str
    top_k: int = 50
    document_ids: list[str] | None
    file_types: list[str] | None
    page_range: PageRange | None
    request_id: str
```

### 3.2 输出

```python
class VectorHit:
    chunk_id: str          # 子块 ID
    parent_id: str         # 父块 ID
    parent_index: int
    child_order: int
    document_id: str
    version: int
    chunk_index: int       # 全局子块序号
    file_type: str
    page_start: int | None
    page_end: int | None
    heading_path: str
    locator: str
    score: float           # COSINE 相似度，越高越相似
```

`VectorHit` 只包含定位和分数信息，不包含正文；正文由父块回查和 ES 结果提供。

## 4. 查询向量化

### 4.1 模型与维度

必须与索引阶段完全一致：

```text
model = text-embedding-v4
dimension = 1024
```

规则：

- 不允许查询使用不同模型或不同维度；
- 模型或维度变更时，必须新建 collection 并全量重建索引，检索层通过配置切换；
- 启动时校验 `EMBEDDING_DIM` 与 Milvus collection 维度一致，不一致直接拒绝启动或标记检索不可用。

### 4.2 调用方式

```python
# 伪代码
response = dashscope.TextEmbedding.call(
    model="text-embedding-v4",
    input=[query],
    dimension=1024,
    api_key=settings.dashscope_api_key,
)
vector = response.output["embeddings"][0]["embedding"]
```

要求：

- 单条查询，批量大小为 1；
- 连接超时 5 秒，读取超时 10 秒；
- 429/5xx 指数退避重试 2 次；
- 401/403 立即失败并告警配置错误；
- 400 且提示输入超限时，截断查询文本后重试一次；仍失败按不可用处理；
- 记录调用耗时和 token 用量。

### 4.3 查询缓存

缓存键：

```text
sha256(EMBEDDING_MODEL + ":" + EMBEDDING_DIM + ":" + query)
```

规则：

- 默认 TTL 300 秒，进程内 LRU 缓存，容量 1000；
- 只缓存成功结果，失败不缓存；
- 缓存命中不调用阿里云，`timing_ms.embedding` 记为 0 或缓存耗时；
- 缓存键不包含过滤条件，因为向量只与 query 有关；
- 敏感场景可以关闭缓存，避免查询文本驻留内存。

### 4.4 错误处理

| 错误 | code | 可重试 | 处理 |
| --- | --- | --- | --- |
| 阿里云鉴权失败 | `EMBEDDING_AUTH_FAILED` | 否 | 告警，向量检索不可用 |
| 阿里云限流 | `EMBEDDING_RATE_LIMITED` | 是 | 退避重试 |
| 阿里云超时 | `EMBEDDING_TIMEOUT` | 是 | 重试 |
| 输入超长 | `EMBEDDING_INPUT_TOO_LONG` | 是 | 截断后重试一次 |
| 维度不匹配 | `EMBEDDING_DIMENSION_MISMATCH` | 否 | 拒绝启动或标记检索不可用 |
| 其他错误 | `EMBEDDING_UNAVAILABLE` | 是 | 降级为关键词-only |

查询向量化失败时：

- 不阻塞整个检索；
- 跳过 Milvus 检索，进入 ES-only 降级；
- 响应 `degraded=true`，`degraded_reasons` 包含 `EMBEDDING_UNAVAILABLE`。

## 5. Milvus 检索

### 5.1 Collection 回顾

```text
collection: document_chunks
description: 文档子块向量（父子分块，仅子块）
index: HNSW
metric: COSINE
dimension: 1024
primary key: chunk_id（子块 ID）
```

字段：

| 字段 | 类型 | 检索用途 |
| --- | --- | --- |
| `chunk_id` | VARCHAR(64) | 主键，结果去重 |
| `parent_id` | VARCHAR(64) | 父块聚合 |
| `parent_index` | INT | 父块排序 |
| `child_order` | INT | 子块顺序 |
| `document_id` | VARCHAR(32) | 过滤 |
| `version` | INT | 过滤 |
| `chunk_index` | INT | 全局子块顺序 |
| `file_type` | VARCHAR(32) | 过滤 |
| `page_start` | INT | 页码过滤和引用 |
| `page_end` | INT | 页码过滤和引用 |
| `heading_path` | VARCHAR(1024) | 引用 |
| `locator` | VARCHAR(512) | 引用 |
| `vector` | FLOAT_VECTOR(1024) | 相似度检索 |

### 5.2 检索参数

```python
search_params = {
    "metric_type": "COSINE",
    "params": {"ef": settings.milvus_search_ef},  # 默认 64
}
results = client.search(
    collection_name="document_chunks",
    data=[query_vector],
    anns_field="vector",
    param=search_params,
    limit=50,
    expr=filter_expression,
    output_fields=[
        "chunk_id", "parent_id", "parent_index", "child_order",
        "document_id", "version", "chunk_index", "file_type",
        "page_start", "page_end", "heading_path", "locator",
    ],
    consistency_level="Bounded",
    timeout=3.0,
)
```

参数说明：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `limit` | `50` | 子块候选数，对应 `RETRIEVAL_VECTOR_CANDIDATES` |
| `ef` | `64` | HNSW 检索候选队列，越大越准越慢 |
| `consistency_level` | `Bounded` | 允许少量延迟，换取吞吐 |
| `timeout` | `3s` | 单次查询超时 |
| `output_fields` | 上述字段 | 不返回 `vector`，减少传输量 |

调优建议：

- 初始 `ef=64`，如果召回质量不足可以提升到 `128`；
- `limit` 不建议超过 200，候选过多会增加父块聚合和回查开销；
- 过滤条件会影响 HNSW 召回率，过滤比例高时需要适当提高 `ef`。

### 5.3 分数语义

- COSINE 相似度范围约 `[-1, 1]`，越高表示越相似；
- `score` 只用于单路排序和展示，不直接与 BM25 分数比较；
- 父块级 RRF 只使用排名，不使用原始分数；
- 响应中保留 `vector_score` 便于调试和后续 weighted RRF 使用。

### 5.4 一致性级别

- 默认 `Bounded`：检索可能看不到最近几秒写入的数据；
- 索引阶段在 ES 和 Milvus 全部写入完成后会执行 flush，再更新文档状态为 `READY`；正常流程下，`READY` 文档的数据已经可检索；
- 检索层只查询 READY 文档，不会主动查询正在索引中的数据；
- 测试环境可以使用 `Strong` 验证写入后立即可查；
- 如果发现 READY 文档检索不到，按索引可见性延迟排查，必要时在置 READY 前增加可见性确认。

## 6. 过滤表达式

### 6.1 构造规则

过滤条件全部下推到 Milvus：

```text
document_id in ["a1b2...", "c3d4..."]
&& version == 3
&& file_type in ["pdf", "xlsx"]
&& page_end >= 10
&& page_start <= 20
```

规则：

- 所有条件用 `&&` 连接；
- 字符串使用双引号，内部双引号和反斜杠需要转义；
- `document_ids` 和 `file_types` 列表长度上限分别为 1000 和 20；
- 列表为空时直接短路，不发起检索；
- 页码条件使用“重叠”语义：子块的页码范围与请求范围有交集；
- 没有过滤条件时 `expr` 为空字符串或不传，取决于 SDK 行为。

### 6.2 转义

```python
def escape_milvus_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
```

注意：

- 只对已知为字符串的字段做转义；
- 不接受客户端直接传入表达式；
- `document_id`、`file_type` 由服务端白名单校验；

### 6.3 空集合短路

以下情况直接返回空结果，不调用 Milvus：

- READY 文档集合为空；
- `document_ids` 显式传入空数组；
- `file_types` 传入空数组；
- `page_range` 起止页码无效（start > end）。

## 7. 结果结构

### 7.1 VectorHit

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chunk_id` | string | 子块 ID，去重键 |
| `parent_id` | string | 父块 ID，聚合键 |
| `parent_index` | int | 父块序号 |
| `child_order` | int | 子块在父块内序号 |
| `document_id` | string | 文档 ID |
| `version` | int | 版本 |
| `chunk_index` | int | 全局子块序号 |
| `file_type` | string | 文件类型 |
| `page_start` | int/null | 起始页 |
| `page_end` | int/null | 结束页 |
| `heading_path` | string | 标题路径 |
| `locator` | string | 页码或单元格位置 |
| `score` | float | COSINE 相似度 |

### 7.2 排名

- Milvus 返回顺序即向量排名，rank 从 1 开始；
- rank 用于父块聚合和 RRF；
- 单次检索最多返回 `limit` 条；
- 相同分数按 `parent_index`、`child_order` 稳定排序，保证可复现。

## 8. 错误处理与降级

| 错误 | code | 可重试 | 处理 |
| --- | --- | --- | --- |
| Milvus 不可用 | `MILVUS_UNAVAILABLE` | 是 | 重试 1 次；仍失败则降级为关键词-only |
| Milvus 查询超时 | `MILVUS_TIMEOUT` | 是 | 重试 1 次；仍失败则降级 |
| 维度不匹配 | `MILVUS_DIMENSION_MISMATCH` | 否 | 标记检索不可用并告警 |
| collection 不存在 | `MILVUS_COLLECTION_NOT_FOUND` | 否 | 标记检索不可用并告警 |
| 过滤表达式错误 | `MILVUS_EXPR_INVALID` | 否 | 返回 500，记录构造参数 |
| 未预期错误 | `MILVUS_ERROR` | 否 | 降级为关键词-only |

降级行为：

- 记录 WARN 日志和指标 `retrieval_degraded_total{reason="VECTOR_UNAVAILABLE"}`；
- 响应 `degraded=true`；
- 只使用 ES 关键词召回，后续流程不变。

## 9. 性能与并发

- 单次检索 1 个查询向量；
- 并发上限由 Milvus 连接池和节点资源决定，初始 20 并发；
- 设置连接池和查询超时，避免慢查询拖垮服务；
- 检索时 `output_fields` 不包含 `content` 和 `vector`，减少网络传输；
- 10 万子块、HNSW `ef=64` 的 top 50 查询目标 P95 < 300ms；
- 监控指标：查询延迟、返回条数、超时次数、降级次数。

## 10. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MILVUS_URI` | `http://127.0.0.1:19530` | Milvus 地址 |
| `MILVUS_COLLECTION` | `document_chunks` | collection 名称 |
| `MILVUS_SEARCH_EF` | `64` | HNSW 检索 `ef` |
| `MILVUS_SEARCH_TIMEOUT_SECONDS` | `3` | 查询超时 |
| `MILVUS_SEARCH_MAX_RETRIES` | `1` | 查询重试次数 |
| `DASHSCOPE_API_KEY` | 无 | 阿里云 API Key |
| `EMBEDDING_MODEL` | `text-embedding-v4` | embedding 模型 |
| `EMBEDDING_DIM` | `1024` | 向量维度 |
| `EMBEDDING_TIMEOUT_SECONDS` | `10` | embedding 超时 |
| `RETRIEVAL_QUERY_CACHE_TTL_SECONDS` | `300` | 查询向量缓存 TTL |
| `RETRIEVAL_VECTOR_CANDIDATES` | `50` | 子块候选数 |

## 11. 测试

### 11.1 单元测试

- 过滤表达式构造和转义；
- 空集合短路；
- `VectorHit` 字段映射；
- 分数排序和稳定排名；
- 查询缓存命中、过期和容量淘汰；
- 向量化异常到 `EMBEDDING_UNAVAILABLE` 的映射。

### 11.2 集成测试

- 使用真实 Milvus，写入固定子块向量后检索；
- 文档、类型、页码过滤正确；
- `limit` 和 `ef` 参数生效；
- 维度不匹配时启动校验失败；
- 查询向量与索引向量使用同一模型和维度；
- 20 并发查询结果稳定。

### 11.3 故障测试

- Milvus 停止：检索降级为关键词-only；
- embedding 429/超时：降级为关键词-only；
- 查询超时：重试一次后降级；
- collection 被删除：告警并标记检索不可用。

## 12. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-VR-01 | 查询向量模型和维度与索引阶段一致 | 启动校验、集成测试 |
| AC-VR-02 | Milvus 返回子块命中，包含 `parent_id` 和定位字段 | 集成测试 |
| AC-VR-03 | 文档、类型、页码过滤表达式正确 | 集成测试 |
| AC-VR-04 | 空文档范围或空 `document_ids` 不调用 Milvus | 单元测试 |
| AC-VR-05 | Milvus 不可用时降级为关键词-only，`degraded=true` | 故障注入测试 |
| AC-VR-06 | 查询向量缓存命中时不调用阿里云 | 单元测试、集成测试 |
| AC-VR-07 | 10 万子块、top 50 查询 P95 < 300ms | 性能测试 |

## 13. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-VR-01 | 查询和索引模型/维度不一致 | 检索结果不可用 | 启动校验，配置集中管理 |
| R-VR-02 | 过滤条件下 HNSW 召回率下降 | 漏召回 | 提高 `ef`，监控召回评估，必要时调整过滤策略 |
| R-VR-03 | Milvus 单节点不可用 | 向量检索降级 | 接受降级；监控告警，生产前增加副本或高可用 |
| R-VR-04 | 查询向量缓存泄露敏感文本 | 合规风险 | 缓存键使用哈希，敏感场景关闭缓存 |
| R-VR-05 | 候选数过大 | 聚合和回查变慢 | `limit` 上限 200，父块聚合限制候选数 |

## 14. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为向量检索模块实施的基线版本。
