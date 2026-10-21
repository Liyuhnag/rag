# 大文件上传方案 V2

| 项目 | 内容 |
| --- | --- |
| 文档版本 | V1.0（设计基线） |
| 文档状态 | 讨论完成，待实现 |
| 编制日期 | 2026-10-20 |
| 所在分支 | `feat/large-file-upload-v2` |
| 当前范围 | 上传链路、分片大小、API 与 S3 的职责边界、会话状态机、初始化与断点续传、文档身份与数据模型、接口契约、完成与失败补偿、并发与限流、前端交互、测试与验收、安全边界 |
| 后续范围 | 实现、联调、部署（不在本文范围） |

## 0. 文档说明

- 本文是这次大文件上传方案的**唯一载体**，不继承旧版上传设计。
- 每轮讨论确认的结论写入「已确认决策」；还没有定下来的内容放入「待定项」，不提前写成结论。
- 当前完成第一部分（链路与分片）、第二部分（会话状态机）、第三部分（初始化与断点续传）、第四部分（文档身份与数据模型）、第五部分（接口契约）、第六部分（完成与失败补偿）、第七部分（并发与限流）、第八部分（前端交互）、第九部分（测试与验收）、第十部分（安全边界）。所有议题已讨论完成。

## 1. 背景与目标

当前上传链路是「整个文件读入内存 → 写本地临时文件 → worker 再读一遍 → 单次 PUT 到对象存储」，不适合 1GB 级文件，也没有真实进度和断点续传。

本方案的目标：

- 单文件上限约 **1000MB**；
- 支持约 **10 人同时上传**；
- 浏览器按分片上传，后端把分片写入 S3，最终由 S3 合并为一个完整对象；
- 支持暂停、失败重试、刷新页面后重新选择同一文件继续上传；
- API 进程不保存完整文件，不占与文件大小线性相关的内存；
- 上传完成后进入异步解析流程（解析本身不在本文范围）。

本阶段非目标：

- 不做浏览器直传对象存储（不使用预签名 URL）；
- 不在 API 本地拼一份完整临时文件再上传；
- 不做跨设备续传；
- 不在本阶段展开鉴权、多租户和权限模型。

## 2. 约束与假设

| 项目 | 结论 | 说明 |
| --- | --- | --- |
| 单文件上限 | 约 1000MB | 默认按 1000MiB 配置，具体值见第 7 节 |
| 并发上传 | 约 10 人 | 按设计峰值考虑 |
| 使用环境 | 内网 | 浏览器、API、S3 都在内网 |
| 浏览器可达 S3 | 是 | 但本方案不采用浏览器直传 |
| 部署形态 | API 与 S3 目标分离 | 当前可能同机；代码不依赖同机部署 |
| 技术栈 | FastAPI + boto3 + SeaweedFS S3 + MySQL + Redis/Celery + Vue | 沿用现有栈 |
| 鉴权 | 当前无 | 内网使用；本阶段不展开，后续单独设计 |

## 3. 已确认决策

### D1. 上传链路：浏览器 → API → S3，API 代理数据面

**结论**：浏览器只和 API 通信；API 接收分片后调用 S3 `UploadPart`，由 S3 完成分片存储和最终合并。不使用浏览器直传和预签名 URL。

**理由**：

- 鉴权、文件类型、分片大小、限流、审计都集中在 API，边界简单；
- S3 不需要暴露给浏览器，不需要处理 S3 侧 CORS、预签名有效期和 HTTPS/mixed content；
- 当前规模（约 10 人、内网）下，API 代理的带宽和内存可以接受。

**代价**：

- API 成为数据通道，入站和出站各一份流量；
- API 需要控制同时在途的分片数，避免带宽、内存和线程被打满；
- 后续如果并发或文件规模明显增长，需要水平扩展 API 或重新评估直传。

### D2. API 不保存完整临时文件，分片直接转发 S3

**结论**：每个分片在 API 侧只做短暂缓冲（不超过一个分片），随即调用 S3 `UploadPart`，成功后立即释放；不在本地拼接完整文件，也不把完整文件写到磁盘。

**理由**：

- 10 人 × 1GB 如果落完整临时文件，就是 10GB 级本地磁盘，还要处理清理、磁盘满和多实例共享；
- 分片状态保存在 S3，API 不需要靠本地文件恢复；
- API 重启或换实例后，仍可通过 S3 `ListParts` 恢复上传进度。

**说明**：

- 缓冲优先用内存；如果实现上需要落盘，只允许单分片的临时缓冲（如 `SpooledTemporaryFile`），不能是完整文件；
- API 的业务状态不放在进程内：会话在 MySQL，分片在 S3；进程内只保留并发信号量、连接池等运行时资源。

### D3. 分片大小 8MiB，单分片超时 120 秒

**结论**：固定分片大小 `8MiB = 8,388,608` 字节；最后一片按实际剩余大小；单分片请求超时 120 秒。

**依据**：

- S3 要求除最后一片外每片不小于 5MiB，8MiB 满足；最多 10000 片，1GB 远低于上限；
- 1000MB 文件约 120–125 片；
- 10 个在途分片约 80MiB 缓冲，给 boto3、请求解析和其他开销留有余量；
- 失败只重传 8MiB，用户等待短；
- 8MiB 在 10Mbps 链路上约 6.7 秒，120 秒超时对慢网也留足余量。

**备选**：

- 16MiB：片数减半、请求数更少，但 10 个在途分片约 160MiB，慢网下单分片耗时翻倍；
- 32MiB：不推荐，慢网下容易贴近超时，失败重传成本也更高。

### D4. 目标部署：API 与 S3 分离

**结论**：方案按「API 与 S3 分离部署」设计；当前同机只是部署特例，不作为代码假设。

**说明**：

- `S3_ENDPOINT` 配置化：同机时可用回环/内网地址，分离后换成内网域名或内网 IP，代码不变；
- API 不落完整临时文件，业务状态不依赖本机磁盘，具备水平扩展基础；
- 分离后 API 的出口带宽是真实成本：10 人 × 1GB = 10GB 出站，千兆网卡满速约 80 秒；实际会摊在几分钟内，但仍需要限制同时在途分片数；
- 多实例部署时，全局并发限制需要共享机制（如 Redis 信号量）；单实例可先用进程内信号量。具体方案待定。

### D5. 上传会话状态机

**结论**：7 个状态（`UPLOADING`、`COMPLETING`、`COMPLETED`、`ABORTING`、`ABORTED`、`EXPIRED`、`FAILED`）；用 MySQL 条件更新做跨实例互斥，不引入 Redis 状态锁；`COMPLETING` / `ABORTING` 租约 5 分钟；可恢复失败回退 `UPLOADING`，不可恢复才置 `FAILED`。详见第 4 节。

### D6. 初始化与断点续传

**结论**：用全文件 SHA-256 作为文件指纹；`init` 按指纹复用未过期的 `UPLOADING` 会话，`COMPLETED` 直接返回已有结果；唯一活跃会话用生成列 + 唯一索引；并发 `init` 用「建 MPU → INSERT 冲突 → abort 自己的 MPU → 返回已有会话」；会话 TTL 24 小时，`init` 复用时续期；前端 `localStorage` 只存元数据，已传分片以 S3 `ListParts` 为准。详见第 5 节。

### D7. 文档身份与数据模型

**结论**：`init` 生成 `upload_id` 和 `document_id`；对象键为 `documents/{document_id}/source/{filename}`；`complete` 事务写 `documents` + `document_tasks(version=1)` + 会话状态；worker 只更新、不插入；`task_id` 与 `document_id` 解耦，`document_tasks` 用 `(document_id, version)` 做幂等键。表结构见第 6 节。

### D8. 接口契约

**结论**：5 个 `/api/uploads/*` 端点；HTTP JSON 用 camelCase，数据库/Python 用 snake_case；`GET` 对终态返回 200 并用 `status` 区分，`part` / `complete` 对过期/取消返回 410；`uploadedParts` 统一为 `{partNumber,sizeBytes}` 对象数组；legacy `POST /api/documents` 删除，上传统一走 `/api/uploads/*`。详见第 7 节。

### D9. 完成与失败补偿

**结论**：`complete` 事务提交后投递解析任务；投递失败不标 `FAILED`，任务保持 `PENDING`，由每 5 分钟运行的 `redispatch_pending_tasks` 补投；worker 用 `PENDING → RUNNING` 条件更新做幂等。清理任务覆盖过期会话、僵死 `ABORTING`、僵死 `COMPLETING`、孤儿 MPU、孤儿对象和旧会话，清理任务放独立 `maintenance` 队列。详见第 8 节。

### D10. 并发与限流

**结论**：v1 单 API 进程，全局 **20** 个在途分片；前端每文件 **2** 个并发；等待 30 秒拿不到槽位返回 `429 UPLOAD_BUSY`；v1 不在 `init` 做队列拦截，`QUEUE_FULL` / `BROKER_UNAVAILABLE` 保留；nginx/uvicorn 参数见第 9 节。详见第 9 节。

### D11. 前端交互

**结论**：v1 一次只传一个文件，每文件 2 个分片，其他文件排队；9 个前端状态；XHR 真实进度；支持暂停/恢复/取消；`complete` 客户端超时 180 秒，超时后先查状态；`GET /api/tasks/{taskId}` 轮询解析进度；`rag.upload.v2` 和 `rag.processing.v2` 两个 localStorage key。详见第 10 节。

### D12. 测试与验收

**结论**：测试分单元、集成、故障注入、压测、前端五层；用 12 条验收标准收口；SeaweedFS 3.99 的 Multipart / `ListMultipartUploads` 能力先做前置验证。详见第 11 节。

### D13. 安全边界

**结论**：本阶段不做认证、鉴权和多租户；前提是仅内网部署、不暴露公网，接受内网匿名访问的风险。S3 不暴露给浏览器，文件名、类型、大小和分片由服务端校验，上传 ID 随机不可猜。后续要公网暴露时，必须补鉴权、owner、配额和审计。详见第 12 节。

## 4. 上传会话状态机（Q1）

### 4.1 状态集合

| 状态 | 含义 | 允许的操作 | 终态 |
| --- | --- | --- | --- |
| `UPLOADING` | 正常接收分片 | 上传分片、complete、abort | 否 |
| `COMPLETING` | 已抢占“完成”，正在校验、合并、写库 | 查询、幂等 complete | 否 |
| `COMPLETED` | 合并并落库成功 | 幂等返回 | 是 |
| `ABORTING` | 已抢占“取消/过期清理”，正在 AbortMultipartUpload | 查询、重试 abort | 否 |
| `ABORTED` | 用户主动取消完成 | — | 是 |
| `EXPIRED` | 过期清理完成 | — | 是 |
| `FAILED` | 不可恢复，需要重新上传 | — | 是 |

### 4.2 迁移规则

```text
init
  -> UPLOADING

UPLOADING --complete 抢占--> COMPLETING
COMPLETING --成功--> COMPLETED
COMPLETING --可恢复失败（确认 S3 未合并）--> UPLOADING
COMPLETING --不可恢复失败--> FAILED

UPLOADING --abort 抢占（USER）--> ABORTING
UPLOADING --过期抢占（EXPIRED）--> ABORTING
ABORTING --abort 成功（USER）--> ABORTED
ABORTING --abort 成功（EXPIRED）--> EXPIRED

COMPLETING / ABORTING --租约过期--> 允许另一个请求条件接管
```

**init**：直接创建 `UPLOADING`，不设 `INITIATED`。

**上传分片**：

- 只允许 `UPLOADING` 且 `expires_at > now()`；
- 其他状态分别返回“已完成 / 正在完成 / 已取消 / 已过期”；
- 分片上传本身不写 MySQL。

**complete**：

1. 条件抢占：
   ```sql
   UPDATE upload_sessions
   SET status = 'COMPLETING',
       claim_version = claim_version + 1,
       updated_at = UTC_TIMESTAMP(6)
   WHERE id = ?
     AND status = 'UPLOADING'
     AND expires_at > UTC_TIMESTAMP(6)
   ```
2. 抢占成功（1 行）：`ListParts` 校验 → `CompleteMultipartUpload` → 事务写 documents/tasks + `status='COMPLETED'`。
3. 抢占失败（0 行）：读取当前状态：
   - `COMPLETED`：幂等返回已有 document/task；
   - `COMPLETING` 且租约未过期：返回“正在完成”，客户端稍后查询；
   - `COMPLETING` 且租约已过期：条件接管，进入恢复流程；
   - `ABORTING` / `ABORTED` / `EXPIRED` / `FAILED`：拒绝。
4. 可恢复失败（S3 5xx、缺分片，且确认 S3 未合并）：回退 `UPLOADING`，让用户补传或重试。
5. 不可恢复失败（MPU 不存在且对象也不存在，或对象大小不符）：置 `FAILED`，要求重新上传。

**abort**：

- 条件更新 `UPLOADING → ABORTING`，`abort_reason='USER'`；
- 抢占成功后调用 `AbortMultipartUpload`，成功置 `ABORTED`；
- 已经是 `ABORTED` / `EXPIRED`：幂等返回；
- `COMPLETING`：拒绝，避免和 S3 合并对撞。

**过期清理**：

- 条件更新 `UPLOADING AND expires_at <= now() → ABORTING`，`abort_reason='EXPIRED'`；
- `AbortMultipartUpload` 成功后置 `EXPIRED`；
- 清理任务也认租约，避免和用户 abort 重复执行。

**租约**：

- `COMPLETING` / `ABORTING` 用 `updated_at` 做租约，默认 5 分钟；
- 租约过期后允许另一个请求条件接管；
- 1GB/125 片的合并通常秒级，5 分钟是兜底；如果实测合并可能超过 5 分钟，再增加心跳续租。

### 4.3 并发与恢复

**互斥方式**：所有状态迁移都用 MySQL 条件更新，依赖单行原子性，不依赖 Redis 锁。这样多实例部署时也能正确互斥，也不会出现 Redis 锁过期但业务仍在执行的问题。

**租约与 fencing**：接管 `COMPLETING` 时用 `claim_version` 递增；最终写库的 `UPDATE` 带上 `status='COMPLETING' AND claim_version=?`，不匹配就回滚，避免旧执行者在新执行者接管后继续双写。

**S3 已合并、DB 未写成功**：

- 保持 `COMPLETING`，不回退 `UPLOADING`；
- 接管时先 `HeadObject(object_key)`：
  - 对象存在且大小等于预期：跳过合并，直接补写 documents/tasks；
  - 对象不存在：重新 `ListParts` + `CompleteMultipartUpload`；
  - MPU 已不存在且对象也不存在：置 `FAILED`；
  - S3 暂时不可用：保持 `COMPLETING`，返回可重试错误。

**complete 与最后几个分片撞车**：

- 前端规则：发 complete 前必须等所有分片请求结束；
- 服务端在 `CompleteMultipartUpload` 收到 `InvalidPart` 时，重新 `ListParts` 再试一次；
- 仍失败则回退 `UPLOADING`，让客户端重试；
- `COMPLETING` 状态下分片接口直接拒绝。

**重复 complete**：`COMPLETED` 幂等返回；`COMPLETING` 租约有效时返回“进行中”；租约过期才允许接管。

### 4.4 状态机涉及的数据字段

以下字段在 Q3 数据模型中最终定稿：

- `status`：上表 7 个状态；
- `abort_reason`：`USER` / `EXPIRED`；
- `claim_version`：整数，用于接管 fencing；
- `updated_at`：复用为租约时间；
- `error_message`：失败原因；
- `completed_at`：完成时间（可选）。

## 5. 初始化与断点续传（Q2）

### 5.1 文件指纹

**结论**：客户端计算**全文件 SHA-256**，作为文件指纹，64 位小写十六进制。

规则：

- 在 Web Worker 中流式计算，不阻塞 UI；
- 用户选文件后先算指纹，再调 `init`；1GB 大约 1–3 秒，视设备而定，前端显示“正在校验文件…”；
- 指纹只算文件内容，不算文件名、大小和 `lastModified`；`init` 复用会话时再校验 `filename`、`size_bytes`；
- 不采用快速指纹（文件名 + 大小 + mtime + 首尾各 1MiB），因为中间内容变化但首尾和 mtime 不变时会碰撞，可能把两个文件的分片拼进同一个对象；
- 指纹只用于匹配续传会话，不作为安全凭证；服务端仍会校验分片大小和最终对象大小。

### 5.2 init 的会话复用

请求字段（最终字段名和 HTTP 状态码在 Q4 定稿）：

```json
{
  "filename": "年度报告.pdf",
  "sizeBytes": 1048576000,
  "fingerprint": "9f2c...64位小写十六进制"
}
```

服务端根据 `filename` 推导文件类型，不信任客户端传入的类型；`chunkSize` 固定为服务端配置，不接受客户端指定。

判断顺序：

```text
init(F, filename, size)

1. 有 fingerprint=F 且 status=UPLOADING 的会话？
   ├─ 未过期 + filename/size 一致 -> ListParts -> 200 复用
   ├─ 未过期 + filename/size 不一致 -> 409 FINGERPRINT_CONFLICT
   └─ 已过期 -> 标记 ABORTING(EXPIRED)，继续往下走

2. 有 fingerprint=F 且 status=COMPLETING 的会话？
   -> 202 “正在完成”，不新建

3. 有 fingerprint=F 且 status=COMPLETED 的会话？
   ├─ 对应文档记录仍存在 -> 200 已完成，返回已有文档/任务（字段见 Q3/Q4）
   └─ 文档已删除 -> 允许新建会话

4. 有 fingerprint=F 且 status=ABORTING 的会话？
   -> 202/409 “正在取消”，让前端稍后重试

5. 其他终态（ABORTED/EXPIRED/FAILED）或没有会话
   -> 新建
```

补充规则：

- `ListParts` 返回已上传的**分片编号**和大小，不把 ETag 暴露给浏览器；
- `ListParts` 暂时失败（S3 5xx/超时）返回可重试错误，不新建会话；
- `ListParts` 返回 `NoSuchUpload`：说明 MPU 已丢，标记该会话 `FAILED` 后再允许新建；
- `COMPLETED` 直接返回已有结果，是为了避免“服务端已完成但响应丢失，用户刷新后又把 1GB 传一遍”；
- `forceNew`（强制重新上传）本阶段不做，后续有明确需求再加。

### 5.3 唯一活跃会话与并发 init

同一个 fingerprint 最多只能有一个 `UPLOADING` 或 `COMPLETING` 会话。建议在 `upload_sessions` 上增加生成列和唯一索引：

```sql
active_fingerprint CHAR(64) GENERATED ALWAYS AS (
    CASE
        WHEN status IN ('UPLOADING', 'COMPLETING') THEN fingerprint
        ELSE NULL
    END
) STORED,
UNIQUE KEY uk_upload_sessions_active_fingerprint (active_fingerprint)
```

- 进入 `ABORTING` 或终态后，`active_fingerprint` 变成 `NULL`，唯一索引允许多个 `NULL`；
- `COMPLETING` 也占着槽位，避免“正在合并时又新建一个上传”；
- 过期的 `UPLOADING` 必须先迁到 `ABORTING(EXPIRED)`，才能新建。

并发 `init` 的处理：

1. 先查活跃会话；有就复用；
2. 没有则创建 S3 MPU；
3. `INSERT` 会话；
4. 如果唯一索引冲突：
   - `AbortMultipartUpload` 删掉自己刚建的 MPU（忽略 `NoSuchUpload`）；
   - 重新查询成功者的会话；
   - `ListParts` 后返回给前端；
5. 其他写入失败：也 abort 自己的 MPU，返回 503；
6. 创建 MPU 成功但 `INSERT` 前进程崩溃：留下孤儿 MPU，交给 Q5 的清理任务。

### 5.4 会话有效期

- 新建会话：`expires_at = now + 24h`；
- 每次成功复用 `UPLOADING` 会话：把 `expires_at` 续到 `now + 24h`；
- `GET` 状态查询不续期，避免轮询频繁写库；
- 过期的 `UPLOADING` 由清理任务或下一次 `init` 迁到 `ABORTING(EXPIRED)`，再 abort S3；
- TTL 配置项 `UPLOAD_SESSION_TTL_HOURS`，默认 24。

### 5.5 刷新后的恢复流程

`localStorage` 使用一个 key：`rag.upload.v2`，存任务数组：

```json
[
  {
    "fingerprint": "9f2c...",
    "uploadId": "0f3a...",
    "filename": "年度报告.pdf",
    "sizeBytes": 1048576000,
    "lastModified": 1760000000000,
    "createdAt": "2026-10-20T08:00:00Z",
    "updatedAt": "2026-10-20T08:10:00Z"
  }
]
```

原则：

- 只存元数据，不存分片列表和 ETag；
- `fingerprint` 用于匹配文件，`uploadId` 用于用户还没重新选文件时查询状态；
- 多标签页通过 `storage` 事件同步列表。

刷新后、重新选文件前：

1. 读取任务数组；
2. 对每条记录调 `GET /api/uploads/{uploadId}`；
3. 按返回更新：
   - `UPLOADING`：显示“待继续上传”和已传片数；
   - `COMPLETING`：显示“正在处理”，轮询；
   - `COMPLETED`：显示“已完成”，从待继续列表移除；
   - `ABORTED` / `EXPIRED` / 404：移除记录；
   - 网络错误：保留记录，显示“暂时无法确认”，不直接删。

重新选文件后：

1. 在 Web Worker 里计算全文件 SHA-256，显示“正在校验文件…”；
2. 用指纹在本地列表里找同一条记录，找到就提示“继续上传”；
3. 调 `init(fingerprint, filename, sizeBytes)`；
4. 根据返回处理：

| init 返回 | 前端动作 |
| --- | --- |
| `200` + `UPLOADING` + `uploadedParts` | 跳过已传分片，只传缺失的 |
| `200` + `COMPLETED` | 提示“已上传”，清理本地记录，刷新文档列表 |
| `202` + `COMPLETING` | 不传分片，轮询状态 |
| `201` + 新会话 | 从第 1 片开始传 |
| `409` 指纹冲突 | 提示文件名/大小与旧会话不一致，让用户选择取消旧任务或换文件 |
| `410` 已过期 | 删除本地记录，按新上传处理 |

清理 `localStorage` 的时机：

- `complete` 成功返回后；
- 用户主动取消且服务端确认后；
- 服务端返回 `COMPLETED`、`EXPIRED`、`ABORTED`、`404` 时；
- 不要在 `complete` 还没成功时清，否则又会出现重复上传。

边界情况：

- **localStorage 被清空**：不影响续传的正确性；重新选文件算指纹，服务端仍能匹配未过期会话，只是页面不会提前显示“待继续”。
- **文件被改过**：全文件 SHA-256 不同，服务端不会匹配旧会话；旧记录还在时，前端提示“同名文件已变化，旧任务可取消”。
- **多个标签页同时传同一个文件**：服务端唯一活跃会话返回同一个 `uploadId`；分片重复上传是覆盖，`complete` 由状态机保证幂等；前端用 `storage` 事件同步。
- **刷新时还有分片在途**：可能已经成功也可能没有；`ListParts` 里有就跳过，没有就重传。
- **localStorage 不可用**：上传本身不受影响，只是刷新后不会提前显示待继续任务；重新选文件仍可续传。

## 6. 数据模型（Q3）

### 6.1 文档身份与落库责任

规则：

1. `init` 生成 `upload_id` 和 `document_id`；
2. 对象键从第一天起就是 `documents/{document_id}/source/{filename}`；
3. `upload_sessions.document_id` 唯一，一个会话只对应一份文档；
4. `complete` 在一个事务里写 `documents`、写 `document_tasks`（`version=1`，`task_id` 此时生成）、把会话置 `COMPLETED`；
5. worker 只更新 `documents.status` 和 `document_tasks`，不插入 `documents`；
6. 重新解析时 `document_id` 不变，`document_tasks.version` 递增，`(document_id, version)` 唯一；
7. legacy `POST /api/documents` 已删除，文档列表/详情/删除接口保留。

理由：

- 下游 `parse_jobs`、chunk ID、ES/Milvus 都需要稳定的 `document_id`；
- 对象键、解析产物、chunk 元数据统一在 `documents/{document_id}/...` 下；
- `complete` 用已知主键做幂等插入，重试逻辑简单；
- 会话中止只浪费一个 32 位 ID，成本可以忽略。

### 6.2 `upload_sessions`

```sql
CREATE TABLE IF NOT EXISTS upload_sessions (
    id VARCHAR(32) NOT NULL COMMENT '上传会话 ID（upload_id）',
    document_id VARCHAR(32) NOT NULL COMMENT '预生成的文档 ID',
    fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT '全文件 SHA-256',
    active_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (
            CASE
                WHEN status IN ('UPLOADING', 'COMPLETING') THEN fingerprint
                ELSE NULL
            END
        ) STORED COMMENT '活跃会话指纹，非活跃为 NULL',
    filename VARCHAR(255) NOT NULL COMMENT '规范化后的文件名',
    file_type VARCHAR(32) NOT NULL COMMENT '扩展名',
    size_bytes BIGINT NOT NULL COMMENT '文件总字节数',
    chunk_size INT NOT NULL COMMENT '分片大小，固定 8388608',
    total_parts INT NOT NULL COMMENT '分片总数',
    object_key VARCHAR(1024) NOT NULL COMMENT '最终对象 key',
    s3_upload_id VARCHAR(1024) NOT NULL COMMENT 'S3 Multipart Upload ID',
    status VARCHAR(32) NOT NULL
        COMMENT 'UPLOADING/COMPLETING/COMPLETED/ABORTING/ABORTED/EXPIRED/FAILED',
    abort_reason VARCHAR(16) NULL COMMENT 'USER/EXPIRED',
    claim_version INT NOT NULL DEFAULT 0 COMMENT '接管 fencing',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC，复用为租约时间',
    expires_at DATETIME(6) NOT NULL COMMENT '过期时间，UTC',
    completed_at DATETIME(6) NULL COMMENT '完成时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_upload_sessions_document (document_id),
    UNIQUE KEY uk_upload_sessions_active_fingerprint (active_fingerprint),
    KEY idx_upload_sessions_fingerprint (fingerprint, created_at),
    KEY idx_upload_sessions_status_expires (status, expires_at),
    KEY idx_upload_sessions_status_updated (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 6.3 `documents`

```sql
CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    name VARCHAR(255) NOT NULL COMMENT '原始文件名',
    file_type VARCHAR(32) NOT NULL COMMENT '扩展名',
    size_bytes BIGINT NOT NULL COMMENT '文件字节数',
    status VARCHAR(32) NOT NULL
        COMMENT 'UPLOADED/PARSING/CHUNKING/INDEXING/READY/FAILED',
    object_key VARCHAR(1024) NOT NULL COMMENT '对象存储 key',
    file_url VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '原始文件链接',
    previewable TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否可预览',
    preview_url VARCHAR(2048) NULL COMMENT '预览链接',
    chunk_count INT NOT NULL DEFAULT 0 COMMENT '子块数量',
    parent_chunk_count INT NOT NULL DEFAULT 0 COMMENT '父块数量',
    page_count INT NOT NULL DEFAULT 0 COMMENT 'PDF 页数，非 PDF 为 0',
    parse_version INT NOT NULL DEFAULT 1 COMMENT '当前解析版本',
    parsed_md_key VARCHAR(1024) NULL COMMENT '合并 Markdown key',
    parsed_json_key VARCHAR(1024) NULL COMMENT '合并 JSON key',
    source_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT '原始文件 SHA-256',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    uploaded_at DATETIME(6) NOT NULL COMMENT '上传完成时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    PRIMARY KEY (id),
    KEY idx_documents_uploaded_at (uploaded_at),
    KEY idx_documents_name (name),
    KEY idx_documents_size (size_bytes),
    KEY idx_documents_type_uploaded (file_type, uploaded_at),
    KEY idx_documents_status_uploaded (status, uploaded_at),
    KEY idx_documents_source_sha256 (source_sha256),
    FULLTEXT KEY ft_documents_name (name) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 6.4 `document_tasks`

```sql
CREATE TABLE IF NOT EXISTS document_tasks (
    id VARCHAR(32) NOT NULL COMMENT '任务 ID（task_id）',
    document_id VARCHAR(32) NOT NULL COMMENT '文档 ID',
    version INT NOT NULL COMMENT '解析版本，从 1 开始',
    status VARCHAR(32) NOT NULL
        COMMENT 'PENDING/RUNNING/COMPLETED/FAILED',
    stage VARCHAR(32) NOT NULL
        COMMENT 'PENDING/SPLITTING/PARSING/MERGING/CHUNKING/INDEXING/DONE',
    progress INT NOT NULL DEFAULT 0 COMMENT '0-100',
    step VARCHAR(255) NOT NULL DEFAULT '' COMMENT '当前步骤描述',
    error_message VARCHAR(1024) NULL COMMENT '失败原因',
    result_json JSON NULL COMMENT '阶段检查点和结果',
    created_at DATETIME(6) NOT NULL COMMENT '创建时间，UTC',
    updated_at DATETIME(6) NOT NULL COMMENT '更新时间，UTC',
    PRIMARY KEY (id),
    UNIQUE KEY uk_document_tasks_document_version (document_id, version),
    KEY idx_document_tasks_status_updated (status, updated_at),
    KEY idx_document_tasks_document (document_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 6.5 状态枚举与约定

| 表 | 字段 | 取值 |
| --- | --- | --- |
| `upload_sessions` | `status` | `UPLOADING`、`COMPLETING`、`COMPLETED`、`ABORTING`、`ABORTED`、`EXPIRED`、`FAILED` |
| `documents` | `status` | `UPLOADED`、`PARSING`、`CHUNKING`、`INDEXING`、`READY`、`FAILED` |
| `document_tasks` | `status` | `PENDING`、`RUNNING`、`COMPLETED`、`FAILED` |
| `document_tasks` | `stage` | `PENDING`、`SPLITTING`、`PARSING`、`MERGING`、`CHUNKING`、`INDEXING`、`DONE` |

约定：

- 所有时间用 UTC 存 `DATETIME(6)`，比较用数据库时间，避免多实例时钟偏差。
- 主键统一 32 位十六进制；`fingerprint` / `source_sha256` 用 ascii，避免 utf8mb4 索引膨胀。
- 不建外键，关系在 service 层保证（沿用项目现有风格）。
- `upload_sessions.document_id` 唯一；`document_tasks (document_id, version)` 唯一。
- `documents.chunk_count` 是子块数，`parent_chunk_count` 是父块数，命名不要反过来。
- `processing_tasks` 不再使用；`documents.status` 去掉 `PROCESSING`。开发库直接重建；如果要保留旧数据，迁移时把 `PROCESSING` 映射到 `PARSING`。
- `object_key` / `file_url` 除了字符长度，还要校验 UTF-8 字节数不超过 S3 key 上限（1024 字节）。
- 如果部署环境的 MySQL 没有 ngram 插件，去掉 `documents` 的 `FULLTEXT KEY ft_documents_name`。

## 7. 接口契约（Q4）

### 7.1 端点与状态码

| 方法 | 路径 | 作用 | 幂等性 |
| --- | --- | --- | --- |
| `POST` | `/api/uploads/init` | 创建或复用上传会话 | 唯一活跃 fingerprint；重复调用返回同一个会话 |
| `PUT` | `/api/uploads/{uploadId}/parts/{partNumber}` | 上传一个分片 | 同一分片号覆盖，可安全重试 |
| `GET` | `/api/uploads/{uploadId}` | 查询会话状态和已传分片 | 只读 |
| `POST` | `/api/uploads/{uploadId}/complete` | 校验、合并、写库、投递解析 | 状态机保证；重复调用返回已有结果或“进行中” |
| `DELETE` | `/api/uploads/{uploadId}` | 取消上传 | 终态重复调用返回 204 |

| 接口 | 成功 | 常见失败 |
| --- | --- | --- |
| `init` | `200` 复用 / `201` 新建 / `202` 正在完成或正在取消 | `409` 指纹冲突、`413` 文件过大、`415` 类型不支持、`422` 参数非法；`429`/`503` 队列限流保留，v1 不启用 |
| `part` | `200` | `409` 已完成/正在完成、`410` 已取消/已过期、`411` 缺少 `Content-Length`、`413` 分片过大、`422` 分片大小不符、`429` 分片繁忙、`502/503` 存储不可用 |
| `get` | `200`（含终态） | `404` 会话不存在 |
| `complete` | `200` 会话已完成 / `202` 另一个请求正在完成 | `409` 缺分片、`410` 已取消/已过期、`502/503` |
| `delete` | `204` | `404` 不存在、`409` 已完成/正在完成 |

约定：

- `GET` 对 `COMPLETED/ABORTED/EXPIRED/FAILED` 也返回 `200`，用响应里的 `status` 区分；不要用 410 表示“这个会话已过期”。
- `part` 和 `complete` 对过期/取消返回 `410`，因为这两个操作确实不能继续。
- `init` 遇到 `COMPLETING` 或 `ABORTING` 返回 `202`，响应里带 `uploadId` 和 `status`，前端轮询 `GET`。
- 所有写接口的 5xx 都要区分“可重试”（`502/503`、超时）和“不可重试”（参数/状态错误）。

### 7.2 通用约定

- HTTP JSON 用 **camelCase**：`uploadId`、`documentId`、`sizeBytes`、`totalParts`、`uploadedParts`、`expiresAt`；
- 数据库列和 Python 内部字段用 **snake_case**；
- 时间统一 ISO 8601 UTC，带 `Z`，例如 `2026-10-21T12:00:00Z`；
- ID 是 32 位十六进制字符串；
- 状态枚举大写；
- 错误统一：

```json
{
  "error": {
    "code": "INVALID_PART_SIZE",
    "message": "分片大小与预期不符",
    "details": {"expected": 8388608, "actual": 123}
  }
}
```

- `details` 可选，字段也用 camelCase；不把堆栈、SQL、S3 内部错误直接返回给前端。
- 单文件上限由 `MAX_DOCUMENT_BYTES` 配置，默认按 1000MiB（`1,048,576,000` 字节）；如果产品口径是十进制 1000MB，改成 `1,000,000,000`。

### 7.3 `POST /api/uploads/init`

请求：

```json
{
  "filename": "年度报告.pdf",
  "sizeBytes": 1048576000,
  "fingerprint": "9f2c...64位小写十六进制"
}
```

不传 `fileType`、`chunkSize`、`lastModified`：类型由服务端从文件名推导，分片大小由服务端配置决定。

新建 `201`：

```json
{
  "uploadId": "0f3a...",
  "documentId": "a1b2...",
  "status": "UPLOADING",
  "chunkSize": 8388608,
  "totalParts": 125,
  "uploadedParts": [],
  "expiresAt": "2026-10-21T12:00:00Z"
}
```

复用 `200`：字段相同，`uploadedParts` 是已传分片：

```json
"uploadedParts": [
  {"partNumber": 1, "sizeBytes": 8388608},
  {"partNumber": 2, "sizeBytes": 8388608}
]
```

已完成 `200`：

```json
{
  "uploadId": "0f3a...",
  "documentId": "a1b2...",
  "status": "COMPLETED",
  "chunkSize": 8388608,
  "totalParts": 125,
  "uploadedParts": [],
  "expiresAt": "2026-10-21T12:00:00Z",
  "taskId": "c3d4...",
  "taskStatus": "PENDING"
}
```

正在完成 `202`：

```json
{
  "uploadId": "0f3a...",
  "documentId": "a1b2...",
  "status": "COMPLETING",
  "expiresAt": "2026-10-21T12:00:00Z"
}
```

正在取消 `202`：`status` 为 `ABORTING`，字段同上。

### 7.4 `PUT /api/uploads/{uploadId}/parts/{partNumber}`

请求头：

```http
Content-Type: application/octet-stream
Content-Length: 8388608
```

请求体就是分片原始字节，不用 `multipart/form-data`。

成功 `200`：

```json
{
  "partNumber": 1,
  "sizeBytes": 8388608
}
```

规则：

- `partNumber` 从 1 开始，最大 `totalParts`；
- 非最后一片必须正好等于 `chunkSize`；最后一片等于 `sizeBytes - (totalParts - 1) * chunkSize`；
- `Content-Length` 必须存在，缺失返回 `411`；
- `Content-Length` 超过预期返回 `413 PART_TOO_LARGE`，不等于预期返回 `422 INVALID_PART_SIZE`；
- 读取请求体时再次核对实际字节数；不一致返回 `422`；
- 同一分片号重复上传直接覆盖 S3 同编号 Part，天然幂等；
- 不返回 ETag；`complete` 时服务端通过 `ListParts` 获取。

### 7.5 `GET /api/uploads/{uploadId}`

`200`：

```json
{
  "uploadId": "0f3a...",
  "documentId": "a1b2...",
  "filename": "年度报告.pdf",
  "sizeBytes": 1048576000,
  "chunkSize": 8388608,
  "totalParts": 125,
  "status": "UPLOADING",
  "uploadedParts": [
    {"partNumber": 1, "sizeBytes": 8388608}
  ],
  "expiresAt": "2026-10-21T12:00:00Z",
  "errorMessage": null
}
```

终态规则：

- `COMPLETED`：`uploadedParts` 返回 `[]`，额外带 `taskId`、`taskStatus`；
- `ABORTED` / `EXPIRED` / `FAILED`：`uploadedParts` 返回 `[]`，`errorMessage` 有值就返回；
- `COMPLETING`：如果 MPU 还在，可以返回当前已传分片；如果已经合并，返回 `[]`；
- 会话不存在返回 `404`。

### 7.6 `POST /api/uploads/{uploadId}/complete`

请求体为空。

成功 `200`（刚完成或之前已完成）：

```json
{
  "uploadId": "0f3a...",
  "documentId": "a1b2...",
  "status": "COMPLETED",
  "taskId": "c3d4...",
  "taskStatus": "PENDING"
}
```

- `status` 是上传会话状态；
- `taskStatus` 是 `document_tasks.status`，可能为 `PENDING/RUNNING/COMPLETED/FAILED`；
- 如果解析任务还没接入，任务可能直接是 `COMPLETED`。

另一个请求正在完成 `202`：

```json
{
  "uploadId": "0f3a...",
  "documentId": "a1b2...",
  "status": "COMPLETING"
}
```

缺分片 `409`：

```json
{
  "error": {
    "code": "UPLOAD_INCOMPLETE",
    "message": "还有分片未上传",
    "details": {"missingParts": [6, 7]}
  }
}
```

### 7.7 `DELETE /api/uploads/{uploadId}`

- 成功或幂等：`204 No Content`，无响应体；
- 已完成：`409 UPLOAD_ALREADY_COMPLETED`；
- 正在完成：`409 UPLOAD_COMPLETING`；
- 不存在：`404 UPLOAD_NOT_FOUND`；
- 已经是 `ABORTED` / `EXPIRED` / `ABORTING`：直接 `204`。

### 7.8 字段汇总

| 字段 | 类型 | 出现位置 | 说明 |
| --- | --- | --- | --- |
| `uploadId` | string | 除 part 外 | 会话 ID |
| `documentId` | string | init/get/complete | 预生成文档 ID |
| `filename` | string | init 请求、get | 文件名 |
| `sizeBytes` | integer | init 请求、get、part | 文件或分片字节数 |
| `fingerprint` | string | init 请求 | 全文件 SHA-256 |
| `chunkSize` | integer | init/get | 固定 8388608 |
| `totalParts` | integer | init/get | 分片总数 |
| `uploadedParts` | array | init/get | `{partNumber,sizeBytes}` |
| `status` | string | 所有响应 | 会话状态 |
| `taskId` | string | completed/complete | 任务 ID |
| `taskStatus` | string | completed/complete/get | 任务状态 |
| `expiresAt` | string | init/get | ISO 8601 UTC |
| `errorMessage` | string/null | get | 失败原因 |

### 7.9 错误码

| HTTP | code | 出现位置 | 说明 | 可重试 |
| --- | --- | --- | --- | --- |
| 400 | `INVALID_FILENAME` | init | 文件名为空、路径穿越、含 NUL、超过 255 字符 | 否 |
| 400 | `INVALID_FINGERPRINT` | init | 不是 64 位小写十六进制 | 否 |
| 404 | `UPLOAD_NOT_FOUND` | get/part/complete/delete | 会话不存在 | 否 |
| 404 | `TASK_NOT_FOUND` | tasks | 任务不存在 | 否 |
| 409 | `FINGERPRINT_CONFLICT` | init | 同指纹但文件名/大小与现有会话不一致 | 否 |
| 409 | `UPLOAD_ALREADY_COMPLETED` | part/delete | 会话已完成，不能继续传或取消 | 否 |
| 409 | `UPLOAD_COMPLETING` | delete | 另一个请求正在完成，不能取消 | 是，稍后重试 |
| 409 | `UPLOAD_ABORTING` | complete | 会话正在取消，不能完成 | 是，等终态 |
| 409 | `UPLOAD_FAILED` | part/complete | 会话已失败，需要重新 init | 否 |
| 409 | `UPLOAD_INCOMPLETE` | complete | 还有分片未上传，`details.missingParts` | 是，补传后重试 |
| 410 | `UPLOAD_ABORTED` | part/complete | 会话已取消或正在取消 | 否 |
| 410 | `UPLOAD_EXPIRED` | part/complete | 会话已过期 | 否 |
| 411 | `LENGTH_REQUIRED` | part | 缺少 `Content-Length` | 是，补头重试 |
| 413 | `FILE_TOO_LARGE` | init | 超过单文件上限 | 否 |
| 413 | `PART_TOO_LARGE` | part | 分片超过预期大小 | 否 |
| 415 | `UNSUPPORTED_FILE_TYPE` | init | 扩展名不在允许列表 | 否 |
| 422 | `VALIDATION_ERROR` | 所有 | 请求体/路径参数格式错误 | 否 |
| 422 | `INVALID_SIZE` | init | `sizeBytes <= 0` 或不是整数 | 否 |
| 422 | `INVALID_TOTAL_PARTS` | init | 分片数超过 10000 | 否 |
| 422 | `INVALID_PART_NUMBER` | part | 分片号越界 | 否 |
| 422 | `INVALID_PART_SIZE` | part | 分片大小与预期不符 | 否 |
| 429 | `UPLOAD_BUSY` | part | 在途分片达到上限且等待超时 | 是，按 `Retry-After` |
| 429 | `QUEUE_FULL` | init | 异步队列已满（v1 不启用，保留） | 是 |
| 500 | `PERSISTENCE_ERROR` | complete | 数据库事务失败，S3 可能已合并 | 是，稍后重试 |
| 500 | `INTERNAL_ERROR` | 所有 | 未预期错误 | 视情况 |
| 502 | `STORAGE_ERROR` | init/part/complete | S3 返回错误或非法响应 | 是 |
| 503 | `STORAGE_UNAVAILABLE` | init/part/complete | S3 连接失败/超时 | 是 |
| 503 | `DATABASE_UNAVAILABLE` | 所有 | MySQL 不可用 | 是 |
| 503 | `BROKER_UNAVAILABLE` | init | Redis 不可用（v1 不启用，保留） | 是 |

### 7.10 重试策略

| 情况 | 前端动作 |
| --- | --- |
| 网络错误、`429`、`502`、`503` | 指数退避重试，单个分片最多 3 次 |
| `409 UPLOAD_INCOMPLETE` | 补传 `missingParts`，再 complete |
| `409 UPLOAD_COMPLETING` / `202` | 轮询 `GET`，不要连续 complete |
| `410` | 停止，清理本地记录，重新 init |
| `409 FINGERPRINT_CONFLICT` | 停止，提示用户选择取消旧会话或换文件 |
| `4xx` 参数/状态错误 | 不重试，展示 message |
| `500 PERSISTENCE_ERROR` | 退避重试 complete；仍失败则提示稍后再试 |

### 7.11 legacy 接口

- `POST /api/documents` 删除，上传只走 `/api/uploads/*`；
- `GET /api/documents`、`GET /api/documents/{id}`、`DELETE /api/documents/{id}` 保留，属于文档管理，不在本上传契约内；
- 前端旧上传代码需要整体切到新接口。

### 7.12 `GET /api/tasks/{taskId}`

供前端轮询解析/索引任务进度。

`200`：

```json
{
  "id": "c3d4...",
  "documentId": "a1b2...",
  "status": "RUNNING",
  "stage": "PARSING",
  "progress": 42,
  "step": "已解析 5/12 段",
  "errorMessage": null,
  "version": 1
}
```

- `status`：`PENDING/RUNNING/COMPLETED/FAILED`；
- `stage`：`PENDING`、`SPLITTING`、`PARSING`、`MERGING`、`CHUNKING`、`INDEXING`、`DONE`；
- `progress`：0–100；
- `step`：当前步骤描述；
- 不存在返回 `404 TASK_NOT_FOUND`；
- 该接口替换现有基于 `processing_tasks` 的任务查询，字段沿用 camelCase。

## 8. 完成与失败补偿（Q5）

### 8.1 解析任务投递补偿

`complete` 的顺序：

1. 抢占 `COMPLETING`；
2. `ListParts` + `CompleteMultipartUpload`；
3. 事务写 `documents` + `document_tasks(PENDING, version=1)` + 会话 `COMPLETED`；
4. 提交事务；
5. 投递解析任务。

投递结果处理：

- 投递成功：返回 `200`，`taskStatus=PENDING`；
- 投递失败：**任务保持 `PENDING`，接口仍返回 `200`**，记录日志和指标；不标 `FAILED`、不回滚、不抛 500。

原因：上传本身已经成功，不能因为 Redis 抖动让用户重传 1GB；`PENDING` 是可恢复状态。

**PENDING 行就是 outbox**，不新增 outbox 表。

补投任务：

```text
每 5 分钟：
  SELECT document_tasks
  WHERE status = 'PENDING'
    AND updated_at < now() - 5 分钟
  ORDER BY created_at
  LIMIT 50
  FOR UPDATE SKIP LOCKED
  -> 逐个 delay()
  -> 更新 updated_at = now()
```

- 投递成功后更新 `updated_at`，避免每轮重复投；
- worker 还没取走时任务仍是 `PENDING`，下一轮还会再投；重复消息由 worker 幂等挡住；
- 最坏情况下，任务会在 `complete` 后约 10 分钟才被补投；如果希望更快，可改成「每 5 分钟扫、超过 2 分钟」，最坏约 7 分钟；
- 不新增字段；`updated_at` 兼作“最近一次投递尝试时间”。后续要统计投递次数再加 `dispatch_attempts`。

worker 入口先抢占：

```sql
UPDATE document_tasks
SET status = 'RUNNING',
    stage = 'SPLITTING',
    progress = 0,
    updated_at = UTC_TIMESTAMP(6)
WHERE id = ?
  AND status = 'PENDING'
```

- 影响 1 行：本 worker 拿到任务；
- 影响 0 行：别的 worker 已拿走，或任务已终态，直接返回；
- 同一个消息被投递两次，也只有一个 worker 真正执行。

边界：

- 事务提交后、`delay()` 前进程崩溃：任务留在 `PENDING`，beat 补投；
- `delay()` 成功但消息丢失：任务仍是 `PENDING`，beat 补投；worker 幂等；
- broker 长时间不可用：任务一直 `PENDING`，UI 显示“排队中”，恢复后自动补投；
- worker 抢到 `RUNNING` 后崩溃：任务停在 `RUNNING`，属于解析阶段的租约/重试问题，不在上传范围。

### 8.2 清理任务

| 任务 | 频率 | 扫描条件 | 动作 |
| --- | --- | --- | --- |
| `expire_uploads` | 每小时 | `UPLOADING` 且 `expires_at <= now()` | 条件更新为 `ABORTING(EXPIRED)` → abort MPU → `EXPIRED` |
| `retry_aborting` | 每小时 | `ABORTING` 且 `updated_at` 超过租约 | 重试 abort → `ABORTED` / `EXPIRED` |
| `recover_completing` | 每 5 分钟 | `COMPLETING` 且 `updated_at` 超过租约 | 走恢复流程，不直接 abort |
| `cleanup_orphan_mpu` | 每天 | S3 里没有对应会话的 MPU | 超过宽限期后 abort |
| `cleanup_orphan_object` | 每天 | `FAILED/ABORTED/EXPIRED` 且超过 7 天、对象存在但无 documents 行 | 删除对象 |
| `redispatch_pending_tasks` | 每 5 分钟 | `document_tasks.status='PENDING'` 且 `updated_at` 超过 5 分钟 | 重新投递解析任务 |
| `cleanup_old_sessions` | 每天 | 终态且超过 30 天 | 删除会话行（可选） |

**`recover_completing`**：

`COMPLETING` 不能像过期上传一样直接 abort，因为 S3 可能已经合并成功。恢复顺序：

1. `HeadObject(object_key)`：
   - 存在且大小等于 `size_bytes` → 跳过合并，补写 documents/tasks，置 `COMPLETED`；
   - 不存在 → `ListParts`：
     - 分片齐全 → `CompleteMultipartUpload`，再补写 DB；
     - 缺分片且未过期 → 回退 `UPLOADING`，让用户补传；
     - 缺分片且已过期 → `ABORTING(EXPIRED)`；
     - `NoSuchUpload` 且对象不存在 → `FAILED`；
   - S3 暂时不可用 → 保持 `COMPLETING`，下一轮再试。
2. 写 DB 时仍带 `claim_version` 条件，防止和原来的执行者双写。

这个恢复函数和 `complete` 共用，避免两套逻辑。

**`cleanup_orphan_mpu`**：

MPU 和会话不是原子创建的：正常先建 MPU 再 INSERT 会话；如果 INSERT 前进程崩溃，会留下没有会话的 MPU。

- 用 S3 `ListMultipartUploads` 列出进行中的 MPU；
- 按 `s3_upload_id` 查 `upload_sessions`；
- 没有对应会话、或会话已终态且超过宽限期（建议 24 小时）→ `AbortMultipartUpload`；
- 只处理创建时间超过 1 小时的 MPU，避免和正在 `init` 的请求撞车。

需要先验证 SeaweedFS 3.99 的 `ListMultipartUploads` 是否可用；如果不可用，先记录日志并依赖后续的 S3 生命周期规则，文档中标注为限制。

**`cleanup_orphan_object`**：

“S3 已合并、DB 一直没写成功、最后会话变成 `FAILED`”会留下完整对象但没有 `documents` 行。

- 只扫 `FAILED` / `ABORTED` / `EXPIRED` 且 `updated_at` 超过 7 天的会话；
- `HeadObject(object_key)`：
  - 对象不存在 → 跳过；
  - 对象存在，且 `documents` 里没有对应 `document_id` → 删除对象；
  - 对象存在，但 `documents` 行存在 → 不删，记录告警（数据不一致）。
- 对象 key 里带 `document_id`，不会误删别的文档。

**执行原则**：

- 先条件更新“抢占”状态，再做 S3 调用；不要在一个数据库事务里等 S3。
- 批量扫描用 `FOR UPDATE SKIP LOCKED`，每批 50–100 条。
- abort/delete 忽略 `NoSuchUpload` / `NoSuchKey`，保证幂等。
- 所有终态更新都带条件，避免和用户请求互相覆盖。
- 清理任务放独立的 `maintenance` 队列（并发 1），不要和解析/索引抢 worker。
- Celery beat 必须部署；如果暂时没有 beat，用系统 cron 调同一个管理命令。

**监控**：

- 过期会话数、abort 失败数；
- `COMPLETING` 超过 10 分钟的数量；
- 孤儿 MPU / 孤儿对象数量；
- `PENDING` 任务的最大年龄和数量；
- 每次清理的耗时和批次大小。

告警建议：

- `PENDING` 任务超过 15 分钟；
- `COMPLETING` 超过 10 分钟；
- abort 连续失败；
- 孤儿对象删除数为正时也要留痕。

**边界**：

- 解析任务 `RUNNING` 超时/重试、MinerU 分段恢复属于解析阶段设计，不在本上传方案内；
- 文档删除时清理源对象属于文档管理；上传会话保留，`init` 发现文档已删除会新建会话；
- API 不留完整临时文件，因此没有本地临时文件清理。

**建议默认配置**：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `UPLOAD_COMPLETE_LEASE_SECONDS` | 300 | `COMPLETING/ABORTING` 租约 |
| `REDISPATCH_INTERVAL_SECONDS` | 300 | 补投扫描间隔 |
| `REDISPATCH_MIN_AGE_SECONDS` | 300 | PENDING 任务最小年龄 |
| `ORPHAN_MPU_GRACE_HOURS` | 24 | 孤儿 MPU 宽限 |
| `ORPHAN_OBJECT_RETENTION_DAYS` | 7 | 孤儿对象保留天数 |
| `SESSION_RETENTION_DAYS` | 30 | 终态会话保留天数 |
| `CLEANUP_BATCH_SIZE` | 50 | 每批处理条数 |

## 9. 并发与限流（Q6）

### 9.1 并发预算

按 10 个用户、每文件 2 个分片并发：

- 在途分片：10 × 2 = **20 个**；
- 内存：20 × 8MiB = **160MiB**；加上请求解析、boto3 缓冲，按 256MiB 预算；
- 每 1GB 文件约 120–125 片，10 人同时传，API 大部分时间在等网络，不在算 CPU。

### 9.2 限制参数

| 项 | 建议值 | 说明 |
| --- | --- | --- |
| API 进程数 | v1 先 **1 个 uvicorn 进程** | I/O 密集型，1 个进程 + 线程池够用 |
| 全局在途分片 | **20** | 进程内 `asyncio.Semaphore` |
| 每文件并发分片 | **2** | 前端限制；API 只做全局上限 |
| 等待队列上限 | **40** | 超过直接 429，避免无限堆积 |
| 最长等待 | **30 秒** | 等不到槽位返回 `429 UPLOAD_BUSY` + `Retry-After: 5` |
| FastAPI 线程池 | **40** | 要大于在途分片数，留出其他接口余量 |
| boto3 连接池 | **50** | `max_pool_connections=50` |
| boto3 超时 | connect 10s / read 120s | 和单分片 120 秒对齐 |
| nginx body 上限 | **9m** | 8MiB 分片留余量 |
| uvicorn 并发上限 | **200** | 大于“20 个分片 + 状态查询” |

### 9.3 实现方式

- `part` 接口用 `async def`，先拿 `asyncio.Semaphore(20)` 的槽位，再读取请求体；
- 读取 8MiB 到内存后，用 `run_in_threadpool` 调用 boto3 `upload_part`；
- 等待槽位用 `asyncio.wait_for(..., timeout=30)`；等待中的请求不占线程池；
- 进程内维护 `waiting` 计数，超过 40 直接返回 `429 UPLOAD_BUSY`，不要无限排队；
- `429` 响应带 `Retry-After: 5`，前端按退避重试；
- 槽位在 `finally` 中释放，S3 失败也要释放。

**不要用同步 `def` 实现 part 接口再阻塞等信号量**：那样等待中的请求会占满 FastAPI 线程池，`init/get/complete` 会被拖死。

### 9.4 为什么不做按用户限流

当前没有鉴权，识别不了“用户”，只能按上传会话或 IP：

- 按 IP 会误伤同一 NAT 后面的多个人；
- 按会话挡不住一个人开多个文件。

所以 v1 只做两层：

1. **前端每文件 2 个并发**，防止单个文件占满；
2. **API 全局 20 个在途分片**，防止总量失控。

以后补了鉴权，再按用户/租户做配额。

### 9.5 队列限流

v1 **不在 `init` 拦截**：

- 上传本身总是允许完成；
- 解析任务投递失败就停在 `PENDING`，由第 8 节的补投兜底；
- 不会出现“队列满，用户 1GB 传完被拒绝”；
- `QUEUE_FULL` / `BROKER_UNAVAILABLE` 错误码保留，等真有队列压力再启用。

如果以后要启用，只做“软检查”：

- `LLEN` 解析队列 >= `TASK_QUEUE_LIMIT` 时返回 `429 QUEUE_FULL`；
- Redis 不可达时跳过检查，不要因此拒绝上传；
- 这仍然不能保证 `complete` 时队列不忙，只是提前劝退。

### 9.6 多实例扩展

v1 单实例，进程内信号量就够。

以后 API 多实例，进程内信号量会变成“每实例 20”，总数不可控。可选方案：

- 按实例数分摊，例如 2 个实例、每实例 10；
- 或者上 Redis 全局信号量（ZSET + 过期清理），跨实例共享 20 个槽位。

本阶段不实现，文档标为后续扩展。

### 9.7 部署参数

- nginx：`client_max_body_size 9m`、`proxy_request_buffering off`、`proxy_read_timeout 120s`、`client_body_timeout 120s`；
- uvicorn：`--workers 1 --limit-concurrency 200`；
- FastAPI 线程池：40；
- boto3：`max_pool_connections=50`、`connect_timeout=10`、`read_timeout=120`、`retries=3`；
- MySQL：20 个并发分片每个只做一次会话查询，默认连接数够用；后续加连接池再调。

### 9.8 监控

- `upload_inflight_parts`：当前在途分片数；
- `upload_waiting_parts`：等待槽位的请求数；
- `upload_part_duration`：分片耗时；
- `upload_429_total`：`UPLOAD_BUSY` 次数；
- API 进程内存、boto3 连接池使用率；
- `init/get/complete` 的 P95 延迟。

告警建议：

- `upload_waiting_parts` 长时间接近 40；
- `upload_429_total` 持续增长；
- API 内存接近预算；
- boto3 连接池耗尽。

## 10. 前端交互（Q7）

### 10.1 队列与并发

v1 一次只传一个文件：

- 当前文件 2 个分片并发；
- 其他文件在队列里显示“等待上传”；
- 当前文件 `complete` 或失败后，自动开始下一个；
- 10 个用户 × 2 个分片 = 20，正好对上 API 的全局上限；
- 前端不实现“同时传两个文件”，后续需要时把全局分片并发改成 4 即可。

### 10.2 前端状态

| 状态 | 含义 |
| --- | --- |
| `PENDING` | 排队等待 |
| `HASHING` | 正在计算全文件 SHA-256 |
| `UPLOADING` | 分片上传中 |
| `PAUSED` | 用户暂停 |
| `COMPLETING` | 已发 complete，等待合并 |
| `PROCESSING` | 解析/索引处理中 |
| `SUCCESS` | 全部完成 |
| `ERROR` | 失败 |
| `CANCELLED` | 已取消 |

- 文件选中后先进入 `PENDING`；
- 轮到它时进入 `HASHING`，算完指纹再 `init`；
- `init` 返回后进入 `UPLOADING`；
- 全部分片完成 → `COMPLETING` → `PROCESSING` → `SUCCESS`。

### 10.3 进度计算

- 已完成分片的字节数 + 在途分片的 `upload.onprogress.loaded`，除以文件总大小；
- 暂停、重试时把该分片的 `loaded` 归零，避免进度虚高；
- 恢复上传时，用服务端返回的 `uploadedParts` 初始化已完成字节；
- 进度只用于展示，不参与业务判断；
- 传输进度必须用 **XHR 的 `upload.onprogress`**；`fetch` 没有上传进度，不要用 `fetch` 做分片上传。

### 10.4 暂停 / 恢复 / 取消

**暂停**：

- 只允许在 `UPLOADING` 时暂停；
- `AbortController.abort()` 中止在途分片请求；
- 不调用 `DELETE`，已传分片保留在 S3；
- 状态置 `PAUSED`，释放前端并发槽位。

**恢复**：

- 调 `GET /api/uploads/{uploadId}` 或 `init` 重新获取 `uploadedParts`；
- 只传缺失分片；
- 如果会话已 `EXPIRED/ABORTED`，提示重新选择文件。

**取消**：

- 中止在途请求；
- 调 `DELETE /api/uploads/{uploadId}`；
- `204` → 清理本地记录；
- `409 UPLOAD_ALREADY_COMPLETED` → 提示“已完成，不能取消”；
- `409 UPLOAD_COMPLETING` → 提示“正在合并，稍后再试”；
- 网络错误 → 保留记录，允许重试取消。

### 10.5 重试策略

分片：

- 网络错误 / `429` / `5xx`：最多 3 次，退避 1s / 2s / 4s；
- `429 UPLOAD_BUSY` 优先按 `Retry-After`；
- `4xx` 参数/状态错误：不重试，直接报错。

`complete`：

- 网络错误 / `5xx`：退避重试；
- `202` / `409 UPLOAD_COMPLETING`：不重试，轮询 `GET`；
- `409 UPLOAD_INCOMPLETE`：补传 `missingParts` 后再 complete；
- `410`：停止，清理本地记录；
- 客户端 `complete` 超时 **180 秒**；超时后先 `GET` 会话状态，不要直接判失败。

### 10.6 完成后轮询任务

`complete` 返回 `taskId` 后，轮询 `GET /api/tasks/{taskId}`：

- `PENDING`：显示“排队中”；
- `RUNNING`：按 `stage` 显示文案，更新 `progress`；
- `COMPLETED`：`SUCCESS`；
- `FAILED`：`ERROR`，展示 `errorMessage`；
- 轮询间隔 2 秒；页面隐藏时暂停，回到页面再继续；
- 终态后停止轮询。

### 10.7 刷新后的处理

- 上传中的任务：按第 5 节的 `rag.upload.v2` 恢复，用户重新选文件后续传；
- 已 `complete`、正在解析的任务：单独存 `rag.processing.v2`，只放 `taskId`、`documentId`、`filename`、`startedAt`；
- 页面加载时对 `rag.processing.v2` 里的任务调 `GET /api/tasks/{taskId}`，终态就移除；
- 这样刷新后还能看到“正在解析”，不用只靠文档列表。

### 10.8 文案

| 场景 | 文案 |
| --- | --- |
| `HASHING` | 正在校验文件… |
| `PENDING` | 排队中 |
| `UPLOADING` | 上传中 42% · 2.1 MB/s |
| `PAUSED` | 已暂停 |
| `COMPLETING` | 正在合并文件… |
| `SPLITTING` | 正在拆分文件 |
| `PARSING` | 正在解析文档 |
| `MERGING` | 正在合并解析结果 |
| `CHUNKING` | 正在切分文本 |
| `INDEXING` | 正在建立索引 |
| `SUCCESS` | 处理完成 |
| `429 UPLOAD_BUSY` | 当前上传人数较多，正在重试… |
| `410 UPLOAD_EXPIRED` | 上传已过期，请重新选择文件 |
| `409 FINGERPRINT_CONFLICT` | 同名文件已有未完成上传，请选择继续或取消 |

### 10.9 实现注意

- `useUploadManager` 需要重写，不能沿用现在“上传整个文件 + 轮询任务”的模型；
- 分片上传用 XHR，普通 JSON 请求用 `fetch`；
- SHA-256 放 Web Worker，避免 1GB 文件阻塞 UI；
- 多标签页用 `storage` 事件同步；
- 旧 `POST /api/documents` 已删除，前端旧上传代码要整体切换；
- CORS 需要允许 `PUT`，现有 `allow_methods` 要补上；
- 暂停/取消要区分：暂停不删 S3 分片，取消才 `DELETE`。

## 11. 测试与验收（Q8）

### 11.1 前置能力验证

写代码前先用 SeaweedFS 3.99 做一次最小验证：

- `CreateMultipartUpload` / `UploadPart` / `ListParts` / `CompleteMultipartUpload` / `AbortMultipartUpload`；
- 8MiB 分片、乱序上传、同一分片重复覆盖；
- `ListParts` 分页和 ETag 返回；
- `ListMultipartUploads` 是否可用（Q5 的孤儿 MPU 清理依赖它）；
- 完成后对象内容与原始文件字节完全一致。

如果 `ListMultipartUploads` 不可用，孤儿 MPU 清理降级为“日志 + 人工/生命周期规则”，在文档中标注限制。

### 11.2 单元测试（后端）

- 分片计算：`totalParts`、最后一片大小、边界值（0、1 字节、正好 8MiB、8MiB+1、1000MiB、1000MiB+1）；
- 文件名和扩展名校验：路径穿越、NUL、超长、Unicode、4 字节字符；
- 指纹校验：64 位小写十六进制；
- `init` 复用：活跃会话、指纹冲突、过期迁移、已完成返回、终态新建；
- 唯一活跃会话：模拟并发 `INSERT` 冲突；
- 状态机：`complete` 抢占、租约接管、fencing、abort、过期、恢复；
- 错误码映射：每个 code 对应的 HTTP 状态和 `details`；
- 对象 key 字节长度校验；
- 补投：`delay()` 失败保持 `PENDING`；worker `PENDING → RUNNING` 幂等；
- 清理逻辑：过期会话、僵死状态、孤儿对象判断（用 fake clock 和 fake S3）；
- `GET /api/tasks/{taskId}` 的状态、阶段、进度和 404。

### 11.3 集成测试

- 真实 SeaweedFS：分片上传、乱序、覆盖、合并、abort；
- MySQL 8：DDL、生成列唯一索引、事务回滚、并发条件更新；
- Redis/Celery：投递、补投、worker 幂等；
- API 全链路：init → parts → complete → task PENDING；
- 断点续传：传 5 片后重新 init，返回 `uploadedParts`；
- 取消：DELETE → ABORTED，S3 MPU 被 abort；
- 过期：TTL 到期 → ABORTING → EXPIRED；
- 清理：孤儿 MPU、孤儿对象（用独立测试前缀隔离）；
- legacy：`POST /api/documents` 已删除；`GET/DELETE /api/documents` 仍正常；
- 任务接口：`GET /api/tasks/{taskId}` 返回 `document_tasks` 的字段。

### 11.4 故障注入

- `UploadPart` 5xx/超时；
- `CompleteMultipartUpload` 返回 5xx 但实际已合并；
- `ListParts` 暂时不一致；
- S3 合并成功、MySQL 写入失败；
- Redis/broker 在 `complete` 时不可用；
- API 进程在“事务提交后、投递前”退出；
- 并发 init、并发 complete、complete vs abort、complete vs 清理、part vs complete；
- 浏览器：断网、刷新、关标签、暂停、重复点击、多标签页。

### 11.5 压测

- 10 个客户端并发上传 1GB；
- API 在途分片 20，观察内存、CPU、429 比例、P95；
- 单文件 1GB 在 100Mbps 下限时目标 **< 2 分钟**（理论约 84 秒，留余量）；
- 100 个状态轮询/秒；
- 连续跑 100 个文件，观察连接池、内存、清理任务；
- API RSS 目标 **< 512MiB**。

注意：10 × 1GB 的聚合带宽是 10Gbps，普通千兆环境跑不到；压测时可以限速客户端或减少单文件大小，重点看 API 内存、429 比例、错误率和清理是否稳定。

### 11.6 前端测试

- Vitest：SHA-256 worker、队列调度、进度计算、重试退避、暂停/恢复、localStorage 读写、任务轮询；
- Playwright：选文件 → 校验 → 上传 → 暂停/恢复 → 刷新后继续 → 取消 → 完成轮询；
- 自动化用 100MB 文件，1GB 手工验证；
- 多标签页、断网恢复、`429 UPLOAD_BUSY` 重试。

### 11.7 验收标准

| 编号 | 验收项 |
| --- | --- |
| AC-01 | 1GB 文件通过 8MiB 分片上传成功，S3 对象 SHA-256 与客户端一致 |
| AC-02 | 传 5 片后刷新，重新选同一文件，只补传缺失分片并完成 |
| AC-03 | 暂停后恢复，不重复上传已完成分片，进度正确 |
| AC-04 | 取消上传后 MPU 被 abort，会话为 ABORTED |
| AC-05 | 10 人并发 1GB，API 内存 < 512MiB，无未预期 5xx |
| AC-06 | 重复 init/complete 幂等，不产生第二份文档或任务 |
| AC-07 | S3 已合并、DB 失败后可恢复，最终只有一份文档 |
| AC-08 | broker 不可用时上传仍成功，任务 PENDING，恢复后自动补投 |
| AC-09 | 过期会话、孤儿 MPU、孤儿对象能被清理 |
| AC-10 | legacy 上传入口已删除，文档列表/详情/删除仍正常 |
| AC-11 | 前端能显示解析阶段的 stage/progress，刷新后仍能看到 |
| AC-12 | API 不落完整文件，内存不随文件大小线性增长 |

### 11.8 环境与 CI

- 本地/CI：Docker Compose 起 SeaweedFS 3.99、MySQL 8、Redis 7；
- 后端：`ruff check` + `pytest`；
- 前端：`npm run build` + `vitest`；
- 集成测试放夜间任务，避免每次 PR 都跑 1GB 压测；
- Playwright 用 100MB 文件跑 PR，1GB 手工/夜间；
- 每次测试用独立前缀/桶，结束后清理。

### 11.9 测试数据

- 边界大小：0、1 字节、8MiB、8MiB+1、1000MiB、1000MiB+1；
- 文件名：Unicode、255 字符、4 字节字符、路径穿越、NUL、大小写扩展名；
- 重复文件：同指纹同文件名、同指纹不同文件名、同名不同内容；
- 过期场景：用 fake clock 或可配置 TTL，不等待 24 小时；
- 大文件：1GB 随机内容，保证 SHA-256 可校验；上传测试不依赖解析。

## 12. 安全边界与假设（Q9：不做鉴权）

### 12.1 结论

本阶段**不做认证、鉴权、多租户**。系统按内网工具设计，前提是不暴露公网；内网匿名访问的风险由网络边界承担，不在应用层解决。

### 12.2 前提

- API、S3、MySQL、Redis、Celery 只在内网；
- 通过防火墙/安全组限制访问来源；
- 不做公网端口映射、反向代理或 CDN 暴露；
- S3 端口不暴露给浏览器；
- 使用系统的都是内网可信用户。

### 12.3 已有保护

- `upload_id`、`document_id`、`task_id` 都是 32 位十六进制随机值，不可猜测；但它们是标识，不是认证凭证；
- 文件名经过路径穿越和 NUL 清洗；文件类型由服务端从文件名推导；
- 文件大小、分片号、分片大小、`Content-Length` 都在服务端校验；
- 对象 key 由服务端生成，不接受客户端传入；
- CORS 只允许配置的前端 Origin，并允许 `PUT`；
- 错误响应不返回堆栈、SQL、S3 内部细节；
- 日志记录 `upload_id` / `document_id` / `task_id`，不记录文件正文；
- 全局 20 个在途分片，避免单个客户端把 API 打满。

### 12.4 接受的风险

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| 内网任何客户端都能调用 init/part/complete | 可以被匿名使用 | 接受；靠网络边界限制，公网暴露前补鉴权 |
| 没有按用户配额 | 一个人可能占满全局 20 个分片 | 接受；监控 429 和等待队列，后续按用户限流 |
| 没有操作审计 | 无法追溯是谁上传/删除 | 接受；日志保留 ID 和操作，后续补用户身份 |
| 文档列表/详情/删除匿名可访问 | 内网可见全部文档 | 接受；ID 不可猜，但仍不是权限控制 |
| 没有病毒扫描和内容审核 | 恶意文件可能进入解析链路 | 解析服务必须隔离运行；上传层只做类型/大小校验 |
| 只按扩展名校验，没有 magic bytes | 改扩展名可绕过类型限制 | 接受；后续可加 magic bytes 校验 |
| `upload_id` 可当作 bearer 标识 | ID 泄露后可操作该会话 | 不暴露公网；日志和前端存储注意不要外泄 |
| 没有传输层加密（内网 HTTP） | 内网嗅探 | 接受；如需 HTTPS 在网关层加 |

### 12.5 以后暴露公网前必须补

- 认证和授权（SSO / Token），上传会话绑定 `owner`；
- `upload_sessions`、`documents` 增加归属字段，按用户隔离列表和删除；
- 按用户/租户的配额和限流；
- 操作审计和告警；
- 如果使用 Cookie 会话，补 CSRF 防护；
- 病毒扫描、magic bytes、内容安全策略；
- HTTPS、WAF、对象存储访问策略；
- 必要时改成浏览器直传 + 预签名 URL，并重新评估 CORS 和签名有效期。

### 12.6 本期明确不做

- 不做登录、注册、Token、Session；
- 不做 owner 字段、RBAC、多租户；
- 不做病毒扫描、内容审核、客户端加密；
- 不做预签名 URL、S3 直传；
- 不做按用户/IP 的配额。

## 13. 概念链路

```text
浏览器                         API                          SeaweedFS S3
  |                             |                              |
  |-- init -------------------->| 创建/复用上传会话             |
  |                             |----------------------------->| CreateMultipartUpload
  |<-- uploadId / totalParts ---|                              |
  |                             |                              |
  |-- PUT part 1 --------------->| 校验会话/分片号/大小         |
  |                             | 抢槽位 + 缓冲 8MiB            |
  |                             |----------------------------->| UploadPart
  |<-- 200 partNumber ----------|                              |
  |          ...                |                              |
  |-- PUT part N --------------->|                              |
  |                             |                              |
  |-- complete ---------------->| ListParts 校验               |
  |                             |----------------------------->| CompleteMultipartUpload
  |                             | 事务写 documents / tasks      |
  |                             | 投递解析任务                  |
  |<-- 202 taskId --------------|                              |
```

说明：接口字段见第 7 节，表结构见第 6 节；会话状态见第 4 节，失败补偿见第 8 节，并发限流见第 9 节，前端交互见第 10 节，测试与验收见第 11 节，安全边界见第 12 节。

## 14. 职责边界

| 角色 | 负责 | 不负责 |
| --- | --- | --- |
| 浏览器 | 切片、调用 API、显示真实分片进度、暂停/恢复、刷新后续传 | 不直接访问 S3 |
| API | 创建/复用会话、校验、缓冲并转发分片、完成合并、落库、投递解析 | 不保存完整文件，不做解析 |
| SeaweedFS S3 | 保存分片、合并对象、提供分片状态（ListParts） | 不承担业务鉴权和会话管理 |
| MySQL | 上传会话、文档、任务等业务状态 | 不保存文件正文 |
| Redis/Celery | 任务队列、补投、限流（后续细化） | 不作为分片状态的唯一来源 |

## 15. 容量与资源估算

按 1000MB/文件、10 人并发、8MiB 分片粗算：

- 一批上传总量约 10GB；API 入站约 10GB、出站约 10GB；
- 千兆全双工下，10GB 单向满速约 80 秒；实际上传会摊在几分钟内，峰值取决于客户端链路和同时在途分片数；
- 每个在途分片缓冲 8MiB，10 个在途约 80MiB；如果每用户 2 个在途分片，约 160MiB；
- 每 1GB 文件约 120–125 个分片请求，10 人约 1200–1250 个请求；
- 单分片超时 120 秒，需同步调整 API、反向代理和 S3 客户端的超时配置。

以上为设计估算，联调时按真实网络、磁盘和 S3 性能校准。

## 16. 待定项

无。Q1–Q9 已全部完成，结论分别见第 4–12 节。实现阶段的新问题另开文档。

## 17. 变更记录

| 版本 | 日期 | 说明 |
| --- | --- | --- |
| V0.1 | 2026-10-20 | 初稿：确认浏览器→API→S3 链路、API 只缓冲分片、8MiB 分片/120 秒超时、API 与 S3 目标分离。 |
| V0.2 | 2026-10-20 | 补充 Q1 会话状态机：7 状态、MySQL 条件更新互斥、5 分钟租约、S3 合并/DB 失败恢复与 fencing。 |
| V0.3 | 2026-10-20 | 补充 Q2 初始化与断点续传：全文件 SHA-256、init 复用规则、唯一活跃会话、并发 init、24 小时 TTL、localStorage 刷新恢复。 |
| V0.4 | 2026-10-20 | 补充 Q3 文档身份与数据模型：document_id 预生成、complete 落库、worker 只更新、三张表 DDL 与状态枚举。 |
| V0.5 | 2026-10-20 | 补充 Q4 接口契约：5 个端点、camelCase、请求/响应示例、错误码与重试策略；删除 legacy `POST /api/documents`。 |
| V0.6 | 2026-10-20 | 补充 Q5 完成与失败补偿：PENDING outbox 补投、worker 幂等、清理任务、孤儿 MPU/对象、maintenance 队列。 |
| V0.7 | 2026-10-20 | 补充 Q6 并发与限流：单 API 进程 20 个在途分片、每文件 2 并发、429 UPLOAD_BUSY、nginx/uvicorn 参数、多实例扩展。 |
| V0.8 | 2026-10-21 | 补充 Q7 前端交互：单文件队列、9 个状态、XHR 进度、暂停/恢复/取消、180 秒 complete 超时、任务轮询与 processing localStorage。 |
| V0.9 | 2026-10-21 | 补充 Q8 测试与验收：前置能力验证、单元/集成/故障/压测/前端测试、12 条验收标准、CI 与测试数据。 |
| V1.0 | 2026-10-21 | 补充 Q9 安全边界：本期不做鉴权，明确内网前提、已有保护、接受风险与公网暴露前的补强项；整理为设计基线。 |
