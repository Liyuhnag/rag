# Backend - RAG API

FastAPI Web 层。当前提供 API 契约和请求校验。`services/` 里是占位，文档存储、检索和模型生成后续再实现。

Python 环境使用 [uv](https://docs.astral.sh/uv/)。解释器版本见 `.python-version`（3.12）。

## 启动

```bash
cd backend
uv sync --all-groups
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
uv run celery -A main.celery_app worker --loglevel=info
uv run celery -A main.celery_app flower
```

接口进程、Celery worker 和 Flower 要在同一台机器上。上传内容先落在本机临时目录，再由 worker 写入对象存储。Redis 只监听 `127.0.0.1:6379`。Flower 监控页是 `http://<主机>:5555`，登录账号在 `FLOWER_BASIC_AUTH`。

接口文档：http://127.0.0.1:8000/docs

测试：

```bash
uv run pytest
```

## 环境变量

配置放在 `backend/.env`，进程启动时读取。已存在的环境变量不会被 `.env` 覆盖。示例见 `.env.example`。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_UPLOAD_BYTES` | `20971520` | 单个文件上限，20MB |
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
| `POST` | `/api/documents` | 校验文件后提交异步处理，返回任务 |
| `GET` | `/api/tasks/{id}` | 返回异步任务状态；完成后带有文档 ID |
| `GET` | `/api/documents/{id}` | 返回一条文档记录 |
| `DELETE` | `/api/documents/{id}` | 删除文档记录和对象 |
| `POST` | `/api/chat` | 校验问题后返回 `501`，检索生成尚未接入 |

允许的扩展名：`pdf`、`md`、`markdown`、`doc`、`docx`、`txt`、`xls`、`xlsx`、`csv`。

列表查询参数为 `page`、`pageSize`、`sortBy`、`sortOrder`。上传表单字段为 `file`。这些成功响应的字段结构已经写在 OpenAPI 里。
