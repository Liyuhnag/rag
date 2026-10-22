# 检索阶段总体设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-RET-000 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 检索阶段总体架构、检索流程、接口契约、过滤与降级、性能目标 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义 RAG 检索阶段的总体架构、检索流程、接口契约、过滤条件、降级策略、性能目标和跨模块约定，作为向量检索、Elasticsearch 检索、RRF 融合和 rerank 四个模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- 检索服务接口定义和返回结构；
- 查询向量化、Milvus 向量检索和 Elasticsearch 关键词检索的编排；
- 子块召回结果按 `parent_id` 聚合为父块的规则；
- 父块级 RRF 融合和 top-k 返回；
- 可选 rerank 的调用位置和降级方式；
- 检索链路的错误处理、日志、指标和性能目标。

本文档不覆盖：

- MySQL、Elasticsearch、Milvus 的索引写入（见索引阶段 RAG-IDX 文档集）；
- 最终答案生成、对话管理和前端 UI；
- 用户认证、租户隔离和文档权限（当前阶段缺失，见第 6.3 节）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| Parent Chunk / 父块 | 索引阶段生成的语义完整大块，最终返回给上层模型的上下文单元。 |
| Child Chunk / 子块 | 索引阶段生成的检索单元，参与 embedding、BM25 和向量召回。 |
| Vector Hit | Milvus 向量检索返回的一条子块命中。 |
| Keyword Hit | Elasticsearch BM25 检索返回的一条子块命中。 |
| 父块聚合 | 将同一 `parent_id` 下的多个子块命中折叠为一个父块结果。 |
| RRF | Reciprocal Rank Fusion，倒数排名融合。 |
| 父块级 RRF | 先把每路子块命中折叠为父块排名，再对父块排名做 RRF。 |
| Rerank | 对 RRF 候选父块进行二次精排，默认关闭。 |
| READY 文档 | `documents.status = READY`，已经完成解析、切块和双索引的文档。 |
| 降级 | 某一路召回或可选模块不可用时，使用剩余能力继续返回结果。 |

### 1.4 设计依据

- [索引阶段总体设计](../indexing/00-overview.md)
- [向量化与 Milvus 存储详细设计](../indexing/03-vectorization-milvus.md)
- [Elasticsearch 索引详细设计](../indexing/04-elasticsearch-indexing.md)
- 阿里云 DashScope embedding / rerank 官方文档
- Milvus 与 Elasticsearch 官方文档

### 1.5 设计目标

- 提供统一的检索接口，输入问题文本和过滤条件，输出父块列表；
- 向量检索和关键词检索并行执行，单路故障时可以降级；
- 子块召回结果按 `parent_id` 聚合，同一父块只返回一个结果；
- 使用父块级 RRF 融合，避免子块排名直接混入最终分数；
- 返回父块正文、命中子块片段、排名和原始分数，便于引用和高亮；
- 可选接入 rerank，失败时不阻塞主链路；
- 检索链路可观测：每阶段耗时、降级原因和候选数量都有记录；
- 接口、字段和错误码保持稳定，便于后续接入答案生成和前端。

## 2. 非目标

- 不做查询改写、同义词扩展、HyDE、多查询扩展；
- 不做多轮对话和会话上下文管理；
- 不做最终答案生成和引用格式化；
- 不做用户权限和租户隔离（当前阶段缺失，生产前必须补齐）；
- 不做跨语言检索；
- 不做向量压缩、量化或多向量检索；
- 不做检索结果的前端分页和导出。

## 3. 总体架构

```text
┌──────────────────────────────────────────────────────────────────┐
│                         调用方（API / 上层服务）                  │
└──────────────────────────────┬───────────────────────────────────┘
                               │ RetrievalRequest
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                       RetrievalService                            │
│                                                                  │
│  ┌──────────────┐   ┌────────────────────┐   ┌────────────────┐  │
│  │ READY 文档解析│   │ 查询向量化          │   │ 并行两路召回    │  │
│  │ （MySQL）     │   │ （阿里云 embedding）│   │                │  │
│  └──────┬───────┘   └─────────┬──────────┘   │  ┌──────────┐  │  │
│         │                     │              │  │ Milvus   │  │   │
│         │                     └─────────────►│  │ 向量检索  │  │  │
│         │                                    │  └──────────┘  │  │
│         │                                    │  ┌──────────┐  │  │
│         │                                    │  │ ES BM25  │  │  │
│         │                                    │  └──────────┘  │  │
│         │                                    └───────┬────────┘  │
│         │                                            │           │
│         │              ┌─────────────────────────────▼────────┐  │
│         │              │ 父块聚合 + 父块级 RRF 融合             │  │
│         │              └─────────────┬────────────────────────┘  │
│         │                            │                           │
│         └──────────► ┌───────────────▼────────────┐              │
│                      │ 父块回查（MySQL）           │              │
│                      └───────────────┬────────────┘              │
│                                      │                           │
│                      ┌───────────────▼────────────┐              │
│                      │ 可选 rerank（阿里云）       │              │
│                      └───────────────┬────────────┘              │
│                                      │                           │
└──────────────────────────────────────┼───────────────────────────┘
                                       │ RetrievalResponse
                                       ▼
                                   调用方
```

组件职责：

| 组件 | 职责 | 详细设计 |
| --- | --- | --- |
| `RetrievalService` | 编排检索流程、超时控制、降级决策、响应组装 | 本文档 |
| `QueryEmbedder` | 调用阿里云 embedding 生成查询向量 | RAG-RET-010 |
| `VectorRetriever` | 查询 Milvus，返回子块向量命中 | RAG-RET-010 |
| `KeywordRetriever` | 查询 Elasticsearch，返回子块关键词命中 | RAG-RET-020 |
| `ParentAggregator` | 按 `parent_id` 聚合子块命中，折叠为父块排名 | RAG-RET-030 |
| `RRFFuser` | 计算父块级 RRF 分数并排序 | RAG-RET-030 |
| `ParentBackfiller` | 从 MySQL 回查父块正文和元数据 | RAG-RET-030 |
| `Reranker` | 可选精排，默认关闭 | RAG-RET-040 |

## 4. 检索流程

### 4.1 主流程

```text
1. 校验 RetrievalRequest（query 非空、长度限制、top_k 范围）
2. 解析 READY 文档范围（MySQL）
   - 如果允许的文档集合为空，直接返回空结果，不查询 ES/Milvus
3. 查询向量化（阿里云 embedding）
   - 失败时跳过向量检索，进入 ES-only 降级
4. 并行执行：
   - Milvus 子块向量检索 top 50
   - ES 子块 BM25 检索 top 50
5. 合并两路子块命中：
   - 按 parent_id 聚合
   - 每路同一父块只保留最佳（最小）子块排名
6. 父块级 RRF：
   - score(parent) = Σ 1 / (60 + parent_rank_i)
   - 排序、去重、取 top_k 父块
7. 父块回查（MySQL）：
   - 按 parent_id 批量读取父块正文和元数据
   - 父块缺失时跳过并告警
8. 可选 rerank：
   - 对 RRF top 20 父块调用阿里云 rerank
   - 失败时回退 RRF 顺序
9. 组装 RetrievalResponse：
   - 父块正文
   - matched_children 片段
   - 分数、排名、降级标记、耗时
10. 返回结果
```

### 4.2 超时预算

| 阶段 | 超时 | 说明 |
| --- | --- | --- |
| 请求校验 | 立即 | 不发起外部调用 |
| READY 文档解析 | 1s | MySQL 查询，允许短缓存 |
| 查询 embedding | 10s | 与索引阶段一致的超时策略 |
| Milvus 检索 | 3s | 单次查询 |
| ES 检索 | 3s | 单次查询 |
| 父块回查 | 2s | 批量主键查询 |
| rerank | 15s | 可选，超时回退 |
| 总预算 | 30s | 两路并行，总耗时不超过总预算 |

超时后：

- 单路超时按该路不可用处理，进入降级；
- rerank 超时回退到 RRF 结果；
- 两路都超时返回 503，错误码 `RETRIEVAL_UNAVAILABLE`。

## 5. 接口契约

### 5.1 请求

```json
{
  "query": "2026 年华东区收入结构如何？",
  "top_k": 10,
  "document_ids": ["a1b2c3d4e5f6..."],
  "file_types": ["pdf", "xlsx"],
  "page_range": {"start": 1, "end": 50},
  "rerank": false,
  "include_children": true,
  "include_highlight": true
}
```

字段说明：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `query` | string | 是 | 无 | 问题文本，长度 ≤ `RETRIEVAL_MAX_QUERY_LENGTH` |
| `top_k` | int | 否 | `10` | 返回父块数量，范围 1-50 |
| `document_ids` | string[] | 否 | 无 | 限定文档；省略表示全部 READY 文档 |
| `file_types` | string[] | 否 | 无 | 限定文件类型 |
| `page_range` | object | 否 | 无 | 页码过滤，按子块页码与区间是否重叠判断 |
| `rerank` | bool | 否 | `false` | 是否启用 rerank；仅在服务端允许时生效 |
| `include_children` | bool | 否 | `true` | 是否返回命中子块片段 |
| `include_highlight` | bool | 否 | `true` | 是否返回 ES 高亮片段 |

### 5.2 响应

```json
{
  "query": "2026 年华东区收入结构如何？",
  "results": [
    {
      "parent_id": "p1a2b3c4d5e6...",
      "document_id": "a1b2c3d4e5f6...",
      "document_name": "2026 年财务报告.pdf",
      "file_type": "pdf",
      "content": "父块完整正文",
      "heading_path": "第三章 财务分析 > 3.2 收入结构",
      "locator": "page:10-12",
      "page_start": 10,
      "page_end": 12,
      "token_count": 1520,
      "score": 0.032,
      "rrf_score": 0.032,
      "rerank_score": null,
      "matched_child_count": 2,
      "matched_children": [
        {
          "chunk_id": "c9f2a1b3...",
          "content": "命中子块正文片段",
          "highlight": "……<em>收入结构</em>……",
          "vector_rank": 3,
          "keyword_rank": 1,
          "vector_score": 0.78,
          "keyword_score": 12.4
        }
      ]
    }
  ],
  "degraded": false,
  "degraded_reasons": [],
  "reranked": false,
  "rerank_error": null,
  "missing_parent_count": 0,
  "timing_ms": {
    "ready_documents": 3,
    "embedding": 45,
    "vector": 120,
    "keyword": 80,
    "fusion": 5,
    "backfill": 12,
    "rerank": 0,
    "total": 265
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `results` | 父块结果列表，按最终分数降序 |
| `content` | 父块正文，来自 MySQL |
| `score` | 最终排序分数：未 rerank 时为 `rrf_score`，rerank 成功后为 `rerank_score` |
| `rrf_score` | 父块级 RRF 分数 |
| `rerank_score` | rerank 分数，未启用或失败时为 `null` |
| `matched_children` | 命中子块片段，默认每个父块最多 3 条 |
| `matched_child_count` | 该父块命中的子块总数，用于展示和同分排序 |
| `degraded` | 是否发生召回降级 |
| `degraded_reasons` | 降级原因列表，如 `VECTOR_UNAVAILABLE`、`KEYWORD_UNAVAILABLE`、`EMBEDDING_UNAVAILABLE` |
| `reranked` | 是否成功执行 rerank |
| `rerank_error` | rerank 失败原因，成功时为 `null` |
| `missing_parent_count` | 聚合后父块在 MySQL 缺失的数量，正常为 0 |
| `timing_ms` | 各阶段耗时，便于排查 |

### 5.3 错误码

| 错误码 | HTTP | 说明 | 可重试 |
| --- | --- | --- | --- |
| `RETRIEVAL_INVALID_REQUEST` | 400 | 参数校验失败，如 query 为空、top_k 越界 | 否 |
| `RETRIEVAL_EMPTY_SCOPE` | 200 | 允许的文档集合为空，返回空结果 | 否 |
| `EMBEDDING_UNAVAILABLE` | 200 | 查询向量化失败，已降级为 ES-only | 是 |
| `VECTOR_UNAVAILABLE` | 200 | Milvus 不可用，已降级为 ES-only | 是 |
| `KEYWORD_UNAVAILABLE` | 200 | ES 不可用，已降级为向量-only | 是 |
| `RERANK_FAILED` | 200 | rerank 失败，已回退 RRF 结果 | 是 |
| `RETRIEVAL_UNAVAILABLE` | 503 | 两路召回都不可用 | 是 |
| `RETRIEVAL_MYSQL_UNAVAILABLE` | 503 | MySQL 不可用，无法解析文档范围或回查父块 | 是 |
| `RETRIEVAL_TIMEOUT` | 504 | 超过总超时预算 | 是 |
| `RETRIEVAL_INTERNAL_ERROR` | 500 | 未预期错误 | 否 |

约定：

- 单路降级不返回 5xx，通过 `degraded=true` 和 `degraded_reasons` 表达；
- 错误响应体包含 `code`、`message`、`degraded`、`request_id`；
- 所有外部调用都带 `request_id` 和 `trace_id`，便于日志关联。

### 5.4 接口定义

本阶段检索服务优先作为内部服务实现；如果需要 HTTP 暴露，接口定义如下：

```text
POST /api/retrieval/search
Content-Type: application/json

请求体：见 5.1
响应体：见 5.2
```

约定：

- 当前没有鉴权，接口只允许内网或被可信服务调用；
- `request_id` 由调用方传入或服务端生成，响应中原样返回；
- 单请求 `query` 只能有一个，不支持批量查询；
- 批量场景由调用方并发调用，服务端按 20 并发保护。

## 6. 过滤与权限

### 6.1 过滤条件

两路召回都支持相同的过滤条件：

| 条件 | Milvus 表达式 | ES filter | 说明 |
| --- | --- | --- | --- |
| `document_ids` | `document_id in [...]` | `terms: {document_id: [...]}` | 限定文档 |
| `file_types` | `file_type in [...]` | `terms: {file_type: [...]}` | 限定类型 |
| `page_range` | `page_end >= start && page_start <= end` | `range` 组合 | 按子块页码与区间重叠 |
| `version`（预留） | `version == n` | `term: {version: n}` | 当前只保留最新版本 |
| `chunk_level` | 不适用（Milvus 只有子块） | `term: {chunk_level: "child"}` | 只检索子块 |

规则：

- `document_ids` 为空数组时，直接返回空结果，不查询 ES/Milvus；
- `file_types` 为空数组时，等同不过滤；
- 字符串过滤值必须转义，避免表达式注入；
- 页码过滤只对 PDF/Office 等有页码的文档生效，Excel 的 `page_start/page_end` 为空，需要改用 `locator` 或忽略页码条件；
- `top_k` 的有效范围是 1-50，超过上限按 50 处理并记录告警。

### 6.2 READY 文档解析

检索开始前先解析允许检索的文档范围：

```text
allowed_document_ids =
  - 请求指定 document_ids：请求集合 ∩ MySQL 中 status = READY 的文档集合
  - 请求未指定：MySQL 中所有 status = READY 的文档集合
```

约定：

- 只有 READY 文档可检索：正在解析、切块、索引的文档，即使 ES/Milvus 已有部分数据，也不能返回；
- 允许 5 秒进程内缓存 READY 文档 ID 集合，减少 MySQL 压力；
- 解析结果为空时直接返回空结果，不调用 embedding、Milvus、ES；
- 文档数超过阈值（默认 10000）时，需要分页或改为在索引层维护可检索标记，本期先用简单查询 + 缓存。

### 6.3 权限（当前阶段限制）

当前系统没有用户认证、授权和租户隔离：

- 检索接口只允许在内网或被可信服务调用，不能直接暴露公网；
- `document_ids` 由调用方传入，服务端只校验它属于 READY 集合，不校验调用方是否有权访问；
- 生产前必须补齐认证、文档归属和权限过滤；
- 权限方案落地后，需要在 MySQL、ES、Milvus 中增加 `owner_id` 或 `tenant_id` 字段，并在检索过滤条件中加入对应约束。

该限制同时记录在风险 `R-RET-06`。

## 7. 数据来源与一致性

### 7.1 数据职责

| 数据 | 来源 | 是否参与检索 | 说明 |
| --- | --- | --- | --- |
| 父块正文、页码、标题路径 | MySQL `document_chunks` | 最终返回 | 唯一源数据 |
| 子块正文、高亮 | Elasticsearch `document_chunks` | 关键词召回 | 派生索引 |
| 子块向量 | Milvus `document_chunks` | 向量召回 | 派生索引 |
| 文档状态、名称、类型 | MySQL `documents` | 范围解析、展示 | 唯一源数据 |

约定：

- MySQL 是源数据，ES/Milvus 是派生索引；
- 检索结果最终以 MySQL 的父块正文为准；
- ES 的高亮和子块正文只用于展示，不作为最终正文；
- 如果 ES 与 MySQL 的子块正文不一致，以 MySQL 为准并记录告警。

### 7.2 降级矩阵

| 故障 | 向量检索 | 关键词检索 | 父块回查 | 最终行为 |
| --- | --- | --- | --- | --- |
| 正常 | 可用 | 可用 | 可用 | 双路 RRF，返回父块 |
| ES 不可用 | 可用 | 不可用 | 可用 | 向量-only，`degraded=true`，原因 `KEYWORD_UNAVAILABLE` |
| Milvus 不可用 | 不可用 | 可用 | 可用 | 关键词-only，`degraded=true`，原因 `VECTOR_UNAVAILABLE` |
| 查询 embedding 不可用 | 不可用 | 可用 | 可用 | 关键词-only，`degraded=true`，原因 `EMBEDDING_UNAVAILABLE` |
| ES + Milvus 都不可用 | 不可用 | 不可用 | 可用 | 返回 503 `RETRIEVAL_UNAVAILABLE` |
| MySQL 不可用 | 不执行 | 不执行 | 不可用 | 返回 503 `RETRIEVAL_MYSQL_UNAVAILABLE` |
| rerank 不可用 | 正常 | 正常 | 正常 | 回退 RRF 顺序，`reranked=false`，`rerank_error` 有值 |

降级时仍然保证：

- 返回粒度是父块；
- 过滤条件仍然生效；
- `degraded` 和 `degraded_reasons` 准确反映实际情况。

### 7.3 数据完整性

- 每个子块必须有对应父块；父块缺失时跳过该父块并累加 `missing_parent_count`，同时记录告警；
- 聚合时如果发现同一 `parent_id` 对应的 `document_id` 不一致，说明索引数据异常，跳过并告警；
- 父块回查结果数量必须等于聚合后的父块数量减去 `missing_parent_count`；
- 一致性校验任务由索引阶段负责，检索阶段只做运行时兜底。

## 8. 性能目标与容量

### 8.1 性能目标

| 阶段 | 目标（P95） | 说明 |
| --- | --- | --- |
| READY 文档解析 | < 20ms（缓存命中）/ < 100ms（回源） | MySQL 查询 |
| 查询 embedding | < 150ms | 内网到阿里云 |
| Milvus 子块 top 50 | < 300ms | HNSW，10 万子块 |
| ES 子块 top 50 | < 200ms | 单分片 |
| 父块聚合 + RRF | < 20ms | 两次结果集折叠 |
| 父块回查 | < 50ms | 批量主键查询 |
| 全链路（不含 rerank） | < 800ms | 两路并行 |
| 全链路（含 rerank） | < 2000ms | 可选 |

说明：

- 全链路耗时不包含答案生成时间；
- 以上为设计目标值，联调阶段根据真实文档和机器配置校准；
- 并发目标：单实例 20 QPS，P95 不劣化超过 50%；
- 检索结果数量不超过 50 个父块，响应体大小控制在 2MB 以内。

### 8.2 容量估算

| 数据 | 估算 |
| --- | --- |
| 文档数 | 1 万 |
| 父块数 | 100 万 |
| 子块数 | 500 万 |
| 查询向量 | 1024 维 float32，单次约 4KB |
| 并发 | 20 QPS |

以上为上限设计参考，实际容量根据部署机器和真实数据评估。

## 9. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RETRIEVAL_TOP_K` | `10` | 默认返回父块数 |
| `RETRIEVAL_MAX_TOP_K` | `50` | 最大返回父块数 |
| `RETRIEVAL_MAX_QUERY_LENGTH` | `512` | query 最大字符数 |
| `RETRIEVAL_VECTOR_CANDIDATES` | `50` | Milvus 子块候选数 |
| `RETRIEVAL_KEYWORD_CANDIDATES` | `50` | ES 子块候选数 |
| `RRF_K` | `60` | RRF 平滑常数 |
| `RETRIEVAL_MATCHED_CHILDREN_LIMIT` | `3` | 每个父块返回的命中子块片段数上限 |
| `RETRIEVAL_RERANK_ENABLED` | `false` | 是否允许 rerank |
| `RETRIEVAL_RERANK_CANDIDATES` | `20` | 进入 rerank 的父块数 |
| `RETRIEVAL_RERANK_TOP_N` | `10` | rerank 后返回父块数 |
| `RETRIEVAL_QUERY_CACHE_TTL_SECONDS` | `300` | 查询向量缓存 TTL |
| `RETRIEVAL_READY_CACHE_TTL_SECONDS` | `5` | READY 文档集合缓存 TTL |
| `RETRIEVAL_READY_DOC_LIMIT` | `10000` | READY 文档集合单次解析上限 |
| `RETRIEVAL_TOTAL_TIMEOUT_MS` | `30000` | 全链路超时 |

embedding、Milvus、ES 的连接配置沿用索引阶段文档：

- `DASHSCOPE_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_DIM`；
- `MILVUS_URI`、`MILVUS_COLLECTION`；
- `ES_URL`、`ES_INDEX`。

## 10. 日志与指标

### 10.1 日志

每次检索记录一条结构化日志：

```json
{
  "request_id": "req-...",
  "query_hash": "sha256:...",
  "document_scope_size": 120,
  "vector_candidates": 50,
  "keyword_candidates": 50,
  "parent_candidates": 78,
  "result_count": 10,
  "degraded": false,
  "degraded_reasons": [],
  "reranked": false,
  "missing_parent_count": 0,
  "timing_ms": {"embedding": 45, "vector": 120, "keyword": 80, "fusion": 5, "backfill": 12, "rerank": 0, "total": 265}
}
```

约定：

- 不记录完整 query 和父块正文，只记录哈希和长度，避免敏感内容进入日志；
- 记录 `document_ids` 的数量，不记录具体 ID；
- 降级、父块缺失、rerank 失败必须记录 WARN 日志。

### 10.2 指标

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `retrieval_requests_total` | Counter | 检索请求数，带 `degraded` 标签 |
| `retrieval_latency_ms` | Histogram | 各阶段耗时 |
| `retrieval_candidates` | Histogram | 每路候选数、父块候选数 |
| `retrieval_result_count` | Histogram | 返回结果数 |
| `retrieval_degraded_total` | Counter | 降级次数，带 `reason` 标签 |
| `retrieval_missing_parent_total` | Counter | 父块缺失次数 |
| `retrieval_rerank_total` | Counter | rerank 调用数和失败数 |
| `retrieval_empty_total` | Counter | 空结果次数 |

## 11. 测试策略

### 11.1 单元测试

- 请求参数校验和边界值；
- READY 文档集合计算和空集合短路；
- 过滤表达式构造和转义；
- 子块命中按 `parent_id` 聚合；
- 父块级 RRF 分数计算、排序和并列打破；
- 响应组装、`matched_children` 截断、`timing_ms` 统计；
- 降级矩阵和错误码映射；
- rerank 超时回退。

### 11.2 集成测试

- 使用真实 MySQL + Milvus + ES，准备固定文档集；
- 中文问题能够命中预期父块；
- 同一父块的多个子块命中只返回一个父块；
- 父块正文来自 MySQL，且与索引阶段一致；
- 文档、类型、页码过滤正确；
- READY 过滤：正在索引的文档不会被返回；
- 高亮片段正确；
- rerank mock：正常、超时、失败回退。

### 11.3 故障测试

- ES 容器停止：检索降级为向量-only；
- Milvus 容器停止：检索降级为关键词-only；
- embedding 返回 429/超时：降级为关键词-only；
- MySQL 停止：返回 503；
- rerank 超时：返回 RRF 结果；
- 两路都停止：返回 503；
- ES/Milvus 中删除部分子块或父块，验证 `missing_parent_count` 和告警。

### 11.4 性能测试

- 10 万子块数据集下 top 50 两路召回；
- 20 并发持续 5 分钟，统计 P50/P95/P99；
- 查询向量缓存命中率；
- rerank 开启前后的延迟差异。

## 12. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-RET-01 | 输入 query 和过滤条件，返回父块列表，字段完整 | 接口测试 |
| AC-RET-02 | 向量检索和关键词检索并行执行，两路候选数符合配置 | 集成测试 |
| AC-RET-03 | 子块命中按 `parent_id` 聚合，同一父块只返回一个结果 | 集成测试 |
| AC-RET-04 | 父块级 RRF 公式正确，`score`、`rrf_score` 和排名一致 | 单元测试、集成测试 |
| AC-RET-05 | 父块正文来自 MySQL，`matched_children` 包含命中子块片段 | 集成测试 |
| AC-RET-06 | `document_ids`、`file_types`、`page_range` 过滤正确 | 集成测试 |
| AC-RET-07 | 空文档范围直接返回空结果，不调用 ES/Milvus | 单元测试 |
| AC-RET-08 | 只有 READY 文档可检索，正在索引的文档不会返回 | 集成测试 |
| AC-RET-09 | ES 或 Milvus 单路故障时降级，`degraded=true` 且原因正确 | 故障注入测试 |
| AC-RET-10 | MySQL 不可用时返回 503，错误码明确 | 故障注入测试 |
| AC-RET-11 | rerank 开启后返回 `rerank_score` 并按新分数排序；失败时回退 RRF | 集成测试、故障注入测试 |
| AC-RET-12 | 10 万子块、20 并发下全链路 P95 < 800ms（不含 rerank） | 性能测试 |

## 13. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-RET-01 | 阿里云 embedding 延迟或限流 | 查询变慢或降级 | 查询缓存、超时降级、监控 P95 |
| R-RET-02 | Milvus 过滤查询变慢 | 检索延迟上升 | HNSW `ef` 调优、候选数限制、索引文档过滤字段 |
| R-RET-03 | ES 与 MySQL 数据不一致 | 父块缺失或正文不一致 | 索引阶段完整性校验；检索时跳过并告警，以 MySQL 为准 |
| R-RET-04 | 父块级 RRF 排序不理想 | 相关性下降 | `k` 可配置、同分按命中子块数排序，后续引入 weighted RRF |
| R-RET-05 | rerank 成本和延迟 | 费用和响应时间上升 | 默认关闭、限制候选数 20、超时回退、结果缓存 |
| R-RET-06 | 当前没有鉴权 | 检索和数据越权 | 接口仅内网可用；生产前补齐认证、归属和租户过滤 |
| R-RET-07 | 父块正文过长 | 上层模型上下文超限 | 父块最大 2000 token；响应携带 `token_count`，由上层控制预算 |
| R-RET-08 | 查询文本发送到阿里云 | 数据合规风险 | 复用索引阶段合规策略；敏感场景切换自建 embedding/rerank |
| R-RET-09 | ES/Milvus 单节点故障 | 检索降级或不可用 | 本阶段接受；监控告警，生产前升级或增加副本 |
| R-RET-10 | 空结果无法区分“无匹配”和“数据缺失” | 排查困难 | 响应返回 `missing_parent_count` 和降级原因，日志记录候选数 |

## 14. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为检索阶段实施的基线版本。
