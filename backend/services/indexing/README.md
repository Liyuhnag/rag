# 文档子块索引

入口是 `task/index_document.py` 的 `rag.index_document`，运行在独立的 `index` 队列。本地解析与 MinerU 合并完成后，先将父子块保存到 MySQL，再推进 `INDEXING` 并投递索引。CSV/XLSX 继续走表结构保存。

## 数据流

1. 在 MySQL 文档行锁内复核 `parse_version`，按 `chunk_index` 分页读取当前版本的 CHILD，每批最多 10 条。
2. 首次使用时创建 Milvus collection、HNSW/COSINE 索引及 ES strict mapping。已有集合校验维度、模型标识和必需字段；ES 校验字段类型与分词器。
3. 清理该文档当前版本的外部记录，用 `heading_path + "\n" + content` 调用 embedding。正文会发送到所配置的 embedding 服务。
4. 共用 `chunk_id`：Milvus upsert 子块向量和父块关联字段，ES bulk index 子块正文与来源字段。父块只在 MySQL；Milvus 不存正文，其他来源字段可回查 MySQL。
5. 检查 embedding 数量、维度、有限数值和非零向量，检查 Milvus 写入数量和 ES 每项状态。刷新成功后，只清理该文档更早版本的索引。
6. 将子块 `embedding_model`、旧版本块清理和文档 `READY` 在同一 MySQL 事务提交，最后才完成任务。

## 配置与启动

完整配置在 `backend/.env.example`，实际值写到 `backend/.env`：

| 配置 | 用途 |
| --- | --- |
| `EMBEDDING_BASE_URL`、`DASHSCOPE_API_KEY` | OpenAI 兼容 embedding 地址和密钥 |
| `EMBEDDING_MODEL`、`EMBEDDING_DIM` | 默认 `text-embedding-v4`、1024 维 |
| `INDEXING_BATCH_SIZE`、`INDEXING_TIMEOUT_SECONDS` | 默认每批 10 条、单次请求 60 秒；批量限制为 1–10 |
| `MILVUS_URI`、`MILVUS_TOKEN`、`MILVUS_DATABASE`、`MILVUS_COLLECTION` | Milvus 服务、认证、数据库和集合 |
| `ES_URL`、`ES_INDEX` | Elasticsearch 8 服务和索引 |
| `ES_API_KEY` 或 `ES_USERNAME` / `ES_PASSWORD` | ES 认证，API key 优先 |
| `ES_CA_CERTS` | 自签名 TLS 的 CA 文件路径；默认保持证书校验 |
| `ES_ANALYZER`、`ES_SEARCH_ANALYZER` | 默认 `ik_max_word` / `ik_smart`，需安装与 ES 版本匹配的 IK 插件 |

无 IK 的开发实例可将两个分词器都改为 `standard`。已有 mapping 无法原地切换分词器，需更换 `ES_INDEX` 后重建。模型或维度切换需更换 `MILVUS_COLLECTION` 并重新索引；集合 description 保存模型标识，不应手工修改。

从 `backend` 运行 `uv sync --all-groups`、`./start.sh`，脚本包含索引 worker。手动运行可使用 `uv run celery -A main.celery_app worker -Q index --concurrency=2 --loglevel=info`。任务软/硬时限为 29/30 分钟。

## 失败与一致性

Embedding 网络故障、429 和 5xx 最多重试三次，配置错误立即失败。任一存储失败都不发布 READY，任务记录错误并保留 MySQL 子块。失联 INDEXING 任务超过 10 分钟由现有维护任务重置并补投，恢复时直接从 MySQL 重放；每个成功批次更新任务时间。

双索引与 MySQL 没有分布式事务，失败可能留下部分外部记录；同版本重放先清理并按确定性 ID 重写。检索接入时必须校验 MySQL 的 READY 和当前版本。已正常记录为 FAILED 的任务不属于失联恢复，需要显式重新安排；本次未增加重新索引 API。

版本锁覆盖网络调用，会延长事务和文档行锁持有时间，同文档重解析、删除或其他索引会等待。当前选择串行化保证版本安全；无阻塞在线重建需进一步引入发布版本和任务租约。删除文档后的外部孤儿索引回收、检索与混合召回不在本次实现中。

## 验证

测试覆盖双索引重放、局部失败、过期版本、数量/维度校验、SDK schema、ES mapping、退避、版本锁、任务进度及环境配置。使用 HTTP transport/存储替身和真实 SDK 的本地 schema 构造，不替代真实 Milvus、ES、DashScope 和 MySQL 端到端联调。
