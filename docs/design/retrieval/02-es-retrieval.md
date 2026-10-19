# Elasticsearch 检索详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-RET-020 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 检索阶段——Elasticsearch 子块 BM25 检索、高亮、过滤和结果结构 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义检索阶段的 Elasticsearch 关键词检索，包括查询预处理、BM25 查询 DSL、高亮、过滤条件、返回结构和降级策略，作为关键词检索模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- 用户问题的关键词查询构造；
- Elasticsearch `document_chunks` 子块索引的 BM25 检索；
- 文档、类型、页码过滤；
- 高亮片段生成；
- 关键词命中的结果结构和错误处理。

本文档不覆盖：

- 索引阶段的 mapping 和写入（见 RAG-IDX-040）；
- 查询向量化和 Milvus 检索（见 RAG-RET-010）；
- 父块聚合和 RRF 融合（见 RAG-RET-030）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| BM25 | Elasticsearch 默认相关性评分算法。 |
| IK | Elasticsearch 中文分词插件，索引使用 `ik_max_word`，查询使用 `ik_smart`。 |
| Keyword Hit | ES 返回的一条子块命中。 |
| 高亮 | 在返回片段中标记命中关键词，用 `<em>` 包裹。 |
| multi_match | 跨多个字段的查询方式。 |
| best_fields | multi_match 评分模式，取命中最优字段的分数。 |

### 1.4 设计依据

- [检索阶段总体设计](./00-overview.md)
- [Elasticsearch 索引详细设计](../indexing/04-elasticsearch-indexing.md)
- Elasticsearch 8.x 官方文档
- IK 分词插件文档

### 1.5 设计目标

- 查询问题通过 BM25 召回相关子块；
- 索引和查询使用匹配的 IK 分词策略；
- 返回子块正文和高亮片段，供父块结果展示；
- 支持与向量检索一致的过滤条件；
- ES 不可用时不影响向量检索；
- 查询延迟目标 P95 < 200ms。

## 2. 非目标

- 不做查询改写、拼写纠错和同义词扩展；
- 不做跨字段学习和 LTR 排序；
- 不做聚合分析和报表；
- 不做深分页和导出；
- 不做多索引联邦检索。

## 3. 输入输出

### 3.1 输入

```python
class KeywordRetrievalInput:
    query: str
    top_k: int = 50
    document_ids: list[str] | None
    file_types: list[str] | None
    page_range: PageRange | None
    include_highlight: bool = True
    request_id: str
```

### 3.2 输出

```python
class KeywordHit:
    chunk_id: str          # 子块 ID
    parent_id: str         # 父块 ID
    parent_index: int
    child_order: int
    document_id: str
    version: int
    chunk_index: int
    file_type: str
    content: str           # 子块正文
    heading_path: str
    locator: str
    page_start: int | None
    page_end: int | None
    score: float           # BM25 分数
    highlight: str | None  # 高亮片段
```

## 4. 查询预处理

### 4.1 基本规则

- 去除首尾空白；
- 折叠连续空白符；
- 空查询直接返回空结果，不调用 ES；
- 长度超过 `RETRIEVAL_MAX_QUERY_LENGTH` 时截断并记录告警；
- 保留中文、英文、数字和常用标点；
- 不做停用词去除、同义词扩展或查询改写。

### 4.2 分词行为

- 索引字段使用 `ik_max_word`，会拆出更多词项；
- 查询使用 `ik_smart`，粒度更粗，减少噪声；
- 短查询（2-6 字）依赖 `match_phrase` 提升完整短语命中；
- 长查询依赖 `minimum_should_match` 控制召回范围；
- 同义词和自定义词典作为后续优化，不在本阶段实现。

### 4.3 查询模板

- 单条 query 直接构造 DSL；
- 后续如果引入多查询扩展，使用 `bool.should` 组合多个子查询；
- 本期不支持按字段加权的外部配置，权重固定在 DSL 中。

## 5. Elasticsearch 查询

### 5.1 索引回顾

```text
index: document_chunks
shards: 1
replicas: 0（开发）/ 1（生产）
analyzer: ik_max_word（索引）
search_analyzer: ik_smart（查询）
documents: 仅子块（chunk_level = child）
```

参与检索的字段：

| 字段 | 类型 | 权重 |
| --- | --- | --- |
| `content` | text（ik） | 2.0 |
| `heading_path` | text（ik） | 1.2 |
| `document_name` | text（ik） | 0.8 |
| `document_id`、`version`、`file_type`、`chunk_level`、`parent_id` | keyword/integer | filter |
| `page_start`、`page_end` | integer | range |

### 5.2 查询 DSL

```json
{
  "size": 50,
  "track_total_hits": false,
  "_source": [
    "chunk_id", "parent_id", "parent_index", "child_order",
    "document_id", "version", "chunk_index", "file_type",
    "content", "heading_path", "locator", "page_start", "page_end"
  ],
  "sort": [
    {"_score": "desc"},
    {"parent_index": "asc"},
    {"child_order": "asc"}
  ],
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

- `filter` 不参与打分，只做范围限制；
- `chunk_level=child` 保证不会检索到非子块文档；
- `content` 权重最高，`heading_path` 次之，`document_name` 最低；
- `match_phrase` 提升完整短语命中；
- `minimum_should_match` 避免召回过于宽松；
- `_source` 只返回父块聚合和展示需要的字段；
- `track_total_hits=false` 减少统计开销。

### 5.3 过滤条件

| 条件 | DSL | 说明 |
| --- | --- | --- |
| `document_ids` | `terms: {document_id: [...]}` | 限定文档 |
| `file_types` | `terms: {file_type: [...]}` | 限定类型 |
| `page_range` | `range` + `range` 组合 | 子块页码与请求范围重叠 |
| `version`（预留） | `term: {version: n}` | 当前只保留最新版本 |
| `chunk_level` | `term: {chunk_level: "child"}` | 固定条件 |

空集合规则：

- READY 文档集合为空时直接返回空结果；
- `document_ids` 为空数组时不调用 ES；
- `file_types` 为空数组时忽略该条件；
- `page_range` 起止无效时忽略页码条件并记录告警。

### 5.4 高亮

- 默认开启，`include_highlight=false` 时关闭；
- 只高亮 `content` 字段；
- `fragment_size=120`，`number_of_fragments=3`；
- 高亮标签固定为 `<em>`/`</em>`，前端负责转义和展示；
- 高亮片段用于 `matched_children.highlight`，不替代 MySQL 父块正文。

### 5.5 查询参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `size` | `50` | 子块候选数，对应 `RETRIEVAL_KEYWORD_CANDIDATES` |
| `track_total_hits` | `false` | 不统计总数 |
| `timeout` | `3s` | 查询超时 |
| `refresh` | `false` | 查询前不强制刷新 |
| `allow_partial_search_results` | `false` | 分片失败直接报错，便于降级判断 |

## 6. 结果结构

### 6.1 KeywordHit

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
| `content` | string | 子块正文 |
| `heading_path` | string | 标题路径 |
| `locator` | string | 页码或单元格位置 |
| `page_start` | int/null | 起始页 |
| `page_end` | int/null | 结束页 |
| `score` | float | BM25 分数 |
| `highlight` | string/null | 高亮片段 |

### 6.2 排名

- ES 返回顺序即关键词排名，rank 从 1 开始；
- rank 用于父块聚合和 RRF；
- 分数相同时按 `parent_index`、`child_order` 升序，由 DSL 中的 `sort` 保证；
- 单次检索最多返回 `size` 条。

## 7. 错误处理与降级

| 错误 | code | 可重试 | 处理 |
| --- | --- | --- | --- |
| ES 不可用 | `ES_UNAVAILABLE` | 是 | 重试 1 次；仍失败则降级为向量-only |
| ES 查询超时 | `ES_TIMEOUT` | 是 | 重试 1 次；仍失败则降级 |
| 索引不存在 | `ES_INDEX_NOT_FOUND` | 否 | 告警并标记检索不可用 |
| 查询语法错误 | `ES_QUERY_INVALID` | 否 | 返回 500，记录 DSL |
| 分片失败 | `ES_PARTIAL_FAILURE` | 是 | 重试；仍失败则降级 |
| 鉴权失败 | `ES_AUTH_FAILED` | 否 | 告警并标记检索不可用 |

降级行为：

- 记录 WARN 日志和指标 `retrieval_degraded_total{reason="KEYWORD_UNAVAILABLE"}`；
- 响应 `degraded=true`；
- 只使用向量召回，后续流程不变。

## 8. 性能与并发

- 单次查询一个 DSL；
- 并发上限由 ES 连接池决定，初始 20 并发；
- 查询超时 3 秒，慢查询记录 WARN 日志；
- `_source` 只返回必要字段，避免 `content` 之外的冗余数据；
- 高亮有一定开销，压测时分别统计开启和关闭的延迟；
- 10 万子块、单分片 top 50 查询目标 P95 < 200ms；
- 监控指标：查询延迟、返回条数、超时次数、降级次数、高亮耗时。

## 9. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ES_URL` | `http://127.0.0.1:9200` | ES 地址 |
| `ES_INDEX` | `document_chunks` | 索引/别名 |
| `ES_SEARCH_TIMEOUT_SECONDS` | `3` | 查询超时 |
| `ES_SEARCH_MAX_RETRIES` | `1` | 查询重试次数 |
| `RETRIEVAL_KEYWORD_CANDIDATES` | `50` | 子块候选数 |
| `RETRIEVAL_KEYWORD_MIN_SHOULD_MATCH` | `30%` | multi_match 最少匹配比例 |
| `ES_HIGHLIGHT_ENABLED` | `true` | 默认是否高亮 |
| `ES_HIGHLIGHT_FRAGMENT_SIZE` | `120` | 高亮片段长度 |
| `ES_HIGHLIGHT_FRAGMENTS` | `3` | 高亮片段数 |

## 10. 测试

### 10.1 单元测试

- 查询预处理（去空白、截断、空查询）；
- DSL 构造和 `chunk_level=child` 固定条件；
- 过滤条件组合和空集合处理；
- 高亮开关；
- `KeywordHit` 字段映射；
- 排序稳定性。

### 10.2 集成测试

- 使用真实 ES 和 IK 插件；
- 中文问题命中预期子块；
- 文档、类型、页码过滤正确；
- 高亮片段包含 `<em>` 标签；
- `track_total_hits=false` 不返回总数；
- 20 并发查询结果稳定；
- 与 Milvus mock 联合测试，验证两路输出结构一致。

### 10.3 故障测试

- ES 停止：检索降级为向量-only；
- 查询超时：重试一次后降级；
- 索引不存在：告警并标记检索不可用；
- 分片失败：重试后降级。

## 11. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-ER-01 | 中文问题通过 BM25 召回相关子块 | 集成测试 |
| AC-ER-02 | `chunk_level=child` 过滤生效，父块不会出现在结果中 | 集成测试 |
| AC-ER-03 | 文档、类型、页码过滤正确 | 集成测试 |
| AC-ER-04 | 高亮片段正确，可关闭 | 接口测试 |
| AC-ER-05 | 空文档范围或空 `document_ids` 不调用 ES | 单元测试 |
| AC-ER-06 | ES 不可用时降级为向量-only，`degraded=true` | 故障注入测试 |
| AC-ER-07 | 10 万子块、top 50 查询 P95 < 200ms | 性能测试 |

## 12. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-ER-01 | 中文分词效果不佳 | 关键词召回质量下降 | 使用 IK + `match_phrase`，用真实问题评估，后续增加同义词和自定义词典 |
| R-ER-02 | 高亮开销大 | 查询变慢 | 可关闭高亮，限制片段数和长度 |
| R-ER-03 | ES 单节点不可用 | 关键词检索降级 | 接受降级；监控告警，生产前升级或增加副本 |
| R-ER-04 | 查询 DSL 注入 | 安全问题 | DSL 由代码构造，不接受客户端传入 |
| R-ER-05 | 过滤条件过严 | 空结果 | 记录候选数和过滤条件，便于排查 |

## 13. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为关键词检索模块实施的基线版本。
