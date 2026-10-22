# Rerank 详细设计

| 项目 | 内容 |
| --- | --- |
| 文档编号 | RAG-RET-040 |
| 文档版本 | V0.9（评审稿） |
| 文档状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 检索阶段——可选 rerank 精排、阿里云调用、分数处理、降级、成本和合规 |

## 修订记录

| 版本 | 日期 | 修订人 | 修订说明 |
| --- | --- | --- | --- |
| V0.9 | 2026-10-19 | 架构组 | 初稿，提交研发团队评审。 |

## 1. 概述

### 1.1 编写目的

本文档定义检索阶段的可选 rerank 精排，包括提供方、模型、候选集、调用流程、分数处理、超时降级、成本和数据合规，作为 rerank 模块开发、测试和验收的依据。

### 1.2 适用范围

本文档适用于：

- RRF 之后的父块候选精排；
- 阿里云 DashScope rerank API 的调用和重试；
- rerank 分数与 RRF 分数的关系；
- rerank 失败时的回退；
- rerank 成本、缓存和合规处理。

本文档不覆盖：

- 向量检索和关键词检索（见 RAG-RET-010、RAG-RET-020）；
- 父块聚合和 RRF 融合（见 RAG-RET-030）；
- 训练或自建 rerank 模型（后续阶段）。

### 1.3 术语与缩略语

| 术语 | 说明 |
| --- | --- |
| Rerank | 对候选文档按 query 相关性进行二次精排。 |
| Cross-Encoder | 同时编码 query 和文档的精排模型架构。 |
| 候选集 | 进入 rerank 的父块列表，默认 RRF top 20。 |
| relevance_score | rerank 模型输出的相关性分数。 |
| 回退 | rerank 失败时使用 RRF 原始顺序。 |

### 1.4 设计依据

- [检索阶段总体设计](./00-overview.md)
- [RRF 融合详细设计](./03-rrf-fusion.md)
- 阿里云 DashScope rerank 官方文档
- [向量化与 Milvus 存储详细设计](../indexing/03-vectorization-milvus.md)（数据合规参考）

### 1.5 设计目标

- 默认关闭，按配置启用；
- 精排粒度是父块，输入是父块正文；
- 只对 RRF top 20 父块调用一次 rerank；
- rerank 分数与 RRF 分数分开保存，不混算；
- rerank 失败或超时不阻塞主链路；
- 成本可控、结果可缓存、数据合规可说明。

## 2. 非目标

- 不做 rerank 模型训练和微调；
- 不做自建 Cross-Encoder 服务（后续可选替换）；
- 不做多路 rerank 分数融合；
- 不做 query 改写和文档摘要后再精排；
- 不做逐句或逐段落的细粒度打分。

## 3. 输入输出

### 3.1 输入

```python
class RerankInput:
    query: str
    candidates: list[FusedParent]   # 默认 RRF top 20
    top_n: int = 10
    request_id: str
```

### 3.2 输出

```python
class RerankOutput:
    ranked: list[FusedParent]       # 重新排序后的父块
    reranked: bool
    rerank_error: str | None
    model: str
    call_latency_ms: int
```

## 4. 提供方与模型

### 4.1 默认提供方

默认使用阿里云 DashScope rerank：

```text
provider = aliyun_dashscope
model = gte-rerank-v2
```

选择理由：

- 与 embedding 使用同一云服务，配置和合规流程统一；
- 中文和英文 rerank 效果满足当前阶段需求；
- 按调用计费，不需要自建 GPU 服务；
- 接口简单，易于降级和替换。

模型最终以联调时 DashScope 提供的可用版本为准；变更模型必须更新本文档和配置。

### 4.2 可替换接口

```python
class RerankProvider:
    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        """返回 (候选下标, relevance_score) 列表，按分数降序。"""
```

约束：

- 调用方只依赖 `RerankProvider` 接口；
- 默认实现是阿里云 DashScope；
- 后续可以替换为自建 bge-reranker 服务，接口保持不变；
- provider 实现内部处理鉴权、超时、重试和错误码映射。

## 5. 调用流程

```text
1. 判断 RETRIEVAL_RERANK_ENABLED 和请求 rerank 参数
   - 两者都为 true 才执行
2. 取 RRF 排序后的前 RETRIEVAL_RERANK_CANDIDATES（默认 20）个父块
   - 候选为空时跳过 rerank
3. 构造文档列表：
   - 每个候选使用父块 content
   - 超过 RERANK_MAX_DOC_CHARS 时截断，并在日志记录
4. 调用 RerankProvider.rerank(query, documents, top_n)
5. 映射返回的下标到父块：
   - 写入 rerank_score
   - 按 rerank 分数降序排序
6. 取前 RETRIEVAL_RERANK_TOP_N（默认 10）个父块
7. 成功：reranked=true，score=rerank_score
   - 保留 rrf_score 供排查和对比
8. 失败：reranked=false，rerank_error 记录原因
   - 返回 RRF 原始顺序，不中断请求
```

调用示例：

```python
# 伪代码
response = dashscope.TextReRank.call(
    model="gte-rerank-v2",
    query=query,
    documents=documents,
    top_n=top_n,
    return_documents=False,
)
results = response.output["results"]
# results 形如 [{"index": 3, "relevance_score": 0.91}, ...]
```

说明：

- 只调用一次 rerank，不逐条调用；
- `documents` 数量不超过 `RETRIEVAL_RERANK_CANDIDATES`；
- `top_n` 不超过候选数量；
- 如果 SDK 或接口要求 `return_documents=true`，只取 `index` 和 `relevance_score`，文档正文仍以 MySQL 为准。

## 6. 分数处理

### 6.1 字段语义

| 字段 | 说明 |
| --- | --- |
| `rrf_score` | 父块级 RRF 分数，始终保留 |
| `rerank_score` | rerank 相关性分数，未执行或失败时为 `null` |
| `score` | 最终排序分数：rerank 成功时为 `rerank_score`，否则为 `rrf_score` |

规则：

- 不把 rerank 分数与 RRF 分数相加或加权平均；
- 不使用 rerank 分数覆盖 `rrf_score`；
- 响应中同时返回两个分数，便于对比和回溯；
- 排序使用稳定排序，rerank 分数相同时保持 RRF 顺序；
- `reranked=true` 时，结果数量取 `RETRIEVAL_RERANK_TOP_N`。

### 6.2 截断

- 父块正文超过 `RERANK_MAX_DOC_CHARS` 时截断；
- 截断策略：保留开头，截断位置对齐到最近的句号或换行；
- 截断后仍在日志中记录原始长度和截断长度；
- 截断不修改 MySQL 中的父块正文。

## 7. 错误处理与降级

| 错误 | code | 可重试 | 处理 |
| --- | --- | --- | --- |
| 鉴权失败 | `RERANK_AUTH_FAILED` | 否 | 记录 `rerank_error`，回退 RRF |
| 限流 | `RERANK_RATE_LIMITED` | 是 | 退避重试 1 次；仍失败回退 RRF |
| 超时 | `RERANK_TIMEOUT` | 是 | 重试 1 次；仍失败回退 RRF |
| 输入超长 | `RERANK_INPUT_TOO_LONG` | 是 | 进一步截断后重试一次；仍失败回退 RRF |
| 返回格式错误 | `RERANK_BAD_RESPONSE` | 否 | 回退 RRF，记录响应摘要 |
| 候选为空 | `RERANK_SKIPPED_EMPTY` | 否 | 跳过 rerank，`reranked=false` |
| 未预期错误 | `RERANK_ERROR` | 否 | 回退 RRF |

降级约定：

- rerank 失败不影响 HTTP 状态码，接口仍返回 200；
- `reranked=false`，`rerank_error` 有值；
- 日志级别 WARN；
- 指标 `retrieval_rerank_total{result="failed"}` 自增；
- 连续失败超过阈值（默认 5 次/分钟）时，可选自动熔断，本次进程内禁用 rerank 5 分钟并告警。

## 8. 性能、成本与缓存

### 8.1 性能

- 候选数默认 20，`top_n` 默认 10；
- 连接超时 5 秒，读取超时 15 秒；
- 全链路增加 rerank 后目标 P95 < 2000ms；
- rerank 与父块回查串行执行，不并发调用多次。

### 8.2 成本

- rerank 按调用计费，启用前需要评估查询量；
- 默认关闭，按需开启；
- 候选数限制为 20，不建议超过 50；
- 可以通过缓存降低重复查询成本。

### 8.3 缓存

缓存键：

```text
sha256(RERANK_MODEL + ":" + query + ":" + concat(parent_id + content_hash))
```

规则：

- 默认 TTL 300 秒；
- 只缓存成功结果；
- 缓存容量 500；
- `document` 内容变化时缓存键变化，不会命中旧结果；
- 敏感场景可以关闭缓存。

## 9. 数据合规

- rerank 会把 query 和父块正文发送到阿里云；
- 合规要求与索引阶段的 embedding 一致；
- 敏感文档场景需要关闭 rerank 或替换为自建 rerank 服务；
- 日志不记录 query 和父块正文，只记录哈希、长度和分数；
- 在产品文档中明确数据出内网范围。

## 10. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RETRIEVAL_RERANK_ENABLED` | `false` | 服务端总开关 |
| `RERANK_MODEL` | `gte-rerank-v2` | 阿里云 rerank 模型 |
| `RETRIEVAL_RERANK_CANDIDATES` | `20` | 进入 rerank 的父块数 |
| `RETRIEVAL_RERANK_TOP_N` | `10` | rerank 后返回父块数 |
| `RERANK_TIMEOUT_SECONDS` | `15` | 读取超时 |
| `RERANK_CONNECT_TIMEOUT_SECONDS` | `5` | 连接超时 |
| `RERANK_MAX_RETRIES` | `1` | 重试次数 |
| `RERANK_MAX_DOC_CHARS` | `4000` | 单父块截断长度 |
| `RETRIEVAL_RERANK_CACHE_TTL_SECONDS` | `300` | 结果缓存 TTL |
| `RERANK_FAILURE_CIRCUIT_THRESHOLD` | `5` | 每分钟失败熔断阈值 |
| `RERANK_CIRCUIT_OPEN_SECONDS` | `300` | 熔断持续时间 |

## 11. 测试

### 11.1 单元测试

- 候选集截断和 top_n 计算；
- 分数映射和稳定排序；
- `score`、`rrf_score`、`rerank_score` 的关系；
- 截断策略（对齐句号/换行）；
- 缓存键计算和命中；
- 错误码映射和回退逻辑；
- 熔断逻辑。

### 11.2 集成测试

- mock rerank provider：验证排序变化和响应字段；
- 真实阿里云调用（测试环境）：正常返回、top_n 生效；
- rerank 分数相同时保持 RRF 顺序；
- 关闭开关时不调用阿里云；
- 缓存命中不重复调用。

### 11.3 故障测试

- 阿里云超时：重试后回退 RRF；
- 429：退避重试后回退；
- 鉴权失败：回退并告警；
- 返回格式错误：回退并记录；
- 连续失败触发熔断。

## 12. 验收标准

| 验收编号 | 验收项 | 验证方式 |
| --- | --- | --- |
| AC-RR-01 | 默认关闭时完全不调用 rerank | 集成测试 |
| AC-RR-02 | 开启后对 RRF top 20 调用一次 rerank，返回 top 10 | 集成测试 |
| AC-RR-03 | `rerank_score` 和 `rrf_score` 分别返回，`score` 语义正确 | 接口测试 |
| AC-RR-04 | rerank 超时或失败时回退 RRF 顺序，接口返回 200 | 故障注入测试 |
| AC-RR-05 | 相同输入缓存命中，不重复调用阿里云 | 单元测试、集成测试 |
| AC-RR-06 | 截断后的文档不超过 `RERANK_MAX_DOC_CHARS` | 单元测试 |
| AC-RR-07 | 连续失败触发熔断并告警 | 故障注入测试 |

## 13. 风险与应对措施

| 风险编号 | 风险描述 | 影响 | 应对措施 |
| --- | --- | --- | --- |
| R-RR-01 | rerank 增加延迟 | 响应变慢 | 默认关闭、限制候选数、超时回退、监控 P95 |
| R-RR-02 | rerank 成本超预期 | 费用上升 | 默认关闭、候选数 20、缓存、按查询量评估 |
| R-RR-03 | rerank 服务限流或不可用 | 功能降级 | 重试 + 回退 RRF + 熔断告警 |
| R-RR-04 | 父块正文发送到阿里云 | 合规风险 | 与 embedding 同一合规策略，敏感场景切换自建服务 |
| R-RR-05 | rerank 分数与 RRF 分数混淆 | 排序和排查困难 | 字段分离，文档和接口明确 `score` 语义 |
| R-RR-06 | 候选截断丢失关键信息 | 精排质量下降 | 优先保留开头，按句子截断，监控截断比例 |

## 14. 评审记录

| 评审日期 | 评审版本 | 评审人 | 评审意见 | 处理结果 |
| --- | --- | --- | --- | --- |
|  | V0.9 | 研发团队 |  |  |

评审通过后，本文档版本升级为 V1.0，并作为 rerank 模块实施的基线版本。
