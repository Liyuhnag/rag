# 异步任务流程图历史草稿

`async-task-flow.png`、`async-task-flow.svg` 和 `async-task-flow.prompt.md` 是早期异步上传方案的设计草稿，保留用于追溯设计过程。

图中使用的 `POST /api/documents`、`processing_tasks` 以及 `PENDING`、`STORING`、`STORED` 等接口、模型和状态属于旧方案，不代表当前实现。

当前任务接口和状态流转请以源码为准：

- [任务接口定义](../../../backend/api/tasks/schemas.py)
- [文档任务模型](../../../backend/model/document_task/model.py)
- [解析任务实现](../../../backend/task/parse_document.py)
