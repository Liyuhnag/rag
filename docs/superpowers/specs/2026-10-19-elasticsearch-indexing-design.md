# Elasticsearch 索引设计

- 日期：2026-10-19
- 状态：待评审
- 所属阶段：索引阶段 4/4
- 上游：[文档解析设计](./2026-10-19-document-parsing-design.md)、[向量化与 Milvus 存储设计](./2026-10-19-vectorization-milvus-design.md)
- 配套：混合召回接口

## 1. 目标

为同一份 chunk 建立 Elasticsearch 关键词索引：

- 中文分词使用 IK；
- 支持 BM25 关键词召回；
- 支持标题、正文、页码、工作表位置过滤；
- 支持高亮；
- 与 Milvus 共用 `chunk_id`，支持混合召回；
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
    "refresh_interval": "30s",
    "analysis": {
      "analyzer": {
        "ik_max": {
          "type": "custom",
          "tokenizer": "ik_max_word"
        },
        "ik_smart": {
          "type": "custom",
          "tokenizer": "ik_smart"
        }
      }
    }
  }
}
```

说明：

- 单分片足够当前数据量；
- 开发环境 0 副本，生产至少 1 副本；
- 批量写入期间可临时把 `refresh_interval` 设为 `-1`，完成后恢复；
- IK 插件提供 `ik_max_word` 和 `ik_smart` tokenizer。

### 4.3 Mapping

```json
{
  "mappings": {
    "dynamic": "strict",
    "properties": {
      "chunk_id": {"type": "keyword"},
      "document_id": {"type": "keyword"},
      "version": {"type": "integer"},
      "chunk_index": {"type": "integer"},
      "file_type": {"type": "keyword"},
      "document_name": {
        "type": "text",
        "analyzer": "ik_max",
        "search_analyzer": "ik_smart",
        "fields": {
          "keyword": {"type": "keyword", "ignore_above": 256}
        }
      },
      "content": {
        "type": "text",
        "analyzer": "ik_max",
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
        "analyzer": "ik_max",
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
| `chunk_id` | keyword | 主键、去重、与 Milvus 对齐 |
| `document_id` | keyword | 文档过滤 |
| `version` | integer | 版本过滤 |
| `chunk_index` | integer | 顺序 |
| `file_type` | keyword | 文件类型过滤 |
| `document_name` | text + keyword | 文档名搜索 |
| `content` | text | BM25 主文 |
| `heading_path` | text | 章节召回 |
| `locator` | keyword | 页码/工作表位置 |
| `page_start` / `page_end` | integer | 页码过滤 |
| `token_count` | integer | 统计 |
| `created_at` | date | 时间过滤 |

不存储向量；向量在 Milvus。

### 4.4 动态字段

关闭动态 mapping，避免脏字段：

```json
"dynamic": "strict"
```

所有写入字段必须显式定义。

## 5. 索引写入

### 5.1 写入流程

```text
MySQL document_chunks 写入成功
  -> ES bulk index（_id = chunk_id）
  -> refresh
  -> 更新 document_tasks.result_json.es_indexed = true
```

ES 写入和 Milvus 写入可以在同一个索引任务内顺序执行，也可以并行；本期采用顺序执行，便于错误定位。

### 5.2 Bulk 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `ES_BULK_SIZE` | `500` | 每批文档数 |
| `ES_BULK_TIMEOUT_SECONDS` | `60` | 单批超时 |
| `ES_REFRESH_AFTER_INDEX` | `true` | 完成后刷新 |
| `ES_MAX_RETRIES` | `3` | 批次重试 |

### 5.3 幂等

- `_id = chunk_id`；
- 使用 `index` 操作，重复写入覆盖；
- 重新索引时先删除旧版本，再写入新版本；
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
- 重新解析时先删旧版本，再写新版本。

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

- `content` 权重最高；
- `heading_path` 次之；
- `document_name` 较低；
- `match_phrase` 提升完整短语命中；
- `minimum_should_match` 避免召回过于宽松；
- filter 不参与打分，适合文档/版本/类型过滤；
- 高亮用于前端展示命中片段。

### 6.2 中文查询

- 索引时 `ik_max_word` 会拆出更多词；
- 查询时 `ik_smart` 粒度更粗，减少噪声；
- 对短查询（2-4 字）可以增加 `match_phrase`；
- 对长查询可以降低 `minimum_should_match`；
- 同义词、停用词作为后续优化，不在本期实现。

### 6.3 排序

默认按 `_score` 降序：

```json
"sort": ["_score", {"chunk_index": "asc"}]
```

同一文档的相邻 chunk 分数相同时，按 `chunk_index` 保持顺序。

### 6.4 分页

- 混合召回只需要 top 50，不需要深分页；
- 不使用 `from + size` 做大规模翻页；
- 如果后续需要导出，使用 `search_after`。

## 7. 混合召回

### 7.1 流程

```text
用户问题
  ├── 阿里云 embedding -> Milvus top 50
  └── ES BM25 -> top 50
          │
          ▼
      按 chunk_id 去重
          │
          ▼
      RRF 融合
          │
          ▼
      取 top 10
          │
          ▼
      可选 rerank
```

### 7.2 RRF

```text
score(d) = Σ_i 1 / (k + rank_i(d))
```

- `k = 60`；
- 向量和关键词权重默认相同；
- 如果后续需要调权，可以使用 weighted RRF；
- 同一 chunk 在两路都出现时，分数相加；
- 只在单路出现的 chunk 保留单路分数。

### 7.3 去重

- 以 `chunk_id` 为唯一键；
- 保留 `vector_rank`、`keyword_rank`、`vector_score`、`keyword_score`；
- 最终 `score` 使用 RRF 分数；
- 如果同一 chunk 在两路出现，合并元数据，优先使用 ES 的高亮和 MySQL 的正文。

### 7.4 结果回填

- Milvus 返回 `chunk_id`、`document_id`、`version`、`chunk_index`、页码、标题路径、向量分数；
- ES 返回 `chunk_id`、正文、高亮、BM25 分数；
- 对只在 Milvus 出现的 chunk，按 `chunk_id` 从 MySQL 批量查询正文；
- 对只在 ES 出现的 chunk，直接使用 ES `_source`；
- 最终结果按 RRF 分数排序。

### 7.5 降级

| 故障 | 行为 |
| --- | --- |
| ES 不可用 | 只返回向量召回，`degraded=true` |
| Milvus 不可用 | 只返回关键词召回，`degraded=true` |
| 阿里云 embedding 不可用 | 只返回关键词召回，`degraded=true` |
| 两路都不可用 | 返回 503，记录错误 |

降级时仍要遵守文档/版本过滤。

### 7.6 可选 Rerank

- 默认关闭；
- 开启后对 RRF top 20 调用阿里云 rerank；
- rerank 输入是 query + chunk 正文；
- 输出 top 5-10；
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
2. 从 MySQL `document_chunks` 重新 bulk；
3. 校验 doc count；
4. 切换别名 `document_chunks` 到 v2；
5. 删除旧索引。

### 8.3 备份

- ES 数据是派生索引，可不做独立备份；
- MySQL + SeaweedFS 是源数据；
- 重建时间按 chunk 数量估算，10 万 chunk 约几分钟。

## 9. 与 Milvus 的一致性

| 场景 | 处理 |
| --- | --- |
| MySQL 写入成功，ES 失败 | 重试 ES；MySQL 保留 |
| ES 写入成功，Milvus 失败 | 重试 Milvus；ES 不重复写 |
| Milvus 写入成功，ES 失败 | 重试 ES；Milvus 已有 chunk |
| 文档重新解析 | 删除旧 ES 文档和 Milvus 向量，写新版本 |
| 文档删除 | 删除 ES 文档、Milvus 向量、MySQL chunks、对象存储 |

一致性检查任务（可选）：

- 抽样比对 MySQL chunk_id 与 ES `_id`；
- 抽样比对 MySQL chunk_id 与 Milvus 主键；
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
| `ES_KEYWORD_CANDIDATES` | `50` | 关键词召回候选数 |
| `ES_HIGHLIGHT_ENABLED` | `true` | 是否高亮 |

## 11. 错误处理

| 错误 | code | 可重试 | 处理 |
| --- | --- | --- | --- |
| ES 不可用 | `ES_UNAVAILABLE` | 是 | 重试，索引任务失败 |
| bulk 部分失败 | `ES_BULK_PARTIAL_FAILURE` | 是 | 按失败 item 重试 |
| mapping 冲突 | `ES_MAPPING_CONFLICT` | 否 | 重建索引 |
| 索引不存在 | `ES_INDEX_NOT_FOUND` | 是 | 自动创建 |
| 查询超时 | `ES_QUERY_TIMEOUT` | 是 | 降级或重试 |
| 鉴权失败 | `ES_AUTH_FAILED` | 否 | 检查配置 |

失败时：

- 索引任务记录 `es_indexed=false`；
- 文档不进入 `READY`；
- 重试时按 chunk_id 覆盖，不产生重复；
- 如果 ES 长时间不可用，任务失败，文档 `FAILED`，但 MySQL chunks 保留。

## 12. 测试

### 12.1 单元测试

- mapping 字段完整性；
- 查询 DSL 构造；
- filter 条件；
- RRF 计算和去重；
- 降级逻辑；
- bulk 失败 item 过滤。

### 12.2 集成测试

- 创建索引和 IK analyzer；
- 中文分词效果：
  - “收入结构”能命中“收入结构分析”；
  - “2026年销售数据”能命中对应表格；
- bulk 写入 1000 条；
- 重复写入同一 `_id` 不增加文档数；
- `delete_by_query` 删除指定文档；
- 高亮返回正确片段；
- 文档/版本/类型过滤；
- 混合召回与 Milvus mock 联合测试。

### 12.3 性能测试

- 10 万 chunk 索引构建时间；
- top 50 查询延迟；
- 高亮开销；
- 并发 20 查询的 P95。

## 13. 验收标准

- ES 单节点 + IK 正常启动；
- chunk 可以按 `_id=chunk_id` 幂等写入；
- 中文关键词查询能召回相关内容；
- 文档、版本、文件类型过滤正确；
- 高亮字段可用于前端展示；
- 混合召回返回 top 10，包含 `vector_rank`、`keyword_rank` 和 `score`；
- ES 不可用时检索降级为向量召回；
- 重新索引不产生重复文档；
- 10 万 chunk 的 BM25 查询延迟可接受（目标 < 200ms）。

## 14. 风险

| 风险 | 应对 |
| --- | --- |
| IK 插件版本不匹配 | 固定 ES/IK 版本，镜像内安装，启动时验证 |
| 中文分词效果不佳 | 用真实文档评估，后续加同义词/自定义词典 |
| ES 内存不足 | heap 1-2GB，单节点限额，监控 GC |
| 索引膨胀 | 只存必要字段，`_source` 保留 content 和元数据 |
| 单节点故障 | 本期接受；生产前升级多节点或增加快照 |
| mapping 变更 | 使用别名和新索引重建，不原地修改 |
| 与 Milvus 数据不一致 | 以 MySQL 为准，定期抽样校验，支持重建 |
