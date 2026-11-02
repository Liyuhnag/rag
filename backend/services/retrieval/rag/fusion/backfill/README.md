# 父块缓存与回填

多轮对话、多个子块命中同一父块时，会重复读取较大的父块正文。这里采用 Redis 共享正文缓存与 MySQL 回源，保留批量查询，避免每个候选独立查库。不增加进程内 LRU：多个 worker 已能共享 Redis 中的热点数据，再增加本地缓存会引入额外内存副本和失效同步成本。

## 部署

三个 URL 必须连接独立 Redis 实例，使用不同 DB 编号不能隔离内存和淘汰策略。项目根目录 compose 已提供本地配置。

| 用途 | 环境变量 | 本地端口 | 容量与策略 |
| --- | --- | --- | --- |
| Celery broker / result | `REDIS_URL` | 6379 | 256 MiB，`noeviction` |
| 父块正文 | `PARENT_CACHE_REDIS_URL` | 6380 | 256 MiB，`allkeys-lfu` |
| Bitmap 布隆快照与分布式锁 | `PARENT_GUARD_REDIS_URL` | 6381 | 128 MiB，`noeviction`，AOF |

正文用普通 `SET` 写入，不设 TTL，不缓存空值；容量由 `maxmemory` 和 LFU 控制。LFU 在内存不足时淘汰低频 key，并非按时间过期。锁有 10 秒租期。布隆快照有 24 小时清理期限，旧版本无需永久占用 guard 内存；快照消失会重建或放行，不能解释为父块不存在。线上容量和 Redis 高可用由平台配置，不在应用内实现 Sentinel。

## 查询流程

1. 对候选去重，限制最多 200 个；按调用方授权的文档版本范围筛选，再从 MySQL 批量读取当前 READY 文档的版本、计数、名称和 `updated_at`。
2. 使用共享 Bitmap 布隆快照过滤确定不存在的父块。不存在、损坏、构建不完整、超出构建上限或 Redis 故障的快照一律放行。
3. `MGET` 批量读取正文。key 包含租户、文档、版本、`updated_at` 和父块 ID；值的身份不符、内容为空、损坏或超限都视为未命中。
4. 对缺失父块逐个 `SET NX PX` 加锁，批量发送；得到锁后再次检查缓存，再将仍缺失的 ID 批量回源。释放锁使用 Lua 比较 token 后删除。
5. 未得锁的父块每 20 毫秒检查缓存并重试加锁，直到完成或达到请求预算；不会绕过仍有效的锁直接回源。Redis 故障时则降级到受并发限制的 MySQL。
6. 返回前再次读取文档状态；删除、取消 READY、版本切换或 `updated_at` 变化的结果不返回。当前名称来自数据库。缓存命中仍会有轻量文档元数据查询，缓存节省的是父块正文查询。

## 布隆快照与更新

不使用进程内可用 ID 集合，也不使用一个依赖各 worker 增量同步的全局位图。每个文档版本及 `updated_at` 对应一个独立快照：首次缺失时只读取该快照的父块 ID，确认当前文档修订标识、唯一 ID 数及文档计数一致后，在内存中构建位图，再通过一次 `SET` 原子发布。

该内存缓冲仅用于构建，不是进程缓存。默认误判率目标为 1%，最多构建 100,000 个 ID；超限放行。并发首次构建可能重复读取 ID，但所有数据库查询都受同一进程预算限制。布隆只减少无效 ID 的正文回源，不代替授权、状态校验或 SQL。

现有 `replace_current_version` 在替换 chunk 的同一事务内更新文档 `updated_at`，重新发布 READY 也更新它，因此同版本同数量替换、相同 ID 正文更新都使用新 key。未来增加或修改 chunk 的写入路径也必须在同一事务内更新此字段；不得绕过该约定直接改表。无需删除 Bloom 中的位，chunk ID 仍使用现有 UUIDv5。

## 故障与并发预算

Redis 连接和读取默认超时 200 毫秒，禁用自动重试；连续 3 次失败熔断 5 秒，随后单探针恢复。正文 Redis 与 guard Redis 独立熔断。过滤器可用性不确定时放行，锁服务失败时以数据库并发预算兜底，不把故障伪装成“没有父块”。

MySQL 每批最多 100 个 ID；同一进程、相同并发配置的所有 store 共享 `BoundedSemaphore`，默认最多 16 个查询，获取许可最多等待 200 毫秒，每次 SQL 使用最多 1 秒的 socket 读写预算。它是应用层信号量，不是 MySQL 内置的全局限流。多进程总上限约为 worker 数 × 16，部署时应结合数据库连接容量调整；生产进程应统一并发配置。

一次回填默认预算 3 秒，超时或并发超限显式失败。同步调用在关键边界检查截止时间，并给新建数据库连接传入超时；DNS、连接建立、分包读取和复用外层事务连接不构成严格的整条 SQL 墙钟截止保证。锁是减少重复回源的机制，故障或租约失效时不承诺严格单次执行。

## 接入和验证

API 进程在 `app.state.parent_backfiller` 复用一个服务，依赖函数为 `get_parent_backfiller`，应用退出时关闭连接池。调用示例：

```python
result = backfiller.backfill(
    fused_candidates,
    allowed_versions=authorized_document_versions,
    tenant_id=trusted_tenant_id,
)
```

`allowed_versions` 必须来自服务端完成授权的范围，不能使用客户端自报值或直接把检索结果当成授权依据。当前没有租户 ACL 子系统，key 作用域隔离不代表已经实现权限控制。方法是同步的，未来在异步路由中使用时需要在线程池调用；本次不新增子块查询父块接口，也不接入尚未完成的聊天流程。

在 backend 目录运行：

```sh
.venv/bin/python -m pytest tests/test_parent_backfill.py tests/test_parent_cache_config.py -q
```

Redis 用例自动启动两套临时隔离实例，通过 Unix socket 访问，结束后清理；没有 `redis-server` 时跳过这些用例，不会连接业务 Redis。
