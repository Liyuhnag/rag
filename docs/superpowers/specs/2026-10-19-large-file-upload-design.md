# 大文件上传设计

- 日期：2026-10-19
- 状态：待评审
- 所属阶段：索引阶段 1/4
- 上游：无
- 下游：[文档解析设计](./2026-10-19-document-parsing-design.md)

## 1. 目标

把当前“20MB、全量读内存、单请求上传”的链路替换为：

- 单文件最大约 300MB；
- 8MiB 分片上传；
- 支持暂停、失败重试、刷新页面后重新选择同一文件继续上传；
- 分片直接写入 SeaweedFS 的 S3 Multipart Upload；
- 完成后在对象存储中得到一个完整原始文件对象；
- 上传不占 API 进程内存，不依赖本地临时文件。

## 2. 非目标

- 不做浏览器直传对象存储（预签名 URL 方案）；
- 不做跨设备续传，续传只在同一浏览器、同一文件指纹下有效；
- 不做用户认证和多租户上传隔离；
- 不做上传文件内容审核和病毒扫描；
- 不保留永久分片对象。

## 3. 当前实现与问题

当前上传接口：

```text
POST /api/documents
  -> read_upload() 全量读入内存
  -> 写 /tmp/rag-uploads/{task_id}
  -> Celery worker 再 read_bytes()
  -> 单次 PUT 到 SeaweedFS
```

问题：

- 300MB 文件会在 API 和 worker 中产生多份完整内存副本；
- 没有真实上传进度；
- 网络中断后只能重传整个文件；
- 刷新页面后无法恢复；
- 前端上传队列的“进度”是轮询任务状态，不是传输进度。

## 4. 总体方案

使用 S3 Multipart Upload：

```text
POST /api/uploads/init
  -> 创建 upload_sessions 记录
  -> CreateMultipartUpload
  -> 返回 upload_id、chunk_size、total_parts、已上传分片

PUT /api/uploads/{upload_id}/parts/{part_number}
  -> 校验分片大小和范围
  -> UploadPart
  -> 返回 ETag

GET /api/uploads/{upload_id}
  -> 查询会话
  -> ListParts
  -> 返回已上传分片

POST /api/uploads/{upload_id}/complete
  -> 校验分片完整性
  -> CompleteMultipartUpload
  -> 创建 documents + document_tasks
  -> 投递 parse 任务

DELETE /api/uploads/{upload_id}
  -> AbortMultipartUpload
  -> 标记 ABORTED
```

`CompleteMultipartUpload` 由 SeaweedFS 完成合并，应用层拿到的就是一个完整对象。下载、预览、后续解析都按单对象处理，不需要应用层合并分片。

## 5. 为什么不用 Redis bitmap

Redis bitmap 只能表示“第 N 片是否上传过”，不能保存：

- 分片 ETag，`CompleteMultipartUpload` 必需；
- 分片大小；
- 上传会话状态；
- 对象 key 和 S3 upload ID。

而且 S3 `ListParts` 本身就是分片状态的权威来源，再维护 bitmap 会形成两套状态。

最终分工：

| 数据 | 存储 |
| --- | --- |
| 上传会话元数据 | MySQL `upload_sessions` |
| 已上传分片和 ETag | SeaweedFS `ListParts` |
| 完成后的原始文件 | SeaweedFS 单对象 |
| 并发锁、限流、MinerU 信号量 | Redis |

Redis 只做辅助，不作为上传状态的唯一真相。

## 6. 数据模型

### 6.1 `upload_sessions`

```sql
CREATE TABLE IF NOT EXISTS upload_sessions (
    id VARCHAR(32) NOT NULL COMMENT '上传会话 ID',
    document_id VARCHAR(32) NOT NULL COMMENT '预生成的文档 ID',
    fingerprint CHAR(64) NOT NULL COMMENT '文件指纹 SHA-256',
    filename VARCHAR(255) NOT NULL COMMENT '规范化后的文件名',
    file_type VARCHAR(32) NOT NULL COMMENT '扩展名',
    size_bytes BIGINT NOT NULL COMMENT '文件总字节数',
    chunk_size INT NOT NULL COMMENT '分片大小',
    total_parts INT NOT NULL COMMENT '分片总数',
    object_key VARCHAR(768) NOT NULL COMMENT '最终对象 key',
    s3_upload_id VARCHAR(1024) NOT NULL COMMENT 'S3 Multipart Upload ID',
    status VARCHAR(32) NOT NULL COMMENT 'INITIATED/UPLOADING/COMPLETED/ABORTED/EXPIRED',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    expires_at DATETIME(6) NOT NULL COMMENT '过期时间，UTC',
    PRIMARY KEY (id),
    KEY idx_upload_sessions_fingerprint (fingerprint, status),
    KEY idx_upload_sessions_expires (expires_at),
    KEY idx_upload_sessions_document (document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 6.2 `documents`

在上传完成时写入，沿用现有表，新增以下字段：

```sql
ALTER TABLE documents
    ADD COLUMN page_count INT NOT NULL DEFAULT 0 COMMENT 'PDF 页数，非 PDF 为 0',
    ADD COLUMN parse_version INT NOT NULL DEFAULT 0 COMMENT '解析版本',
    ADD COLUMN parsed_md_key VARCHAR(768) NULL COMMENT '合并后的 Markdown key',
    ADD COLUMN parsed_json_key VARCHAR(768) NULL COMMENT '合并后的 JSON key',
    ADD COLUMN source_sha256 CHAR(64) NULL COMMENT '原始文件 SHA-256，可选',
    ADD COLUMN error_message VARCHAR(1024) NULL COMMENT '失败原因';
```

文档状态扩展为：

```text
UPLOADED / PARSING / CHUNKING / INDEXING / READY / FAILED
```

### 6.3 `document_tasks`

上传完成时创建，供后续解析和索引阶段更新：

```sql
CREATE TABLE IF NOT EXISTS document_tasks (
    id VARCHAR(32) NOT NULL COMMENT '任务 ID',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    version INT NOT NULL DEFAULT 1 COMMENT '解析版本，重新解析时递增',
    status VARCHAR(32) NOT NULL COMMENT 'PENDING/RUNNING/COMPLETED/FAILED',
    stage VARCHAR(32) NOT NULL COMMENT 'PENDING/SPLITTING/PARSING/MERGING/CHUNKING/INDEXING/DONE',
    progress INT NOT NULL DEFAULT 0 COMMENT '0-100',
    step VARCHAR(255) NOT NULL DEFAULT '' COMMENT '当前步骤描述',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    result_json JSON NULL COMMENT '阶段检查点和结果',
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_document_tasks_document (document_id),
    KEY idx_document_tasks_status_updated (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 6.4 旧 `processing_tasks`

旧表是上传时创建、带 `temp_path` 的任务模型，与新链路不再匹配。开发阶段直接删除旧表，代码统一使用 `document_tasks`。现有测试中的任务仓库需要同步改造。

## 7. API 契约

统一错误格式沿用现有：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不合法"
  }
}
```

### 7.1 初始化上传

```http
POST /api/uploads/init
Content-Type: application/json
```

请求：

```json
{
  "filename": "年度报告.pdf",
  "size_bytes": 314572800,
  "file_type": "pdf",
  "fingerprint": "9f2c...64位十六进制",
  "chunk_size": 8388608
}
```

响应：

```json
{
  "upload_id": "0f3a...",
  "document_id": "a1b2...",
  "chunk_size": 8388608,
  "total_parts": 38,
  "uploaded_parts": [1, 2, 3],
  "status": "UPLOADING",
  "expires_at": "2026-10-20T12:00:00Z"
}
```

规则：

- `filename` 使用现有 `normalize_filename` 清洗；
- `file_type` 必须在 `ALLOWED_FILE_TYPES` 中；
- `size_bytes` 必须大于 0 且不超过 `MAX_DOCUMENT_BYTES`；
- `fingerprint` 必须是 64 位十六进制；
- `chunk_size` 可以忽略，后端固定使用 `UPLOAD_CHUNK_BYTES`；
- `total_parts = ceil(size_bytes / chunk_size)`，必须不超过 10000；
- `document_id` 在 init 阶段预生成，最终对象 key 为：
  `documents/{document_id}/source/{filename}`；
- 如果存在相同 `fingerprint` 且 `status in (INITIATED, UPLOADING)` 的会话，直接返回该会话和 `ListParts` 结果；
- 如果相同 fingerprint 的会话已 `COMPLETED`，返回已有 `document_id`、`task_id` 和 `COMPLETED` 状态；
- 如果 fingerprint 相同但文件名/大小不同，返回 `409 FINGERPRINT_CONFLICT`，避免误续传；
- `expires_at = now + UPLOAD_SESSION_TTL_HOURS`。

### 7.2 上传分片

```http
PUT /api/uploads/{upload_id}/parts/{part_number}
Content-Type: application/octet-stream
Content-Length: 8388608
```

响应：

```json
{
  "part_number": 1,
  "etag": "\"7d8f...\"",
  "size_bytes": 8388608
}
```

规则：

- `part_number` 从 1 开始，最大 `total_parts`；
- 非最后一片必须正好等于 `chunk_size`；
- 最后一片必须等于 `size_bytes - (total_parts - 1) * chunk_size`；
- `Content-Length` 必须存在且与分片大小一致；
- 超过限制返回 `413`；
- 分片大小不符返回 `422`；
- 同一分片重复上传直接覆盖，天然幂等；
- 上传成功后只返回 ETag，不把 ETag 写入 MySQL，后续以 `ListParts` 为准；
- 分片请求超时建议 60 秒，可由前端按分片重试。

### 7.3 查询上传状态

```http
GET /api/uploads/{upload_id}
```

响应：

```json
{
  "upload_id": "0f3a...",
  "document_id": "a1b2...",
  "filename": "年度报告.pdf",
  "size_bytes": 314572800,
  "chunk_size": 8388608,
  "total_parts": 38,
  "status": "UPLOADING",
  "uploaded_parts": [
    {"part_number": 1, "etag": "\"7d8f...\"", "size_bytes": 8388608},
    {"part_number": 2, "etag": "\"8e9a...\"", "size_bytes": 8388608}
  ],
  "expires_at": "2026-10-20T12:00:00Z"
}
```

`uploaded_parts` 来自 `ListParts`，按 `part_number` 升序。分片数量超过 1000 时分页查询，本期 300MB/8MiB 最多 38 片，一次返回即可。

`POST /api/uploads/init` 返回的 `uploaded_parts` 是分片编号数组，只用于让前端跳过已上传分片；ETag 和分片大小通过 `GET /api/uploads/{upload_id}` 获取。

### 7.4 完成上传

```http
POST /api/uploads/{upload_id}/complete
```

请求体可以为空。

响应：

```json
{
  "upload_id": "0f3a...",
  "document_id": "a1b2...",
  "task_id": "c3d4...",
  "status": "PENDING"
}
```

规则：

- 校验会话存在且未过期；
- 调用 `ListParts`，确认所有 `1..total_parts` 都存在；
- 缺失分片返回 `409 UPLOAD_INCOMPLETE`，响应中带 `missing_parts`；
- 校验每个分片大小；
- 调用 `CompleteMultipartUpload`，提交按 `part_number` 排序的 `{PartNumber, ETag}`；
- 在数据库事务中：
  - 更新 `upload_sessions.status = COMPLETED`；
  - 插入 `documents`，状态 `UPLOADED`，`parse_version = 1`；
  - 插入 `document_tasks`，状态 `PENDING`，阶段 `PENDING`，版本 `1`；
- 提交事务后投递 `parse_document` 到 `parse` 队列；
- 如果会话已经是 `COMPLETED`，直接返回已有 `document_id` 和 `task_id`，不重复创建；
- 使用 Redis 锁 `upload:{upload_id}:complete` 防止并发重复完成，锁 TTL 30 秒。

### 7.5 取消上传

```http
DELETE /api/uploads/{upload_id}
```

行为：

- 调用 `AbortMultipartUpload`；
- 会话标记 `ABORTED`；
- 如果已经 `COMPLETED`，返回 `409 UPLOAD_ALREADY_COMPLETED`，不删除已入库文档；
- 如果会话不存在，返回 `404`。

### 7.6 兼容旧接口

保留 `POST /api/documents` 作为小文件兼容入口：

- 仅接受 `size <= 20MiB`；
- 内部转换为单分片上传，或者直接 `PutObject`；
- 创建 `documents` 和 `document_tasks`，投递解析任务；
- 前端新代码不再使用该接口；
- 现有测试可以逐步迁移到 `/api/uploads`。

## 8. S3 Multipart 实现细节

### 8.1 客户端

使用 `boto3` 替代当前手写 SigV4 客户端：

```python
S3ObjectStorage(
    endpoint_url=settings.s3_endpoint,
    region_name=settings.s3_region,
    aws_access_key_id=settings.s3_access_key_id,
    aws_secret_access_key=settings.s3_secret_access_key,
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        retries={"max_attempts": 3, "mode": "standard"},
    ),
)
```

原因：

- Multipart Upload 的签名、ETag、分页 `ListParts` 用 boto3 更可靠；
- 预签名、流式上传、重试策略不需要自己实现；
- 当前手写客户端只支持整对象 PUT/DELETE。

### 8.2 对象 key

```text
documents/{document_id}/source/{normalized_filename}
```

- `document_id` 在 init 阶段生成；
- 文件名已经过路径穿越清洗；
- 对象 key 最长 768 字符，由校验层保证；
- 解析阶段的分段 PDF 使用：
  `documents/{document_id}/parse/tmp/part-{n}.pdf`；
- 解析结果使用：
  `documents/{document_id}/parsed/merged.md`、
  `documents/{document_id}/parsed/merged.json`。

### 8.3 分片规则

- 分片大小固定 8MiB；
- S3 要求除最后一片外每片至少 5MiB，8MiB 满足；
- 最大 10000 片，8MiB × 10000 = 78GiB，远大于 300MB；
- 300MB 文件约 38 片；
- 3 个并发分片上传，前端可配置 1-5。

### 8.4 完成后的对象

`CompleteMultipartUpload` 之后：

- 对象存储中只有一个完整对象；
- 下载、预览、解析都按单对象读取；
- 分片 part 不再作为普通对象存在，不能单独下载；
- 如果后续要“分块存储原始文件”，属于另一个需求，不在本期范围。

## 9. 断点续传

### 9.1 文件指纹

前端计算：

```text
sha256(
  name + "\0" +
  size + "\0" +
  lastModified + "\0" +
  first_1MiB + "\0" +
  last_1MiB
)
```

规则：

- 文件小于 2MiB 时直接对完整内容做 SHA-256；
- 否则读取首尾各 1MiB，使用 `File.slice()` 和 `crypto.subtle.digest`；
- 输出 64 位十六进制；
- `lastModified` 使用 `File.lastModified`，毫秒时间戳；
- 指纹只用于匹配续传，不作为安全校验。

### 9.2 前端持久化

在 `localStorage` 保存：

```json
{
  "fingerprint": "9f2c...",
  "upload_id": "0f3a...",
  "filename": "年度报告.pdf",
  "size": 314572800,
  "lastModified": 1760000000000,
  "updatedAt": "2026-10-19T12:00:00Z"
}
```

key 建议：

```text
rag.upload.{fingerprint}
```

同时维护一个任务列表：

```text
rag.upload.tasks -> [{fingerprint, uploadId, filename, size, lastModified}]
```

刷新页面后：

- 文件对象本身无法恢复；
- 页面显示“待继续上传”的任务；
- 用户重新选择同一文件后，前端计算指纹；
- 如果匹配到 localStorage 记录，调用 `POST /api/uploads/init`；
- 后端返回已有会话和 `uploaded_parts`，前端跳过已上传分片。

### 9.3 文件变化处理

- 文件名、大小、`lastModified` 任一变化，指纹不同，视为新文件；
- 指纹相同但后端记录的文件名/大小不同，返回 `409`；
- 会话超过 24 小时后，`init` 返回新会话，前端清理旧记录；
- 用户取消上传时，删除 localStorage 记录。

## 10. 前端实现

### 10.1 上传状态

复用并扩展现有 `useUploadManager`：

```text
PENDING      等待上传
UPLOADING    分片上传中
PAUSED       用户暂停
PROCESSING   上传完成，等待解析/索引
SUCCESS      文档 READY
ERROR        上传或处理失败
```

任务字段新增：

```text
uploadId
documentId
taskId
partSize
totalParts
uploadedParts
partProgress
fingerprint
stage
step
```

### 10.2 上传流程

1. `addUploadTask(file)`；
2. 计算指纹；
3. 调用 `init`，拿到 `upload_id`、`chunk_size`、`total_parts`、`uploaded_parts`；
4. 计算缺失分片；
5. 使用 3 个并发上传缺失分片；
6. 每个分片用 `XMLHttpRequest` 或 `fetch` + `AbortController`，记录 `loaded`；
7. 总分片进度：

   ```text
   progress = sum(part.loaded for all parts) / file.size * 100
   ```

8. 全部分片成功后调用 `complete`；
9. 轮询 `GET /api/tasks/{task_id}`，展示解析、切块、索引阶段；
10. 文档 `READY` 后刷新文档列表。

### 10.3 分片上传实现

```text
file.slice(offset, offset + chunkSize)
  -> PUT /api/uploads/{id}/parts/{n}
  -> 读取响应 ETag
```

要求：

- 分片请求不设置全局 5 分钟超时，按分片设置 60 秒；
- 分片失败重试 3 次，退避 1s/2s/4s；
- 429/5xx 可重试，4xx（除 408/429）不重试；
- 暂停时 `AbortController.abort()` 中止在途请求，已上传分片保留；
- 恢复时重新 `init`/`GET` 查询 `uploaded_parts`，只传缺失分片；
- 取消时调用 `DELETE`，清理 localStorage。

### 10.4 任务轮询

现有 `getTaskStatus` 每 2 秒轮询，扩展响应：

```json
{
  "id": "c3d4...",
  "status": "RUNNING",
  "stage": "PARSING",
  "progress": 42,
  "step": "已解析 5/12 段",
  "document_id": "a1b2...",
  "error_message": null,
  "result": {}
}
```

前端阶段文案：

| stage | 文案 |
| --- | --- |
| `SPLITTING` | 正在拆分文件 |
| `PARSING` | 正在解析文档 |
| `MERGING` | 正在合并解析结果 |
| `CHUNKING` | 正在切分文本 |
| `INDEXING` | 正在建立索引 |
| `DONE` | 处理完成 |

`READY` 后停止轮询；`FAILED` 后展示错误并允许重试。

### 10.5 CORS

当前后端 `allow_methods` 只有 GET/POST/DELETE/OPTIONS，需要加入 `PUT`，否则分片上传会被浏览器预检拦截。

## 11. 校验与安全

- 文件名：复用 `normalize_filename`，拒绝空名、路径穿越、NUL、超过 255 字符；
- 扩展名：复用 `ALLOWED_FILE_TYPES`；
- 大小：`0 < size <= MAX_DOCUMENT_BYTES`；
- 分片编号：`1 <= part_number <= total_parts`；
- 分片大小：严格匹配预期值；
- `Content-Length`：必须存在，且与分片大小一致；
- 对象 key：由后端生成，不接受客户端传入；
- 上传会话：ID 为 32 位十六进制，不可猜测；
- 当前没有鉴权：接口不要直接暴露公网；生产前补认证，并把上传会话绑定到用户；
- 完成后可做 magic bytes 校验，拒绝扩展名和内容不符的文件；
- 加密 PDF、损坏文件在解析阶段返回明确错误。

## 12. 错误码

| HTTP | code | 说明 | 可重试 |
| --- | --- | --- | --- |
| 400 | `INVALID_FILENAME` | 文件名非法 | 否 |
| 400 | `INVALID_FINGERPRINT` | 指纹格式错误 | 否 |
| 404 | `UPLOAD_NOT_FOUND` | 会话不存在 | 否 |
| 409 | `FINGERPRINT_CONFLICT` | 指纹相同但文件信息不同 | 否 |
| 409 | `UPLOAD_INCOMPLETE` | 分片未传完 | 是，补传 |
| 409 | `UPLOAD_ALREADY_COMPLETED` | 会话已完成 | 否 |
| 410 | `UPLOAD_EXPIRED` | 会话过期 | 否，重新 init |
| 413 | `FILE_TOO_LARGE` | 超过 300MB | 否 |
| 413 | `PART_TOO_LARGE` | 分片超过预期 | 否 |
| 415 | `UNSUPPORTED_FILE_TYPE` | 扩展名不支持 | 否 |
| 422 | `INVALID_PART_SIZE` | 分片大小不符 | 否 |
| 422 | `INVALID_PART_NUMBER` | 分片编号越界 | 否 |
| 429 | `TOO_MANY_REQUESTS` | 限流 | 是 |
| 502 | `STORAGE_ERROR` | 对象存储失败 | 是 |
| 500 | `INTERNAL_ERROR` | 未预期错误 | 视情况 |

## 13. 清理

Celery beat 每小时执行：

1. 查询 `upload_sessions`：
   - `status in (INITIATED, UPLOADING)`；
   - `expires_at < now()`；
2. 对每个会话调用 `AbortMultipartUpload`；
3. 标记 `EXPIRED`；
4. 删除对应的 localStorage 记录由前端在下次 init 时处理。

每天执行：

- 清理 `parse/tmp/` 下超过 7 天的分段 PDF；
- 清理没有关联文档的孤儿原始对象（可选，按对象前缀扫描）；
- 统计未完成 Multipart Upload，避免存储泄漏。

## 14. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_DOCUMENT_BYTES` | `314572800` | 单文件上限 |
| `UPLOAD_CHUNK_BYTES` | `8388608` | 分片大小 |
| `UPLOAD_CONCURRENCY` | `3` | 前端并发数默认值 |
| `UPLOAD_SESSION_TTL_HOURS` | `24` | 会话有效期 |
| `UPLOAD_PART_TIMEOUT_SECONDS` | `60` | 单分片请求超时 |
| `S3_ENDPOINT` | 无 | SeaweedFS S3 地址 |
| `S3_BUCKET` | 无 | 桶名 |

保留旧变量 `MAX_UPLOAD_BYTES` 的读取兼容，但新代码使用 `MAX_DOCUMENT_BYTES`。

## 15. 测试

### 15.1 单元测试

- `total_parts` 和最后一片大小计算；
- 分片编号、大小、`Content-Length` 校验；
- 指纹生成（小文件、普通文件、首尾读取）；
- 会话过期和 fingerprint 冲突；
- `complete` 缺少分片；
- 重复 `complete` 幂等；
- 取消已完成会话。

### 15.2 集成测试

使用真实 SeaweedFS 或支持 multipart 的测试替身：

- `CreateMultipartUpload`；
- `UploadPart`；
- `ListParts`；
- `CompleteMultipartUpload`；
- `AbortMultipartUpload`；
- 完成后对象内容与原始文件一致；
- 分片乱序上传、重复上传、补传。

### 15.3 前端测试

- 8MiB 切片；
- 3 并发；
- 暂停/恢复；
- 刷新后重新选择同一文件；
- 分片失败重试；
- 进度计算；
- 取消清理。

### 15.4 手工测试

- 300MB PDF 上传；
- 上传到 50% 刷新页面，重新选择文件续传；
- 上传中断网，恢复后重试；
- 重复点击完成；
- 会话过期后重新 init。

## 16. 验收标准

- 300MB 文件通过 8MiB 分片上传成功；
- 刷新页面后重新选择同一文件可以跳过已上传分片；
- 上传完成后对象存储只有一个完整对象；
- API 进程内存不随文件大小线性增长；
- 分片失败可以单独重试；
- 取消上传后 Multipart Upload 被 abort；
- 过期会话被定时清理；
- 旧 `POST /api/documents` 兼容入口仍可用于小文件；
- 前端真实显示上传进度，并在上传完成后切换到解析/索引进度。

## 17. 风险

| 风险 | 应对 |
| --- | --- |
| SeaweedFS Multipart 兼容性未知 | 实现前用 boto3 做完整冒烟测试；不通过则退回“分片落本地后合并上传” |
| boto3 与 SeaweedFS 的 path-style/签名差异 | 固定 `addressing_style=path`、`signature_version=s3v4`，用真实环境验证 |
| 300MB 文件分片数较多 | 8MiB 分片，38 片，前端 3 并发，可接受 |
| 浏览器指纹计算耗时 | 只读首尾 1MiB；Web Crypto 异步计算，不阻塞 UI |
| 无鉴权 | 生产前补认证和上传会话 owner 字段 |
