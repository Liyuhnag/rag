# 解析、合并与切分的租约写入门禁

状态：已实现，关联 [#22](https://github.com/Liyuhnag/rag/issues/22)。

## 问题

文档 version 判断产物属于哪个业务版本；lease_token 判断当前哪个领取者有权执行同一版本。
只在任务 complete/fail 时检查 token，无法阻止旧 worker 先覆盖文档、切分结果或对象存储产物。

## 决定

- claim 生成本次 token，回读结果必须仍匹配本次生成的值。
- complete、fail、进度更新、合并抢占和索引交接必须携带 token。
- 本地解析、MinerU 查询/提交/下载、合并期间，TaskLease 每 120 秒续租。
  续租不成功或抛出数据库异常时锁存失效，后续业务写入停止，不能补写失败状态。
- TaskLease.writes 在同一事务中锁定任务行、核验 RUNNING/token/stage，再执行文档及业务写入。
  通过 ContextVar 让嵌套 ORM 复用该连接，外层统一提交或回滚；其他线程保持独立连接。
  业务事务持有行锁时心跳跳过续租，提交前刷新 updated_at，避免恢复者在事务提交后立即误抢。
  切分和表结构存储原本就通过事务锁住文档并消费结果流，此处增加任务行门禁。
- 对象存储的解析、合并、图片、分段清单和 ZIP 使用 attempts/{token} 路径。
  ZIP 每次下载再分配独立文件名。失效期间已发送的外部请求可能完成，但不能覆盖新领取者的产物，
  也不能再发布数据库指针；遗留孤立对象按存储生命周期清理。
- parse_jobs 持久化提交时的 token，同版本的新领取可创建新分段组。
  轮询、失败处理、重新提交、下载和合并始终使用分段保存的 token，合并只读取同 token 分段。
  等待 MinerU 时没有常驻本地 worker；每次有效轮询续租，远程等待恢复策略仍由 #23 定义。
- INDEXING 继续使用既有索引 worker 的续租逻辑；交接仅在 token 更新成功后投递索引队列。

写入门禁保护的是原子发布：已经开始并持锁的事务完成前，其他领取者无法替换 token。
租约失效按正常停止处理，不应把新领取者的任务或文档标成失败。

## 部署与迁移

1. 排空在途 PARSING / MERGING / CHUNKING（含等待 MinerU 的分段），停止旧 worker 和轮询调度。
2. 对已有库执行 backend/db/migrations/0003_parse_job_lease.sql，再启动新版本 worker/调度。
   新库直接使用 schema.sql。
3. 历史 parse_jobs.lease_token 保持 NULL，不使用当前任务的 token 回填未知批次。
   新代码忽略这些历史回调；如果无法排空，应先单独制定在途任务的停用与重新执行方案。

#22 的范围不扩大 recover 阶段，不修改 retry API 或重试次数策略。

## 后续变更：#23 的统一 recover

- recover 扩展到 PARSING / MERGING / CHUNKING / INDEXING，空闲阈值读取公共阶段策略表。
  当前 PARSING 为 900 秒，其余三个阶段为 600 秒；有效 MinerU 轮询会续租。
- 一次查询按各阶段阈值筛选 RUNNING 任务，再逐条条件更新。更新时核对扫描到的
  stage、updated_at 和 token，避免覆盖扫描后发生的续租、交接或重新领取。
- 恢复只将任务置为 PENDING、清除 token；保留阶段、进度、产物与检查点，不修改文档。
  数据库更新提交后按原阶段投递，投递失败由 PENDING 补投兜底。
- retry 准入矩阵见后续 [统一重试约定](0002-task-retry-admission.md)；维护调度周期调整仍属于后续增量。

## 验证

常规测试覆盖心跳失效锁存、解析/上传/切分前失去执行权、晚到 MinerU 结果及对象路径隔离。

在 backend 目录运行真实 MySQL 集成测试：

```sh
RAG_TEST_MYSQL=1 .venv/bin/python -m pytest tests/test_task_lease_mysql.py -q
```

测试读取 .env 的 MySQL 配置，在同一库创建随机前缀的隔离表并在结束后删除，
不会操作业务表。覆盖并发领取、旧 token 收尾拒绝、事务共享与回滚、恢复等待行锁、
跨线程连接隔离以及分段按原 token 领取。
