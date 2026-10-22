# RRF 融合详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-RET-030 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 检索阶段——子块命中父块聚合、父块级 RRF、top-k、父块回查和响应组装 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义向量命中和关键词命中如何按 `parent_id` 聚合为父块、如何计算父块级 RRF 分数、如何回查父块正文并组装最终响应，作为融合和编排模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- 子块命中的去重和父块聚合；
- 每路父块排名折叠；
- 父块级 RRF 分数计算、排序和并列打破；
- top-k 父块选择；
- 父块正文回查和 `matched_children` 组装；
- 边界情况和降级处理。

本文档不覆盖：

- 向量检索和关键词检索的实现（见 RAG-RET-010、RAG-RET-020）；
- rerank 的调用和分数处理（见 RAG-RET-040）；
- 索引写入和数据一致性维护（见 RAG-IDX 文档集）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| 子块排名 | 单路召回结果中，子块按该路分数从高到低的位置，rank 从 1 开始。 |
| 父块排名 | 把单路子块命中折叠到父块后，父块按最佳子块排名排序得到的位置，从 1 开始。 |
| 最佳子块排名 | 同一父块在该路中所有命中子块的最小 rank。 |
| RRF | Reciprocal Rank Fusion，倒数排名融合。 |
| matched_children | 父块结果中命中的子块片段列表。 |
| missing_parent_count | 聚合后父块在 MySQL 中缺失的数量。 |

### 1.4 设计依据

- [检索阶段总体设计](./00-overview.md)
- [向量检索详细设计](./01-vector-retrieval.md)
- [Elasticsearch 检索详细设计](./02-es-retrieval.md)
- [向量化与 Milvus 存储详细设计](../indexing/03-vectorization-milvus.md)

### 1.5 设计目标

- 同一父块的多个子块命中只返回一个父块结果；
- 父块排名由该路最佳子块排名决定，避免“多个平庸子块”压过“一个强子块”；
- RRF 只使用排名，不混合两路原始分数；
- 最终返回父块正文，子块片段用于引用和高亮；
- 排序结果确定、可复现；
- 响应体大小可控。

## 2. 非目标

- 不做加权 RRF（预留配置，默认不实现）；
- 不做 Cross-Encoder 精排（见 RAG-RET-040）；
- 不做父块之间的邻接扩展或上下文拼接；
- 不做结果聚类、去重合并和摘要生成；
- 不做多轮对话的上下文选择。

## 3. 输入输出

### 3.1 输入

```python
class FusionInput:
    query: str
    vector_hits: list[VectorHit]      # 最多 50 条，已按向量排名排序
    keyword_hits: list[KeywordHit]    # 最多 50 条，已按 BM25 排名排序
    top_k: int = 10
    matched_children_limit: int = 3
    request_id: str
```

### 3.2 输出

```python
class FusedParent:
    parent_id: str
    document_id: str
    document_name: str
    file_type: str
    content: str
    heading_path: str
    locator: str
    page_start: int | None
    page_end: int | None
    token_count: int
    score: float              # RRF 分数
    matched_child_count: int
    matched_children: list[MatchedChild]


class MatchedChild:
    chunk_id: str
    content: str
    highlight: str | None
    vector_rank: int | None
    keyword_rank: int | None
    vector_score: float | None
    keyword_score: float | None
```

## 4. 父块聚合

### 4.1 子块去重

每一路内部先按 `chunk_id` 去重：

- 同一 `chunk_id` 只保留第一次出现的记录；
- 去重后重新编号 rank，从 1 开始；
- 两路之间的 `chunk_id` 不去重，因为需要保留各自的排名和分数。

### 4.2 每路父块排名折叠

对每一路命中列表分别执行：

```python
def collapse_to_parents(hits, path: str) -> list[ParentRankEntry]:
    best_rank: dict[str, int] = {}
    matched: dict[str, list[MatchedChildRef]] = {}

    for rank, hit in enumerate(hits, start=1):
        pid = hit.parent_id
        if pid not in best_rank or rank < best_rank[pid]:
            best_rank[pid] = rank
        matched.setdefault(pid, []).append(
            MatchedChildRef(
                chunk_id=hit.chunk_id,
                rank=rank,
                score=hit.score,
                path=path,
                highlight=getattr(hit, "highlight", None),
            )
        )

    ordered = sorted(best_rank.items(), key=lambda item: (item[1], item[0]))
    return [
        ParentRankEntry(parent_id=pid, best_child_rank=rank, children=matched[pid])
        for pid, rank in ordered
    ]
```

规则：

- 折叠后按 `best_child_rank` 升序排列，得到该路的父块排名；
- 父块排名从 1 开始，与子块 rank 无关；
- 同一父块在该路只保留一个父块排名；
- `matched_children` 收集该父块在该路的全部命中子块，供后续展示。

### 4.3 两路合并

- 以 `parent_id` 为键合并两路结果；
- 每个父块记录：
  - 向量路父块排名（可能没有）；
  - 关键词路父块排名（可能没有）；
  - 两路的命中子块列表；
- 同一父块在两路都出现时，两路排名都要保留，分别参与 RRF。

## 5. RRF 融合

### 5.1 计算公式

```text
score(parent) = Σ_i 1 / (K + parent_rank_i(parent))
```

其中：

- `K = RRF_K`，默认 60；
- `parent_rank_i(parent)` 是父块在第 `i` 路的父块排名；
- 如果父块不在某一路出现，该项不参与求和；
- 一个父块最多得到两个 RRF 项（向量 + 关键词）。

示例：

```text
向量路父块排名：A=1，B=2，C=3
关键词路父块排名：B=1，A=2，D=4

score(A) = 1/(60+1) + 1/(60+2) ≈ 0.03252
score(B) = 1/(60+2) + 1/(60+1) ≈ 0.03252
score(C) = 1/(60+3) ≈ 0.01587
score(D) = 1/(60+4) ≈ 0.01563
```

说明：A 和 B 分数相同，需要通过并列打破规则继续排序。示例仅用于展示公式。

### 5.2 排序与并列打破

排序键，依次比较：

1. `score` 降序；
2. `matched_child_count` 降序（命中子块多的父块优先）；
3. `parent_index` 升序（同一文档内保持原文顺序）；
4. `parent_id` 升序（保证结果确定）。

### 5.3 参数 K

- `K=60` 是 RRF 的常用默认值；
- `K` 越大，排名差异被平滑得越明显，头部结果的区分度越低；
- `K` 越小，头部排名优势越明显；
- 上线后通过离线评估调整，调整前必须记录变更。

### 5.4 为什么使用父块级 RRF

直接对子块做 RRF 再折叠到父块，会导致：

- 父块命中子块数量多时，RRF 分数累加可能偏高，产生“数量优势”；
- 同一父块内部的子块分数无法代表父块的整体相关性；
- 最终排序与返回粒度不一致，调参困难。

父块级 RRF：

- 每路先折叠为父块排名，排名语义和最终返回粒度一致；
- 只使用最佳子块排名，避免数量优势；
- 同分时再参考命中子块数量，兼顾“覆盖面”；
- 实现简单，便于测试和解释。

### 5.5 后续扩展

- weighted RRF：向量路和关键词路分别乘以权重；
- rank 归一化后加权求和；
- 上述扩展需要单独评审，本期不实现。

## 6. 父块回查

### 6.1 查询

按 RRF 排序取前 `top_k` 个 `parent_id`，从 MySQL 批量查询：

```sql
SELECT
    id, document_id, content, heading_path, locator,
    page_start, page_end, file_type, token_count, parent_index
FROM document_chunks
WHERE chunk_level = 'PARENT'
  AND id IN (...);
```

要求：

- 使用参数化查询，禁止字符串拼接；
- 单次查询最多 `top_k` 个 ID；
- 查询结果按 RRF 顺序重新排列；
- 附加查询 `documents` 表获取 `document_name` 和最新状态，确认文档仍为 `READY`。

### 6.2 缺失处理

- 父块在 MySQL 中缺失：跳过该父块，`missing_parent_count += 1`，记录 WARN 日志；
- 文档状态不再是 `READY`：跳过该父块，记录 WARN 日志；
- 缺失数量超过阈值（默认 10）时，整个请求返回 503 `RETRIEVAL_MYSQL_UNAVAILABLE` 或标记 `degraded=true`，由上层决定；
- 正常情况下 `missing_parent_count` 应为 0。

## 7. 响应组装

### 7.1 结果字段

每个父块结果包含：

- 父块 `content`：来自 MySQL，完整返回；
- `document_name`、`file_type`、`heading_path`、`locator`、页码；
- `score`：RRF 分数；
- `rrf_score`：RRF 分数（与 `score` 相同，便于 rerank 后区分）；
- `rerank_score`：默认为 `null`；
- `matched_child_count`：命中子块总数；
- `matched_children`：命中子块片段列表。

### 7.2 matched_children 规则

- 默认每个父块最多返回 3 条，由 `RETRIEVAL_MATCHED_CHILDREN_LIMIT` 控制；
- 排序规则：
  1. 两路都命中的子块优先；
  2. 按两个 rank 的较小值升序；
  3. 只有一路命中时，按该路 rank 升序；
- 每个子块的 `content` 截断到 `RETRIEVAL_MATCHED_CHILD_CONTENT_CHARS`（默认 300 字符）；
- 优先使用 ES 的 `highlight`，没有时返回截断后的 `content`；
- `matched_children` 是展示和引用信息，父块正文始终以 MySQL 为准。

### 7.3 响应体大小控制

- `top_k` 最大 50；
- 每个父块正文最大 2000 token；
- 每个父块最多 3 条子块片段，每条不超过 300 字符；
- 预估响应体小于 2MB；
- 如果超过 2MB，按 `top_k` 顺序截断并记录告警。

### 7.4 耗时统计

`timing_ms` 包含：

```json
{
  "ready_documents": 3,
  "embedding": 45,
  "vector": 120,
  "keyword": 80,
  "fusion": 5,
  "backfill": 12,
  "rerank": 0,
  "total": 265
}
```

## 8. 边界情况

| 情况 | 处理 |
| --- | --- |
| 两路都为空 | 返回空结果 `results=[]`，`degraded=false` |
| 只有向量路有结果 | 只用向量路父块排名计算 RRF |
| 只有关键词路有结果 | 只用关键词路父块排名计算 RRF |
| 同一子块在两路出现 | 两路各自保留 rank 和分数，合并到同一个 `matched_child` |
| 同一父块多个子块命中 | 折叠为一个父块，`matched_child_count` 累加 |
| 同一 `parent_id` 对应多个 `document_id` | 数据异常，跳过并告警 |
| 父块在 MySQL 缺失 | 跳过，累加 `missing_parent_count` |
| 文档不再是 READY | 跳过，记录 WARN |
| `top_k` 大于候选父块数 | 返回全部候选父块 |
| 候选父块数为 0 | 返回空结果，不查询 MySQL |

## 9. 降级

融合阶段本身不主动降级；降级由上游检索阶段决定：

- 单路不可用时，融合只处理可用的一路；
- 两路都不可用时，不会进入融合阶段，直接返回 503；
- MySQL 不可用时，融合无法回查父块，返回 503；
- rerank 失败不影响融合结果，直接返回 RRF 顺序。

## 10. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RRF_K` | `60` | RRF 平滑常数 |
| `RETRIEVAL_TOP_K` | `10` | 返回父块数 |
| `RETRIEVAL_MATCHED_CHILDREN_LIMIT` | `3` | 每个父块的命中子块片段数上限 |
| `RETRIEVAL_MATCHED_CHILD_CONTENT_CHARS` | `300` | 子块片段截断长度 |
| `RETRIEVAL_MAX_RESULT_BYTES` | `2097152` | 响应体大小上限（2MB） |
| `RETRIEVAL_PARENT_MISSING_THRESHOLD` | `10` | 父块缺失告警阈值 |

## 11. 测试

### 11.1 单元测试

- 单路子块去重和 rank 重排；
- 父块排名折叠：同一父块多个子块、跨父块交错；
- 父块级 RRF 公式和示例；
- 并列打破顺序；
- 两路合并和 `matched_children` 收集；
- `matched_children` 截断和排序；
- 边界情况表逐项覆盖；
- 排序确定性：同一输入多次计算结果一致。

### 11.2 集成测试

- 构造固定向量命中和关键词命中，验证最终父块顺序；
- 父块正文从 MySQL 回查正确；
- 父块缺失和文档状态变化时跳过并告警；
- 响应字段完整，`timing_ms` 各阶段有值；
- `top_k`、`matched_children_limit` 生效。

### 11.3 故障测试

- 单路为空时融合正确；
- 两路全空时返回空结果；
- 父块缺失超过阈值时的行为；
- 响应体超过上限时的截断行为。

## 12. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-FU-01 | 子块命中按 `parent_id` 聚合，同一父块只返回一个结果 | 单元测试、集成测试 |
| AC-FU-02 | 父块排名由最佳子块排名决定 | 单元测试 |
| AC-FU-03 | 父块级 RRF 公式和并列打破正确 | 单元测试 |
| AC-FU-04 | 父块正文来自 MySQL，`matched_children` 内容正确 | 集成测试 |
| AC-FU-05 | 边界情况表逐项覆盖 | 单元测试 |
| AC-FU-06 | 相同输入多次计算结果确定一致 | 单元测试 |
| AC-FU-07 | 父块缺失时跳过并累加 `missing_parent_count` | 故障注入测试 |
| AC-FU-08 | 响应体不超过 2MB | 接口测试、压力测试 |

## 13. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-FU-01 | 父块排名折叠丢失信息 | 相关性下降 | 保留 `matched_children` 和命中数量，同分时参考覆盖度 |
| R-FU-02 | RRF 参数不合理 | 排序不符合预期 | `K` 可配置，离线评估后调整 |
| R-FU-03 | 父块缺失 | 结果缺失 | 索引阶段完整性校验；运行时跳过并告警，阈值超限返回 503 |
| R-FU-04 | 响应体过大 | 网络和序列化开销 | 限制 `top_k`、片段数和截断长度 |
| R-FU-05 | 并列结果不稳定 | 同一问题结果跳动 | 固定多级排序键，最后按 `parent_id` 升序 |
| R-FU-06 | MySQL 回查成为瓶颈 | 延迟上升 | 批量主键查询、限制 `top_k`、连接池复用 |

## 14. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为融合模块实施的基线版本。
