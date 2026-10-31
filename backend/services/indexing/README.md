# 文档子块索引

入口是 `task/index_document.py` 的 `rag.index_document`，运行在独立的 `index` 队列。本地解析与 MinerU 合并完成后，先将父子块保存到 MySQL，再推进 `INDEXING` 并投递索引。CSV/XLSX 继续走表结构保存。

## 数据流

1. 在 MySQL 文档行锁内复核 `parse_version`，按 `chunk_index` 分页读取当前版本的 CHILD，每批最多 10 条。
2. 首次使用时创建 Milvus collection、HNSW/COSINE 索引及 ES strict mapping。已有集合校验维度、模型标识和必需字段；ES 校验字段类型与分词器。
3. 先把当前 `parse_version` 提交为 `PROCESSING`，再清理该文档当前版本的外部记录。文档行锁里如果状态仍是 `READY`，这次不删除，重新取消发布后再写。向量输入是 `heading_path + "\n" + content`。正文会发送到所配置的 embedding 服务。
4. 共用 `chunk_id`：Milvus upsert 子块向量和父块关联字段，ES bulk index 子块正文与来源字段。父块只在 MySQL；Milvus 不存正文，其他来源字段可回查 MySQL。
5. 写入前检查子块的父块、版本、顺序、页码边界及父块 `child_count`。检查 embedding 数量、维度、有限数值和非零向量，以及 Milvus 写入数量和 ES 每项状态。
6. 刷新后比对 MySQL 子块数与 Milvus/ES 当前版本数量，并抽查首尾子块 ID 和版本；不一致时失败并保留旧版本。
7. 清理旧版本后，将子块 `embedding_model`、旧版本块清理和文档 `READY` 在同一 MySQL 事务提交，最后才完成任务。

## 配置与启动

完整配置在 `backend/.env.example`，实际值写到 `backend/.env`：

| 配置 | 用途 |
| --- | --- |
| `EMBEDDING_BASE_URL`、`DASHSCOPE_API_KEY` | OpenAI 兼容 embedding 地址和密钥。密钥留空时进程仍能启动，第一次 embedding 会失败 |
| `EMBEDDING_MODEL`、`EMBEDDING_DIM` | 默认 `text-embedding-v4`、1024 维 |
| `INDEXING_BATCH_SIZE`、`INDEXING_TIMEOUT_SECONDS` | 默认每批 10 条、单次请求 60 秒；批量限制为 1–10 |
| `MILVUS_URI`、`MILVUS_TOKEN`、`MILVUS_DATABASE`、`MILVUS_COLLECTION` | Milvus 服务、认证、数据库和集合 |
| `ES_URL`、`ES_INDEX` | Elasticsearch 8 服务和索引 |
| `ES_BULK_SIZE` | ES Bulk 每批条数，默认 500；与 embedding 批量独立 |
| `ES_API_KEY` 或 `ES_USERNAME` / `ES_PASSWORD` | ES 认证，API key 优先 |
| `ES_CA_CERTS` | 自签名 TLS 的 CA 文件路径；默认保持证书校验 |
| `ES_ANALYZER`、`ES_SEARCH_ANALYZER` | 默认 `ik_max_word` / `ik_smart`，需安装与 ES 版本匹配的 IK 插件 |

无 IK 的开发实例可将两个分词器都改为 `standard`。已有 mapping 无法原地切换分词器，需更换 `ES_INDEX` 后重建。模型或维度切换需更换 `MILVUS_COLLECTION` 并重新索引；集合 description 保存模型标识，不应手工修改。

从 `backend` 运行 `uv sync --all-groups`、`./start.sh`，脚本包含索引 worker。手动运行可使用 `uv run celery -A main.celery_app worker -Q index --concurrency=2 --loglevel=info`。任务软/硬时限为 29/30 分钟。

## 失败与一致性

Embedding 网络故障、429 和 5xx 最多重试三次，配置错误立即失败。明确的 400 token 长度错误会拆分批次；单条仍超限时拆分文本并合并归一化向量，最多递归 8 层。每次请求记录 token 用量、耗时或 HTTP 失败状态，不记录正文。ES Bulk 使用 `refresh=false`，只重试 429/5xx 的失败条目，全部批次完成后统一刷新。Milvus 与 Elasticsearch 都刷新且数量核对通过后，才更新任务进度和 `result_json` 中的写入数量、最后子块 ID。任一存储失败或一致性检查不通过都不发布 READY，任务记录错误并保留 MySQL 子块。索引任务执行期间每 2 分钟按领取标记续租 `updated_at`。完成和失败只更新仍持有该标记的任务；标记已被新领取换掉时，旧执行不再改任务或文档。失联 INDEXING 任务超过 10 分钟由现有维护任务重置并补投，重新领取时更换标记。恢复领取索引任务时保留已有进度和步骤。重放前先把文档提交为 `PROCESSING`，再从 MySQL 重放；文档在当前索引被清空期间不再保持 `READY`。任务完成标记写不上时只记错误，不把已发布文档改回失败。

双索引与 MySQL 没有分布式事务，失败可能留下部分外部记录；同版本重放先取消 `READY`，再清理并按确定性 ID 重写。检索接入时必须校验 MySQL 的 READY 和当前版本。

`POST /api/tasks/{id}/retry` 已统一支持各可执行阶段：任务与文档均为 `FAILED`，且任务版本仍是文档当前版本时，才按原阶段重新排队。公共 retry 保留产物和索引检查点，只清除错误与执行 token；索引 worker 当前仍从 MySQL 子块完整重放，批次续跑和索引版本隔离尚未接入。投递 Redis 失败时任务保持 PENDING，由维护任务补投。准入矩阵见 [统一重试约定](../../../docs/adr/0002-task-retry-admission.md)。

版本锁覆盖网络调用，会延长事务和文档行锁持有时间，同文档重解析、删除或其他索引会等待。当前选择串行化保证版本安全；无阻塞在线重建需进一步引入发布版本和任务租约。删除文档后的外部孤儿索引回收、检索与混合召回不在本次实现中。

已发布文档可通过维护队列的 `rag.audit_document_index(document_id)` 触发只读巡检。它复核父子块结构、MySQL 数量与双索引当前版本数量，并抽查首尾子块 ID；发现差异时任务失败并记录错误日志，不自动改写或删除数据。

## 验证

测试覆盖双索引重放、局部失败、过期版本、数量/维度校验、SDK schema、ES mapping、退避、版本锁、任务进度及环境配置。使用 HTTP transport/存储替身和真实 SDK 的本地 schema 构造，不替代真实 Milvus、ES、DashScope 和 MySQL 端到端联调。
