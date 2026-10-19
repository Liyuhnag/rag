# RAG Project

一个基于检索增强生成（RAG）的智能问答系统，支持文档向量化和智能检索。

## 项目结构

```
rag/
├── frontend/          # 前端项目 (Vue 3 + Vite)
│   ├── src/
│   │   ├── components/
│   │   │   ├── FileUpload.vue     # 文件上传组件
│   │   │   └── ChatInterface.vue  # 聊天界面组件
│   │   ├── App.vue
│   │   └── main.js
│   ├── package.json
│   └── vite.config.js
├── backend/           # 后端项目 (FastAPI + uv)
├── docs/             # 项目文档
├── CLAUDE.md         # AI Agent 开发文档
├── AGENT.md          # Git 工作流和规范
└── README.md         # 本文件
```

## 技术栈

### 前端
- **框架**: Vue 3 (Composition API)
- **构建工具**: Vite
- **样式**: 原生 CSS + CSS Grid
- **主题**: 深色主题 + 天蓝色 accent (#38BDF8)

### 后端
- **Web 框架**: FastAPI
- **环境管理**: uv（Python 3.12）
- **已提供**: 文档与对话的 API 契约、参数校验
- **服务占位**: 文档存储、检索和模型生成待实现

## 快速开始

### 前端开发

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

### 后端开发

```bash
cd backend
uv sync --all-groups
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

访问 http://127.0.0.1:8000/docs

### 中间件（Docker Compose）

本地开发需要的 MySQL、Redis、SeaweedFS（S3 对象存储）用 compose 一键启动：

```bash
docker compose up -d        # 启动
docker compose ps           # 查看状态
docker compose down         # 停止（保留数据）
docker compose down -v      # 停止并清空数据
```

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| MySQL | `127.0.0.1:3306` | 库 `app`，账号 `app` / `rag-dev-password` |
| Redis | `127.0.0.1:6379` | Celery broker，任务结果在 `db 1` |
| SeaweedFS S3 | `127.0.0.1:8333` | 桶 `rag-documents` 首次启动自动创建 |
| SeaweedFS Filer | `127.0.0.1:8888` | 文件访问地址，对应 `FILE_BASE_URL` |

把 `backend/.env.example` 复制为 `backend/.env` 后，S3、MySQL 凭据改成 compose 里的本地值：

```bash
S3_ACCESS_KEY_ID=rag-admin
S3_SECRET_ACCESS_KEY=rag-dev-secret
MYSQL_USER=app
MYSQL_PASSWORD=rag-dev-password
MYSQL_DATABASE=app
FILE_BASE_URL=http://127.0.0.1:8888
```

## 检查

提交前安装 pre-commit，之后每次提交会检查文件格式，并在相关目录有改动时运行后端检查和前端构建：

```bash
uvx pre-commit install
```

手动跑一遍：

```bash
uvx pre-commit run --all-files
```

推送到 `feature`、`main` 或打开 Pull Request 时，GitHub Actions 会执行同样的后端检查、后端测试和前端构建。`main` 上的前端构建会保留 7 天构建产物。仓库还没有生产部署环境，所以流水线到构建产物为止。

## Git 分支规范

⚠️ **重要：请严格遵守分支规范**

### 分支说明
- **main** - 生产部署分支，只用于发布，禁止直接提交
- **feature** - 开发主分支，所有开发从这里开始
- **feat/xxx** - 功能开发分支，从 feature 分支切出

### 工作流程

1. **开始新功能**
   ```bash
   git checkout feature
   git pull origin feature
   git checkout -b feat/your-feature-name
   ```

2. **开发提交**
   ```bash
   git add .
   git commit -m "feat: 添加xxx功能"
   ```

3. **合并到 feature**
   ```bash
   git checkout feature
   git merge feat/your-feature-name
   git push origin feature
   ```

4. **发布到 main**（由维护者操作）
   ```bash
   git checkout main
   git merge feature
   git tag v1.0.0
   git push origin main --tags
   ```

### 提交规范

使用语义化提交信息：

- `feat:` 新功能
- `fix:` 修复bug
- `docs:` 文档更新
- `style:` 代码格式调整
- `refactor:` 重构
- `test:` 测试相关
- `chore:` 构建配置等

示例：
```
feat: 添加文件上传拖拽功能
fix: 修复聊天界面滚动问题
docs: 更新README
style: 优化输入框样式
```

## 功能模块

### 1. 文件上传
- 拖拽上传
- 文件列表管理
- 上传进度显示
- 支持的文件类型：PDF、TXT、MD、DOCX

### 2. 聊天界面
- 实时对话
- 消息历史
- 来源引用显示
- 打字指示器

## 开发指南

详细的开发指南请参考：
- [CLAUDE.md](./CLAUDE.md) - 项目技术文档
- [AGENT.md](./AGENT.md) - AI Agent 工作规范

## 当前状态

- [x] 前端基础框架搭建
- [x] 文件上传界面
- [x] 聊天界面
- [x] Git 分支规范
- [ ] 后端 API 开发
- [ ] 向量化服务
- [ ] 检索服务
- [ ] 前后端联调

## License

MIT
