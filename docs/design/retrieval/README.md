# RAG 文档检索阶段设计文档集（第二部分）

| 项目 | 内容 |
| --- | --- |
| 文档集编号 | RAG-RET |
| 文档集版本 | V0.9（评审稿） |
| 文档集状态 | 评审中 |
| 密级 | 内部 |
| 编制 | 架构组 |
| 评审 | 研发团队 |
| 批准 | 项目负责人 |
| 编制日期 | 2026-10-19 |
| 适用范围 | 检索阶段——查询向量化、Milvus 向量检索、Elasticsearch 关键词检索、父块聚合、RRF 融合与 rerank |

## 1. 文档集说明

本目录是 RAG 文档检索阶段的正式设计文档集，覆盖从用户问题到候选父块列表的完整链路：

```text
query
  -> 查询向量化
  -> Milvus 向量检索（子块）
  -> Elasticsearch BM25 检索（子块）
  -> 按 parent_id 聚合、RRF 融合
  -> 从 MySQL 回查父块正文
  -> 可选 rerank（父块）
  -> 返回父块列表
```

索引阶段（RAG-IDX）负责把文档写入 MySQL、Elasticsearch 和 Milvus；本阶段只负责检索，不修改索引数据。检索阶段的输入依赖索引阶段产出的：

- MySQL `document_chunks`：父块正文、子块正文、层级关系；
- Elasticsearch `document_chunks`：子块关键词索引；
- Milvus `document_chunks`：子块向量索引。

本阶段不包含最终答案生成、对话 UI、查询改写、多轮对话和用户权限体系。这些内容在后续阶段单独设计。

## 2. 文档清单

| 文档编号 | 文档名称 | 版本 | 状态 | 主要内容 |
| --- | --- | --- | --- | --- |
| RAG-RET-000 | [检索阶段总体设计](./00-overview.md) | V0.9 | 评审中 | 总体架构、检索流程、接口契约、过滤与降级、性能目标、验收与风险 |
| RAG-RET-010 | [向量检索详细设计](./01-vector-retrieval.md) | V0.9 | 评审中 | 查询向量化、Milvus 检索参数、过滤表达式、结果结构、错误处理 |
| RAG-RET-020 | [Elasticsearch 检索详细设计](./02-es-retrieval.md) | V0.9 | 评审中 | 查询预处理、BM25 DSL、高亮、过滤、结果结构、错误处理 |
| RAG-RET-030 | [RRF 融合详细设计](./03-rrf-fusion.md) | V0.9 | 评审中 | 父块聚合、父块级 RRF、去重、top-k、父块回查、响应组装 |
| RAG-RET-040 | [Rerank 详细设计](./04-rerank.md) | V0.9 | 评审中 | 阿里云 rerank、候选集、超时与降级、分数处理、成本与合规 |

## 3. 阅读顺序

建议按以下顺序阅读和评审：

1. 先读总体设计，确认检索阶段边界、接口契约和降级策略；
2. 再读向量检索和 Elasticsearch 检索，确认两路召回的输入输出一致；
3. 然后读 RRF 融合，确认父块聚合、排名折叠和 top-k 规则；
4. 最后读 rerank，确认可选的精排流程、成本和降级方式。

## 4. 评审与变更流程

1. 文档编制完成后提交研发团队评审；
2. 评审意见记录在各文档的“评审记录”章节；
3. 编制人根据评审意见修订文档，更新修订记录和版本号；
4. 评审通过后版本升级为 V1.0，作为实施基线；
5. 实施过程中如需变更接口、字段或降级策略，必须同步更新受影响的文档，并在修订记录中说明原因。

## 5. 适用范围与约束

- 检索单元是子块，最终返回单元是父块；
- 向量检索和关键词检索的候选数默认各 50，最终返回父块默认 10；
- 父块正文只存 MySQL，ES/Milvus 只索引子块；
- 只检索 `documents.status = READY` 的文档；
- embedding 使用阿里云 DashScope `text-embedding-v4`，默认 1024 维，必须与索引阶段一致；
- rerank 默认关闭，开启后使用阿里云 DashScope rerank，失败时回退 RRF；
- 当前系统没有用户认证和租户隔离，检索接口不得直接暴露公网；
- 查询问题和父块正文在开启 embedding/rerank 时会发送到阿里云，按索引阶段的合规要求处理。

## 6. 相关文档

- 全流程分享版：[../end-to-end-pipeline.md](../end-to-end-pipeline.md)
- 生成阶段设计文档集：[../generation/README.md](../generation/README.md)
- 索引阶段设计文档集：[../indexing/README.md](../indexing/README.md)
- 索引阶段总体设计：[../indexing/00-overview.md](../indexing/00-overview.md)
- 向量化与 Milvus 存储详细设计：[../indexing/03-vectorization-milvus.md](../indexing/03-vectorization-milvus.md)
- Elasticsearch 索引详细设计：[../indexing/04-elasticsearch-indexing.md](../indexing/04-elasticsearch-indexing.md)
- 项目文档入口：[docs/README.md](../../README.md)
