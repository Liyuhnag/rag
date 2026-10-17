# Backend - RAG API

FastAPI Web 层。当前提供 API 契约和请求校验。`services/` 里是占位，文档存储、检索和模型生成后续再实现。

Python 环境使用 [uv](https://docs.astral.sh/uv/)。解释器版本见 `.python-version`（3.12）。

## 启动

```bash
cd backend
uv sync --all-groups
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

接口文档：http://127.0.0.1:8000/docs

测试：

```bash
uv run pytest
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_UPLOAD_BYTES` | `20971520` | 单个文件上限，20MB |
| `UPLOAD_DIR` | `backend/data/uploads` | 原始文件目录 |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许的前端来源，逗号分隔 |

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
| `GET` | `/api/documents` | 校验分页参数。服务占位期间返回 `501` |
| `POST` | `/api/documents` | 校验文件后调用文档服务。服务占位期间返回 `501` |
| `GET` | `/api/documents/{id}` | 调用文档服务。服务占位期间返回 `501` |
| `DELETE` | `/api/documents/{id}` | 调用文档服务。服务占位期间返回 `501` |
| `POST` | `/api/chat` | 校验问题后返回 `501`，检索生成尚未接入 |

允许的扩展名：`pdf`、`md`、`markdown`、`doc`、`docx`、`txt`、`xls`、`xlsx`、`csv`。

列表查询参数为 `page`、`pageSize`、`sortBy`、`sortOrder`。上传表单字段为 `file`，可选 `operator`。这些成功响应的字段结构已经写在 OpenAPI 里。
