# Elasticsearch 索引详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-IDX-040 |
| 文档版本 | V0.9.1（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 索引阶段——子块 Elasticsearch 索引、中文分词、BM25 查询与父块混合召回 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |
| V0.9.1 | 2026-10-19 | 架构组 | 评审前修订：ES 只索引子块，检索按 parent_id 聚合为父块，RRF 改为父块级融合。 |

## 1. 概述

### 1.1 编写目的

本文档定义 Elasticsearch 子块索引结构、中文分词配置、批量写入、关键词查询、父块聚合、混合召回和索引生命周期，作为关键词检索模块开发、测试和运维的依据。

### 1.2 适用范围

本文档适用于：

- Elasticsearch 单节点部署和 IK 插件配置；
- `document_chunks` 子块索引 mapping 和写入；
- 中文 BM25 子块查询和高亮；
- 子块命中按 `parent_id` 聚合为父块；
- 向量召回与关键词召回的父块级 RRF 融合；
- 索引重建、删除和一致性检查。

本文档不覆盖向量生成和 Milvus 写入；后者见向量化与 Milvus 存储详细设计。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| IK | Elasticsearch 中文分词插件。 |
| BM25 | Elasticsearch 默认的相关性评分算法。 |
| Mapping | 索引字段类型和分析器定义。 |
| Alias | 指向一个或多个索引的别名，用于无缝重建。 |
| Bulk | Elasticsearch 批量写入接口。 |
| RRF | Reciprocal Rank Fusion，倒数排名融合。 |
| Parent Chunk / 父块 | 最终返回给上层模型的上下文单元，不写入 ES。 |
| Child Chunk / 子块 | 写入 ES 并参与 BM25 召回的单元。 |
| 高亮 | 在查询结果中标记命中关键词的片段。 |

### 1.4 设计依据

- [RAG 文档索引阶段总体设计](./00-overview.md)
- [文档解析详细设计](./02-document-parsing.md)
- [向量化与 Milvus 存储详细设计](./03-vectorization-milvus.md)
- Elasticsearch 8.x 官方文档
- IK 分词插件文档

### 1.5 设计目标

为同一份子块建立 Elasticsearch 关键词索引，并支撑父块级最终检索：

- 中文分词使用 IK；
- 支持子块 BM25 关键词召回；
- 支持标题、正文、页码、工作表位置过滤；
- 支持子块高亮；
- 与 Milvus 共用子块 `chunk_id`，支持按 `parent_id` 聚合的混合召回；
- 支持批量写入、重试、重新索引。

## 2. 非目标

- 不做 ES 集群高可用；
- 不做跨集群搜索；
- 不做 ES 作为主数据库；
- 不做语义向量写入 ES（向量在 Milvus）；
- 不做复杂 NLP 管道和同义词词典（可作为后续优化）。

## 3. 部署

### 3.1 单节点

- Elasticsearch 8.x；
- 单节点、单分片、开发环境 0 副本；
- 与后端同机；
- 数据卷持久化；
- 默认端口 `9200`，只监听内网；
- 开发环境关闭 X-Pack 安全，生产环境开启 TLS 和认证。

### 3.2 中文分词

安装 IK 插件：

- 插件版本必须与 ES 版本完全匹配；
- 在自定义镜像中安装，避免运行时下载；
- 索引时使用 `ik_max_word`，查询时使用 `ik_smart`；
- 如果 IK 安装失败，ES 启动会报错，必须在部署阶段验证。

### 3.3 资源

- 开发环境 JVM heap 1GB；
- 生产建议 2-4GB，不超过物理内存的 50%；
- 单节点至少预留 2GB 内存给 ES；
- 磁盘按原始文本和索引膨胀 1.5-2 倍估算。

## 4. 索引设计

### 4.1 索引名

```text
document_chunks
```

生产环境建议使用别名：

```text
document_chunks -> document_chunks_v1
```

这样 mapping 变更时可以创建 `document_chunks_v2`，重建完成后原子切换别名。

### 4.2 Settings

```json
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "refresh_interval": "30s"
  }
}
```

说明：

- 单分片足够当前数据量；
- 开发环境 0 副本，生产至少 1 副本；
- 批量写入期间可临时把 `refresh_interval` 设为 `-1`，完成后恢复；
- IK 插件提供 `ik_max_word` 和 `ik_smart` analyzer。

### 4.3 Mapping

```json
{
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "chunk_id": {"type": "keyword"},
      "chunk_level": {"type": "keyword"},
      "parent_id": {"type": "keyword"},
      "parent_index": {"type": "integer"},
      "child_order": {"type": "integer"},
      "document_id": {"type": "keyword"},
      "version": {"type": "integer"},
      "chunk_index": {"type": "integer"},
      "file_type": {"type": "keyword"},
      "document_name": {
        "type": "text",
        "analyzer": "ik_max_word",
        "search_analyzer": "ik_smart",
        "fields": {
          "keyword": {"type": "keyword", "ignore_above": 256}
        }
      },
      "content": {
        "type": "text",
        "analyzer": "ik_max_word",
        "search_analyzer": "ik_smart",
        "fields": {
          "smart": {
            "type": "text",
            "analyzer": "ik_smart"
          }
        }
      },
      "heading_path": {
        "type": "text",
        "analyzer": "ik_max_word",
        "search_analyzer": "ik_smart"
      },
      "locator": {"type": "keyword"},
      "page_start": {"type": "integer"},
      "page_end": {"type": "integer"},
      "token_count": {"type": "integer"},
      "created_at": {"type": "date"}
    }
  }
}
```

字段说明：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `chunk_id` | keyword | 子块主键、去重、与 Milvus 对齐 |
| `chunk_level` | keyword | 固定为 `child`；保留字段便于过滤和未来扩展 |
| `parent_id` | keyword | 子块聚合为父块 |
| `parent_index` | integer | 父块顺序 |
| `child_order` | integer | 子块在父块内顺序 |
| `document_id` | keyword | 文档过滤 |
| `version` | integer | 版本过滤 |
| `chunk_index` | integer | 全局子块顺序 |
| `file_type` | keyword | 文件类型过滤 |
| `document_name` | text + keyword | 文档名搜索 |
| `content` | text | BM25 主文 |
| `heading_path` | text | 章节召回 |
| `locator` | keyword | 页码/工作表位置 |
| `page_start` / `page_end` | integer | 页码过滤 |
| `token_count` | integer | 统计 |
| `created_at` | date | 时间过滤 |

`document_name` 来自 `documents.name`，由索引任务写入；ES 只存储子块，不存储父块正文；父块在最终检索时从 MySQL 读取。不存储向量，向量在 Milvus。

### 4.4 动态字段

关闭动态 mapping，避免脏字段：

```json
"dynamic": "strict"
```

所有写入字段必须显式定义。

## 5. 索引写入

### 5.1 写入流程

```text
MySQL document_chunks 写入成功（先父块，后子块）
  -> ES bulk index（仅子块，_id = 子块 chunk_id）
  -> refresh
  -> 更新 document_tasks.result_json.es_indexed = true
```

ES 只写入 `chunk_level=child` 的子块；父块不写入 ES，最终检索时从 MySQL 按 `parent_id` 读取。ES 写入和 Milvus 写入可以在同一个索引任务内顺序执行，也可以并行；本阶段采用顺序执行，便于错误定位。

### 5.2 Bulk 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `ES_BULK_SIZE` | `500` | 每批文档数 |
| `ES_BULK_TIMEOUT_SECONDS` | `60` | 单批超时 |
| `ES_REFRESH_AFTER_INDEX` | `true` | 完成后刷新 |
| `ES_MAX_RETRIES` | `3` | 批次重试 |

### 5.3 幂等

- `_id = 子块 chunk_id`；
- 使用 `index` 操作，重复写入覆盖；
- 只写入子块，父块不写入；
- 重新索引时先删除旧版本子块文档，再写入新版本；
- 如果只重试部分失败项，按返回的 `items` 过滤，重新 bulk。

### 5.4 删除

按文档删除：

```json
{
  "query": {
    "term": {"document_id": "a1b2..."}
  }
}
```

使用 `delete_by_query`：

- `conflicts=proceed`；
- `refresh=true`；
- 删除后确认 count 为 0；
- 重新解析时先删旧版本子块文档，再写新版本；
- 父块存放在 MySQL，不在此删除范围内，由 MySQL 清理旧版本父块。

### 5.5 刷新

- 批量写入时 `refresh=false`；
- 全部批次完成后 `refresh=true`；
- 或调用 `POST /document_chunks/_refresh`；
- 索引任务完成后再把文档标记 `READY`，确保检索能看到新数据。

## 6. 关键词查询

### 6.1 查询 DSL

```json
{
  "size": 50,
  "query": {
    "bool": {
      "filter": [
        {"term": {"chunk_level": "child"}},
        {"terms": {"document_id": ["a1b2", "c3d4"]}},
        {"term": {"version": 1}},
        {"term": {"file_type": "pdf"}}
      ],
      "should": [
        {
          "multi_match": {
            "query": "收入结构",
            "fields": ["content^2.0", "heading_path^1.2", "document_name^0.8"],
            "type": "best_fields",
            "operator": "or",
            "minimum_should_match": "30%"
          }
        },
        {
          "match_phrase": {
            "content": {
              "query": "收入结构",
              "boost": 2.0
            }
          }
        }
      ],
      "minimum_should_match": 1
    }
  },
  "highlight": {
    "fields": {
      "content": {
        "fragment_size": 120,
        "number_of_fragments": 3,
        "pre_tags": ["<em>"],
        "post_tags": ["</em>"]
      }
    }
  }
}
```

说明：

- 只检索 `chunk_level=child` 的子块；
- `content` 权重最高；
- `heading_path` 次之；
- `document_name` 较低；
- `match_phrase` 提升完整短语命中；
- `minimum_should_match` 避免召回过于宽松；
- filter 不参与打分，适合层级/文档/版本/类型过滤；
- 返回结果必须包含 `parent_id`、`parent_index`、`child_order`，供父块聚合使用；
- 高亮用于前端展示命中片段。

### 6.2 中文查询

- 索引时 `ik_max_word` 会拆出更多词；
- 查询时 `ik_smart` 粒度更粗，减少噪声；
- 对短查询（2-4 字）可以增加 `match_phrase`；
- 对长查询可以降低 `minimum_should_match`；
- 同义词、停用词作为后续优化，不在本阶段实现。

### 6.3 排序

默认按 `_score` 降序：

```json
"sort": ["_score", {"parent_index": "asc"}, {"child_order": "asc"}]
```

同一文档的相邻子块分数相同时，按 `parent_index` 和 `child_order` 保持顺序，便于父块聚合和引用。

### 6.4 分页

- 混合召回只需要 top 50，不需要深分页；
- 不使用 `from + size` 做大规模翻页；
- 如果后续需要导出，使用 `search_after`。

## 7. 混合召回

> 本章定义索引阶段与检索阶段的召回契约和融合规则；检索阶段的完整设计（向量检索、ES 检索、RRF 融合、rerank）见[检索阶段设计文档集](../retrieval/README.md)。

### 7.1 流程

```text
用户问题
  ├── 阿里云 embedding -> Milvus 子块 top 50
  └── ES BM25 -> 子块 top 50
          │
          ▼
      按 parent_id 聚合
      （同一父块只保留该路最佳子块排名）
          │
          ▼
      父块级 RRF 融合
          │
          ▼
      取 top 10 父块
          │
          ▼
      从 MySQL 回查父块正文 + 命中子块片段
          │
          ▼
      可选 rerank（父块正文）
```

### 7.2 父块级 RRF

```text
score(parent) = Σ_i 1 / (k + parent_rank_i(parent))
```

- `k = 60`；
- 每一路先把子块命中折叠为父块排名：
  - 同一 `parent_id` 在该路只保留最佳（最小）子块排名；
  - 按最佳子块排名对父块排序，得到该路的 `parent_rank`；
- 向量和关键词权重默认相同；
- 如果后续需要调权，可以使用 weighted RRF；
- 同一父块在两路都出现时，分数相加；
- 只在单路出现的父块保留单路分数；
- 分数相同时，按 `matched_child_count` 降序、`parent_index` 升序打破并列。

### 7.3 聚合与去重

- 子块命中以 `chunk_id` 为唯一键去重；
- 父块以 `parent_id` 为唯一键聚合，同一父块只返回一个结果；
- 每个父块保留 `matched_children`：
  - 命中子块 `chunk_id`；
  - 子块正文片段或高亮；
  - `vector_rank`、`keyword_rank`、`vector_score`、`keyword_score`；
- `matched_child_count` 用于展示命中的子块数量，并作为同分排序依据；
- 父块正文不在 ES 中，最终从 MySQL 回查；ES 子块片段用于引用和高亮。

### 7.4 结果回填

- Milvus 返回：子块 `chunk_id`、`parent_id`、`parent_index`、`child_order`、`document_id`、`version`、`chunk_index`、页码、标题路径、向量分数；
- ES 返回：子块 `chunk_id`、`parent_id`、正文、高亮、BM25 分数；
- 按 `parent_id` 聚合后，从 MySQL `document_chunks` 批量读取 `chunk_level=PARENT` 的父块正文与元数据；
- 对只在 Milvus 出现的子块，按 `chunk_id` 从 MySQL 查询子块正文；
- 对只在 ES 出现的子块，直接使用 ES `_source` 和 highlight；
- 最终结果按父块级 RRF 分数排序，返回父块正文和命中子块片段。

### 7.5 降级

| 故障 | 行为 |
| --- | --- |
| ES 不可用 | 只用向量召回子块，聚合为父块返回，`degraded=true` |
| Milvus 不可用 | 只用关键词召回子块，聚合为父块返回，`degraded=true` |
| 阿里云 embedding 不可用 | 只用关键词召回子块，聚合为父块返回，`degraded=true` |
| MySQL 不可用 | 无法回查父块正文，返回 503，记录错误 |
| 两路召回都不可用 | 返回 503，记录错误 |

降级时仍要遵守文档/版本过滤，并且仍然返回父块粒度结果；如果父块回查失败，说明存在数据完整性问题，跳过该父块并告警。

### 7.6 可选 Rerank

- 默认关闭；
- 开启后对 RRF top 20 父块调用阿里云 rerank；
- rerank 输入是 query + 父块正文，不是子块；
- 输出 top 5-10 父块；
- rerank 超时或失败时回退到 RRF 结果；
- 不把 rerank 分数混入 RRF 分数。

## 8. 索引生命周期

### 8.1 创建

启动时检查索引是否存在：

- 不存在：创建 settings/mapping，等待 yellow/green；
- 存在：校验 mapping 字段和 IK analyzer；
- mapping 不兼容：拒绝写入，提示重建索引。

### 8.2 重建

更换分词器、mapping 或切块策略时：

1. 创建 `document_chunks_v2`；
2. 从 MySQL `document_chunks` 重新 bulk（仅 `chunk_level=child` 的子块）；
3. 校验 doc count；
4. 切换别名 `document_chunks` 到 v2；
5. 删除旧索引。

父块不需要重建 ES 索引；如果切块策略变化导致父子关系变化，需要同时重新生成 MySQL 父块和子块。

### 8.3 备份

- ES 数据是派生索引，可不做独立备份；
- MySQL + SeaweedFS 是源数据；
- 重建时间按子块数量估算，10 万子块约几分钟。

## 9. 与 Milvus 的一致性

| 场景 | 处理 |
| --- | --- |
| MySQL 写入成功，ES 失败 | 重试 ES；MySQL 保留 |
| ES 写入成功，Milvus 失败 | 重试 Milvus；ES 不重复写 |
| Milvus 写入成功，ES 失败 | 重试 ES；Milvus 已有子块 |
| 父块 | 只存 MySQL；ES/Milvus 均不存父块 |
| 文档重新解析 | 删除旧 ES 子块文档、Milvus 子块向量和 MySQL 旧父块/子块，写新版本 |
| 文档删除 | 删除 ES 子块文档、Milvus 子块向量、MySQL 父块/子块、对象存储 |

一致性检查任务：

- 抽样比对 MySQL 子块 `chunk_id` 与 ES `_id`；
- 抽样比对 MySQL 子块 `chunk_id` 与 Milvus 主键；
- 校验每个子块的 `parent_id` 在 MySQL 中存在对应父块；
- 校验父块的 `child_count` 等于实际子块数量；
- 发现缺失时记录告警，不自动修复。

## 10. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ES_URL` | `http://127.0.0.1:9200` | ES 地址 |
| `ES_INDEX` | `document_chunks` | 索引/别名 |
| `ES_USERNAME` | 空 | 开发环境为空 |
| `ES_PASSWORD` | 空 | 开发环境为空 |
| `ES_BULK_SIZE` | `500` | 批量条数 |
| `ES_BULK_TIMEOUT_SECONDS` | `60` | 单批超时 |
| `ES_MAX_RETRIES` | `3` | 重试次数 |
| `ES_REFRESH_AFTER_INDEX` | `true` | 完成后刷新 |

检索阶段的候选数、RRF、命中片段和 rerank 配置统一在[检索阶段总体设计](../retrieval/00-overview.md)和[Elasticsearch 检索详细设计](../retrieval/02-es-retrieval.md)中定义，本文档不重复配置。

## 11. 错误处理

| 错误 | code | 可重试 | 处理 |
| --- | --- | --- | --- |
| ES 不可用 | `ES_UNAVAILABLE` | 是 | 重试，索引任务失败 |
| bulk 部分失败 | `ES_BULK_PARTIAL_FAILURE` | 是 | 按失败 item 重试 |
| mapping 冲突 | `ES_MAPPING_CONFLICT` | 否 | 重建索引 |
| 索引不存在 | `ES_INDEX_NOT_FOUND` | 是 | 自动创建 |
| 查询超时 | `ES_QUERY_TIMEOUT` | 是 | 降级或重试 |
| 鉴权失败 | `ES_AUTH_FAILED` | 否 | 检查配置 |
| 父块缺失 | `PARENT_NOT_FOUND` | 否 | 数据完整性问题，跳过并告警 |
| MySQL 不可用 | `MYSQL_UNAVAILABLE` | 是 | 重试；父块回查失败时返回 503 |

失败时：

- 索引任务记录 `es_indexed=false`；
- 文档不进入 `READY`；
- 重试时按子块 `chunk_id` 覆盖，不产生重复；
- 如果 ES 长时间不可用，任务失败，文档 `FAILED`，但 MySQL 父块和子块保留。

## 12. 测试

### 12.1 单元测试

- mapping 字段完整性（含 `chunk_level`、`parent_id`、`parent_index`、`child_order`）；
- 查询 DSL 构造和 `chunk_level=child` 过滤；
- filter 条件；
- 子块命中按 `parent_id` 聚合；
- 父块级 RRF 计算和去重；
- 同分排序稳定性；
- 降级逻辑；
- bulk 失败 item 过滤。

### 12.2 集成测试

- 创建索引和 IK analyzer；
- 中文分词效果：
  - “收入结构”能命中“收入结构分析”；
  - “2026年销售数据”能命中对应表格；
- bulk 写入 1000 条子块，父块不出现在 ES；
- 重复写入同一 `_id` 不增加文档数；
- `delete_by_query` 删除指定文档的子块；
- 高亮返回正确片段；
- 文档/版本/类型过滤；
- 多个子块命中同一父块时只返回一个父块结果；
- 父块正文从 MySQL 回查正确；
- 父块缺失时跳过并告警；
- 混合召回与 Milvus mock 联合测试，结果粒度是父块。

### 12.3 性能测试

- 10 万子块索引构建时间；
- top 50 子块查询延迟；
- 父块聚合和批量回查开销；
- 高亮开销；
- 并发 20 查询的 P95。

## 13. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-ES-01 | ES 单节点 + IK 正常启动 | 部署验证 |
| AC-ES-02 | 子块可以按 `_id=chunk_id` 幂等写入，父块不写入 ES | 集成测试 |
| AC-ES-03 | 中文关键词查询能召回相关子块 | 集成测试 |
| AC-ES-04 | 文档、版本、文件类型过滤正确 | 接口测试 |
| AC-ES-05 | 高亮字段可用于前端展示 | 接口测试 |
| AC-ES-06 | 子块命中按 `parent_id` 聚合，同一父块只返回一个结果 | 集成测试 |
| AC-ES-07 | 混合召回返回 top 10 父块，包含父块正文和命中子块片段 | 集成测试 |
| AC-ES-08 | 父块级 RRF 的 `score`、`matched_children` 计算正确 | 单元测试、集成测试 |
| AC-ES-09 | ES 不可用时检索降级为向量召回，仍返回父块结果 | 故障注入测试 |
| AC-ES-10 | 重新索引不产生重复子块文档 | 集成测试 |
| AC-ES-11 | 10 万子块的 BM25 查询延迟可接受（目标 < 200ms） | 性能测试 |

## 14. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-ES-01 | IK 插件版本不匹配 | ES 启动失败或中文分词异常 | 固定 ES/IK 版本，镜像内安装，启动时验证 |
| R-ES-02 | 中文分词效果不佳 | 关键词召回质量下降 | 用真实文档评估，后续增加同义词和自定义词典 |
| R-ES-03 | ES 内存不足 | 查询变慢或节点不稳定 | heap 1-2GB，单节点限额，监控 GC |
| R-ES-04 | 索引膨胀 | 磁盘占用过高 | 只存必要字段，`_source` 保留 content 和元数据 |
| R-ES-05 | 单节点故障 | 关键词检索不可用 | 本阶段接受；生产前升级多节点或增加快照 |
| R-ES-06 | mapping 变更 | 需要重建索引 | 使用别名和新索引重建，不原地修改 |
| R-ES-07 | 与 Milvus 数据不一致 | 召回结果缺失或重复 | 以 MySQL 为准，定期抽样校验，支持重建 |
| R-ES-08 | 父块只存 MySQL，ES 不参与父块检索 | 父块回查失败时结果缺失 | 写入前保证父块先于子块；索引完成后校验孤立子块为 0；运行时父块缺失则跳过并告警 |
| R-ES-09 | 父子分块增加聚合复杂度 | 实现和调试成本上升 | 聚合逻辑单元测试覆盖；结果粒度和字段在接口层固定为父块 |

## 15. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为 Elasticsearch 索引模块实施的基线版本。
