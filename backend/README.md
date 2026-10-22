# Backend - RAG API

FastAPI Web 层。当前提供 API 契约和请求校验。`services/` 里是占位，文档存储、检索和模型生成后续再实现。

Python 环境使用 [uv](https://docs.astral.sh/uv/)。解释器版本见 `.python-version`（3.12）。

## 启动

```bash
cd backend
uv sync --all-groups
./start.sh
```

`start.sh` 会拉起接口、解析 worker、维护 worker、定时任务和 Flower。Ctrl+C 会停掉这次启动的全部进程。接口默认 `0.0.0.0:8000`，可用 `API_PORT` 改端口。

接口进程、Celery worker、beat 和 Flower 要在同一台机器上。上传分片由 API 直接写入对象存储；解析任务走 `celery` 队列，补投和清理任务走独立的 `maintenance` 队列。Redis 只监听 `127.0.0.1:6379`。Flower 监控页是 `http://<主机>:5555`，登录账号在 `FLOWER_BASIC_AUTH`。

接口文档：http://127.0.0.1:8000/docs

测试：

```bash
uv run pytest
```

## 环境变量

配置放在 `backend/.env`，进程启动时读取。已存在的环境变量不会被 `.env` 覆盖。示例见 `.env.example`。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_DOCUMENT_BYTES` | `1048576000` | 大文件上传上限，1000MiB |
| `UPLOAD_CHUNK_BYTES` | `8388608` | 分片大小，8MiB |
| `UPLOAD_SESSION_TTL_HOURS` | `24` | 上传会话有效期 |
| `UPLOAD_COMPLETE_LEASE_SECONDS` | `300` | `COMPLETING/ABORTING` 租约 |
| `ORPHAN_MPU_GRACE_HOURS` | `24` | 孤儿 Multipart Upload 宽限期 |
| `ORPHAN_OBJECT_RETENTION_DAYS` | `7` | 孤儿对象保留天数 |
| `SESSION_RETENTION_DAYS` | `30` | 终态会话保留天数 |
| `CLEANUP_BATCH_SIZE` | `50` | 每批清理条数 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许的前端来源，逗号分隔。本机 `localhost` 和 `127.0.0.1` 的其他端口也会放行 |
| `S3_ENDPOINT` | 无 | S3 地址，例如 `http://127.0.0.1:8333` |
| `S3_REGION` | `us-east-1` | 签名区域 |
| `S3_ACCESS_KEY_ID` | 无 | 也可使用 `AWS_ACCESS_KEY_ID` |
| `S3_SECRET_ACCESS_KEY` | 无 | 也可使用 `AWS_SECRET_ACCESS_KEY` |
| `S3_BUCKET` | 无 | 上传使用的桶 |
| `MYSQL_HOST` | `127.0.0.1` | 文档记录库地址 |
| `MYSQL_PORT` | `3306` | 文档记录库端口 |
| `MYSQL_USER` | 无 | 数据库用户 |
| `MYSQL_PASSWORD` | 无 | 数据库密码 |
| `MYSQL_DATABASE` | 无 | 数据库名 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Celery 队列地址 |
| `FLOWER_BASIC_AUTH` | 无 | Flower 登录，格式 `用户名:密码` |
| `FLOWER_ADDRESS` | `0.0.0.0` | Flower 监听地址 |
| `FLOWER_PORT` | `5555` | Flower 端口 |

## 接口

错误响应统一为：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不合法"
  }
}
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/documents` | 分页返回已上传文档 |
| `GET` | `/api/tasks/{id}` | 返回解析任务状态 |
| `GET` | `/api/documents/{id}` | 返回一条文档记录 |
| `DELETE` | `/api/documents/{id}` | 删除文档记录和对象 |
| `POST` | `/api/chat` | 校验问题后返回 `501`，检索生成尚未接入 |

允许的扩展名：`pdf`、`md`、`markdown`、`doc`、`docx`、`txt`、`xlsx`、`csv`。

列表查询参数为 `page`、`pageSize`、`sortBy`、`sortOrder`。文件通过 `/api/uploads` 分片上传。这些成功响应的字段结构已经写在 OpenAPI 里。
