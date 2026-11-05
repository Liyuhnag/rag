# 公共任务重试准入与阶段准备

关联 [#23](https://github.com/Liyuhnag/rag/issues/23)。本记录扩展 #22 的范围，取代仅支持失败索引任务的 retry 规则。

## 准入矩阵

| 任务 | 文档 | 版本 | 结果 |
| --- | --- | --- | --- |
| FAILED，且阶段在公共策略表中 | FAILED | task.version = parse_version | PENDING + 原阶段，文档改为 PROCESSING，返回 202 |
| FAILED | 非 FAILED 或文档不存在 | 任意 | 409 TASK_NOT_RETRYABLE |
| FAILED | FAILED | 版本不一致 | 409 TASK_NOT_RETRYABLE |
| SUPERSEDED | 任意 | 任意 | 409 TASK_NOT_RETRYABLE |
| PENDING / RUNNING / COMPLETED | 任意 | 任意 | 409 TASK_NOT_RETRYABLE |
| FAILED + DONE 或无对应阶段策略 | 任意 | 任意 | 409 TASK_NOT_RETRYABLE |
| 任务不存在 | 任意 | 任意 | 404 TASK_NOT_FOUND |

可执行阶段包括 PENDING / PARSING / MERGING / CHUNKING / INDEXING。PENDING 表示尚未开始，领取后进入 PARSING；DONE 不可执行。

## 事务与投递

- 公共入口为 `services/task_runtime/lifecycle.py::retry`，API 路径和响应结构保持不变。
- 先单独锁任务行，再单独锁文档行；锁内检查准入。并发 retry 只有一个请求可以从 FAILED 改为 PENDING。
- 阶段准备、文档 FAILED → PROCESSING、任务 FAILED → PENDING 在同一事务中完成，任意写入异常整体回滚。
- 保留原阶段、进度、产物和全部检查点，只清除任务与文档错误、任务 token，展示步骤改为“等待重试”。
- 提交后按原阶段投递；返回本次排队快照，避免再次读取时采用另一执行者的新阶段。broker 故障保留 PENDING，由维护任务补投。
- retry 不分配新文档版本，也不属于失联 RUNNING 的 recover。

## 分段准备

- PARSING 和 MERGING 重试时，选择本任务、本文件、本版本最近一组具有 token 的分段，与恢复接管使用相同选择规则。
- 只处理 FAILED 分段。没有 ZIP 的失败分段清除旧远端编号和错误，重置 attempts=0，改为 SUBMITTED 并安排立即轮询；新 worker 接管后由轮询触发重新提交。
- 已有 ZIP 的失败分段改为 COMPLETED，保留 ZIP 和远端编号；已完成或仍在提交、解析、下载中的正常分段保持不变。
- 阶段准备不生成新 token；新 worker 领取后才生成执行权，并接管上述分段组。升级前 token 为空的历史分段不接管。
- CHUNKING 和 INDEXING 默认不改写业务检查点；索引 worker 目前仍整版重放，保留检查点不表示已实现批次续跑或索引版本隔离。
