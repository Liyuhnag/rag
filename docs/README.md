# 项目文档

此目录用于存放项目相关文档：

- 技术方案设计
- API 接口文档
- 架构设计文档
- 部署文档

## 设计文档

### 全流程（分享版）

- [从文件到答案：RAG 全流程](./design/end-to-end-pipeline.md)

### 索引阶段（第一部分）

- [设计文档集说明](./design/indexing/README.md)
- [索引阶段总体设计](./design/indexing/00-overview.md)
- [大文件上传详细设计](./design/indexing/01-large-file-upload.md)
- [文档解析详细设计](./design/indexing/02-document-parsing.md)
- [向量化与 Milvus 存储详细设计](./design/indexing/03-vectorization-milvus.md)
- [Elasticsearch 索引详细设计](./design/indexing/04-elasticsearch-indexing.md)

### 检索阶段（第二部分）

- [设计文档集说明](./design/retrieval/README.md)
- [检索阶段总体设计](./design/retrieval/00-overview.md)
- [向量检索详细设计](./design/retrieval/01-vector-retrieval.md)
- [Elasticsearch 检索详细设计](./design/retrieval/02-es-retrieval.md)
- [RRF 融合详细设计](./design/retrieval/03-rrf-fusion.md)
- [Rerank 详细设计](./design/retrieval/04-rerank.md)
