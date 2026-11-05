# RAG 项目技术文档

## 项目概述

RAG (Retrieval-Augmented Generation) 智能对话系统，通过向量检索技术增强大语言模型的回答准确性。

### 核心功能
1. **文档向量化** - 上传文档并转换为向量存储
2. **智能检索对话** - 基于向量检索的上下文增强对话

## 技术栈

### 前端技术
- **框架**：Vue 3 + Vite
- **构建工具**：Vite
- **样式方案**：原生 CSS + CSS Grid
- **主题风格**：淡色调、简洁风格
- **布局模式**：左侧固定导航（280px） + 右侧自适应内容区

### 后端技术（规划中）
- **语言**：Python 3.10+
- **Web 框架**：FastAPI
- **向量数据库**：Chroma / Pinecone / Milvus（待选型）
- **LLM 接口**：Anthropic Claude API / OpenAI API
- **文档处理**：LangChain / LlamaIndex
- **向量模型**：sentence-transformers
- **任务队列**：Celery + Redis / RQ（异步处理）
- **消息代理**：Redis
- **进度通知**：WebSocket / SSE（Server-Sent Events）

## Git 工作流规范

### 分支策略

```
main (生产环境)
  ↑
  └─ feature (集成分支)
       ↑
       ├─ feat/file-upload
       ├─ feat/vector-search
       └─ feat/chat-interface
```

#### 分支说明
| 分支类型 | 用途 | 保护级别 |
|---------|------|---------|
| `main` | 生产部署分支 | 🔒 禁止直接推送 |
| `feature` | 开发主分支 | ⚠️ 需要 review |
| `feat/**` | 功能开发分支 | ✅ 自由提交 |

### 开发流程

**1. 开始新功能**
```bash
git checkout feature
git pull origin feature
git checkout -b feat/功能简称
```

**2. 开发与提交**
```bash
git add .
git commit -m "feat: 添加文件上传功能"
git push origin feat/功能简称
```

**3. 合并到 feature**
```bash
git checkout feature
git merge feat/功能简称
git push origin feature
```

**4. 发布到生产（仅负责人）**
```bash
git checkout main
git merge feature
git push origin main
```

### Commit Message 规范

格式：`<type>: <subject>`

| Type | 说明 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat: 添加文件拖拽上传` |
| `fix` | Bug 修复 | `fix: 修复上传进度计算错误` |
| `style` | 样式调整 | `style: 优化按钮圆角` |
| `refactor` | 代码重构 | `refactor: 抽取向量搜索逻辑` |
| `perf` | 性能优化 | `perf: 优化大文件上传性能` |
| `docs` | 文档更新 | `docs: 更新 API 接口文档` |
| `test` | 测试相关 | `test: 添加上传组件单元测试` |
| `chore` | 构建/工具 | `chore: 升级 Vite 到 5.0` |

## 飞书项目管理规范

### 资源链接

| 资源 | 链接 | 用途 |
|------|------|------|
| 多维表格 | [打开表格](https://ocn0mresg7ps.feishu.cn/base/JVnnbUcNEaSkvqs7XhTc8ZbKnjf?table=tblDay8wKWi3Vjpf&view=vewhWaSvDl) | 任务管理、状态跟踪 |
| 知识库 | [打开知识库](https://ocn0mresg7ps.feishu.cn/wiki/Glekw2U3giVrOukDOcIcVkCPndf) | 技术文档、设计方案 |

### 任务状态流转

```
待开发 → 开发中 → 开发完成 → 已上线
  ↓        ↓         ↓          ↓
创建任务  开始开发  提交PR    部署生产
```

| 状态 | 说明 | 操作要求 |
|------|------|---------|
| **待开发** | 任务已创建，等待认领 | 无需操作 |
| **开发中** | 正在开发 | 开始开发时更新状态 |
| **开发完成** | 已提交 PR，等待审查 | 填写 PR 链接 |
| **已上线** | 已部署到生产环境 | 由负责人操作 |

### Agent 自动化流程

收到任务标题后，自动执行：

1. ✅ 更新飞书表格状态 → **开发中**
2. ✅ 创建功能分支 `feat/任务名`
3. ✅ 实现功能并提交代码
4. ✅ 合并到 `feature` 分支
5. ✅ 创建 PR（feature → main）
6. ✅ 更新飞书表格：填写 PR 链接，状态 → **开发完成**
7. ✅ 如有必要，记录设计文档（流程：先写本地 Markdown + SVG 配图 → 用户审核 → 审核通过后再上传飞书知识库）

## 项目结构

```
rag/                                # 仓库根目录
├── frontend/                       # 前端项目
│   ├── src/
│   │   ├── components/
│   │   │   ├── FileUpload.vue      # 文件上传组件
│   │   │   └── ChatInterface.vue   # 聊天界面组件
│   │   ├── App.vue                 # 主应用
│   │   ├── main.js                 # 入口文件
│   │   └── style.css               # 全局样式
│   ├── public/
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
│
├── backend/                        # 后端项目（规划中）
│   ├── api/                        # API 接口
│   ├── services/                   # 业务逻辑
│   ├── models/                     # 数据模型
│   ├── main.py                     # FastAPI 入口
│   └── requirements.txt
│
├── CLAUDE.md                       # 项目技术文档（本文件）
├── AGENT.md                        # Agent 工作规范
└── README.md                       # 项目说明
```

## UI 设计规范

### 设计系统 Token

#### 色彩系统（淡色调）

**主题色**
```css
--primary: #38BDF8;     /* 天蓝色 */
```

**表面层级**（浅灰色调）
```css
--surface-0: #FFFFFF;   /* 纯白背景 */
--surface-1: #F8FAFC;   /* 应用背景 */
--surface-2: #F1F5F9;   /* 卡片背景 */
--surface-3: #E2E8F0;   /* 次级背景 */
```

**文字层级**
```css
--text-primary: #1E293B;    /* 主要文字 */
--text-secondary: #64748B;  /* 次要文字 */
--text-tertiary: #94A3B8;   /* 辅助文字 */
```

**边框**
```css
--border: #E2E8F0;
```

**状态色**
```css
--success: #10B981;
--warning: #F59E0B;
--error: #EF4444;
--info: #3B82F6;
```

#### 间距系统

```css
--space-xs: 4px;
--space-sm: 8px;
--space-md: 16px;
--space-lg: 24px;
--space-xl: 32px;
```

#### 圆角系统

```css
--radius-sm: 4px;       /* 小组件 */
--radius-md: 8px;       /* 卡片、容器 */
--radius-lg: 12px;      /* 大面板 */
```

#### 字体系统

```css
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
--font-size-sm: 14px;
--font-size-base: 16px;
--font-size-lg: 18px;
```

### 组件设计原则

#### 基础组件
- **按钮**：圆角 8px，无 hover 效果
- **输入框**：圆角 8px，边框样式
- **卡片**：圆角 8px，浅灰背景
- **布局**：使用 CSS Grid，左侧导航 280px

#### Alert 提示组件（重要）

**强制规范：禁止使用浏览器原生 `alert()`、`confirm()`、`prompt()`**

所有提示信息必须使用自定义 Alert 组件，显示在页面中央，按严重程度使用不同样式：

| 类型 | 颜色 | 用途 | 示例 |
|------|------|------|------|
| `success` | `#10B981` 绿色 | 操作成功 | "文件上传成功" |
| `error` | `#EF4444` 红色 | 错误、失败 | "文件上传失败" |
| `warning` | `#F59E0B` 橙色 | 警告、确认 | "确定要删除这个文件吗？" |
| `info` | `#3B82F6` 蓝色 | 提示信息 | "正在处理中..." |

**Alert 组件规范：**
```vue
<Alert
  :type="'success' | 'error' | 'warning' | 'info'"
  :message="提示文字"
  :show="布尔值"
  @close="关闭回调"
  :duration="自动关闭时间(ms)，默认3000"
/>
```

**样式要求：**
- 固定定位在页面中央（垂直水平居中）
- 圆角 8px
- 左侧色条（4px 宽）+ 图标 + 文字
- 半透明背景遮罩
- 支持手动关闭（右上角关闭按钮）
- 支持自动关闭（可配置时长）

### 交互原则

- 不使用 hover 效果
- 不使用过渡动画
- 简洁直接的交互反馈

#### 图标管理
所有图标统一使用 SVG 组件，存放在 `frontend/src/icons/` 目录：

**通用图标**
- `IconSuccess.vue` - 成功图标（打勾圆圈）
- `IconError.vue` - 错误图标（叉号圆圈）
- `IconWarning.vue` - 警告图标（感叹号三角形）
- `IconInfo.vue` - 信息图标（感叹号圆圈）
- `IconClose.vue` - 关闭图标（叉号）

**文件类型图标**
- `IconFilePdf.vue` - PDF 文件图标（红色）
- `IconFileWord.vue` - Word 文档图标（蓝色）
- `IconFileExcel.vue` - Excel 表格图标（绿色）
- `IconFileMarkdown.vue` - Markdown 文件图标（紫色）
- `IconFileCsv.vue` - CSV 文件图标（橙色）
- `IconFileText.vue` - 纯文本文件图标（灰色）

#### Toast 提示框（页面顶部）
- 位置：页面顶部居中，距离顶部 20px
- 用途：成功、失败、警告、信息提示
- 交互：自动消失（默认 3 秒），可手动关闭
- 场景：上传成功、操作失败、信息通知等
- 样式特征：
  - 彩色图标（左侧）
  - 文字内容
  - 关闭按钮（右侧）
  - 从顶部滑入动画
  - **不使用左侧色条**

**Toast 使用规范**
```vue
<Toast
  type="success"      <!-- success | error | warning | info -->
  message="操作成功"
  :show="true"
  :duration="3000"    <!-- 自动关闭时间，毫秒 -->
  @close="handleClose"
/>
```

#### Confirm 确认框（页面正中）
- 位置：页面正中，遮罩背景
- 用途：需要用户确认的重要操作
- 交互：必须用户选择"确认"或"取消"
- 场景：删除文件、取消上传、退出登录等
- 样式特征：
  - 顶部彩色图标（圆形背景）
  - 标题 + 描述文字
  - 取消 + 确认按钮
  - 缩放进入动画
  - 半透明遮罩
  - **不使用边框色条**

**Confirm 使用规范**
```vue
<Confirm
  title="确认删除"
  message="确定要删除该文件吗？此操作不可恢复。"
  type="danger"       <!-- warning | danger | info -->
  :show="true"
  confirmText="确认"
  cancelText="取消"
  @confirm="handleConfirm"
  @cancel="handleCancel"
/>
```

**重要规范**
- ❌ **禁止使用浏览器原生弹窗**：`alert()`、`confirm()`、`prompt()`
- ✅ **提示类消息使用 Toast**：成功/失败提示、信息通知
- ✅ **确认类操作使用 Confirm**：删除、取消、退出等重要操作
- ✅ **位置严格区分**：Toast 在顶部，Confirm 在中央
- ✅ **类型选择**：
  - Toast: `success`（成功）、`error`（失败）、`warning`（警告）、`info`（信息）
  - Confirm: `warning`（警告操作）、`danger`（危险操作）、`info`（信息确认）

#### Pagination 分页组件
用于文件列表、搜索结果等长列表的分页展示。

**分页组件规范**
```vue
<Pagination
  :currentPage="1"
  :totalPages="10"
  :totalItems="95"
  @page-change="handlePageChange"
/>
```

**功能特性：**
- 上一页/下一页按钮
- 页码快速跳转（首页、末页、当前页周围）
- 总数统计显示
- 自动禁用边界按钮
- 页码省略显示（超过 7 页时）

**显示逻辑：**
- 总页数 ≤ 7：显示所有页码
- 总页数 > 7：显示首页、末页、当前页及其周围页码，其他用 "..." 省略
- 当前页高亮显示（主题色背景）

#### 文件管理列表
文件管理界面展示用户上传的文档，支持分页浏览和文件操作。

**列表结构：**
```
文件名 | 类型 | 大小 | 分块数 | 上传时间 | 操作人 | 操作
```

**列宽分配（Grid）：**
```css
grid-template-columns: 2fr 100px 100px 100px 140px 150px 100px;
```

**文件类型徽章样式：**
- PDF - 红色边框 `#EF4444`
- Word - 蓝色边框 `#3B82F6`
- Excel - 绿色边框 `#10B981`
- Markdown - 紫色边框 `#A855F7`
- CSV - 橙色边框 `#F59E0B`
- Text - 灰色边框 `#6B7280`

**操作人显示：**
- 圆形头像（28px）：显示用户名首字母
- 头像颜色：主题色背景 `#38BDF8`
- 旁边显示完整用户名

**操作按钮：**
- 删除按钮：hover 时红色边框和背景
- 按钮尺寸：32x32px
- 图标尺寸：16x16px

## 开发指南

### 环境要求

**前端**
- Node.js >= 18.0.0
- npm >= 9.0.0

**后端（规划中）**
- Python >= 3.10
- pip >= 23.0

### 快速开始

#### 前端开发

```bash
# 安装依赖
cd frontend
npm install

# 启动开发服务器
npm run dev
# 访问 http://localhost:5173

# 构建生产版本
npm run build
```

### 代码规范

#### 前端

**Vue 组件结构**
```vue
<script setup>
// 1. imports
// 2. props/emits
// 3. reactive state
// 4. computed
// 5. methods
// 6. lifecycle hooks
</script>

<template>
  <!-- 模板内容 -->
</template>

<style scoped>
/* 组件样式 */
</style>
```

**命名规范**
- 组件文件：PascalCase（`FileUpload.vue`）
- 组件名：PascalCase（`<FileUpload />`）
- 变量/函数：camelCase（`handleUpload`）
- CSS 类名：kebab-case（`.file-upload-button`）
- 常量：UPPER_SNAKE_CASE（`MAX_FILE_SIZE`）

#### 后端（规划中）

**Python 代码风格**
- 遵循 PEP 8
- 使用 type hints
- 最大行长度：88（Black 默认）

**命名规范**
- 文件/模块：snake_case（`text_splitter.py`）
- 类名：PascalCase（`VectorStore`）
- 函数/变量：snake_case（`get_embeddings`）
- 常量：UPPER_SNAKE_CASE（`MAX_CHUNK_SIZE`）

## 异步上传处理方案

### 业务场景

大文件上传后需要经过耗时的处理流程：
1. 文件解析（PDF、Word 等格式）
2. 文本分块（按语义切分）
3. 向量化（调用 embedding 模型）
4. 向量存储（写入向量数据库）

这些操作可能需要数十秒甚至数分钟，不能让前端同步等待。

### 架构设计

#### 方案对比

| 方案 | 技术栈 | 优点 | 缺点 | 推荐场景 |
|------|--------|------|------|----------|
| **Celery + Redis** | Celery 分布式任务队列 | 成熟稳定、功能强大、支持分布式 | 依赖重、配置复杂 | 生产环境、大规模任务 |
| **RQ** | Python-RQ 轻量任务队列 | 简单易用、纯 Python | 功能较少、不支持复杂调度 | 小型项目、快速开发 |
| **FastAPI BackgroundTasks** | FastAPI 内置 | 无额外依赖、配置简单 | 不持久化、重启丢失任务 | 简单异步、MVP 阶段 |
| **asyncio + 数据库** | 纯 Python 异步 | 无外部依赖 | 需要自己实现队列逻辑 | 特殊场景 |

**推荐方案：**
- **MVP 阶段**：FastAPI BackgroundTasks（快速验证）
- **生产环境**：Celery + Redis（稳定可靠）

### 实现方案

#### 阶段一：FastAPI BackgroundTasks（MVP）

```python
# api/documents.py
from fastapi import BackgroundTasks, UploadFile
from services.document_processor import process_document

@app.post("/documents")
async def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks
):
    # 1. 保存文件到临时目录
    file_path = await save_upload_file(file)

    # 2. 创建任务记录
    task = await db.tasks.create({
        "filename": file.filename,
        "status": "pending",
        "progress": 0
    })

    # 3. 添加后台任务
    background_tasks.add_task(
        process_document,
        task_id=task.id,
        file_path=file_path
    )

    # 4. 立即返回
    return {
        "task_id": task.id,
        "status": "pending",
        "message": "文件上传成功，正在处理中"
    }

# 查询任务状态
@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    task = await db.tasks.get(task_id)
    return {
        "task_id": task.id,
        "status": task.status,  # pending/processing/completed/failed
        "progress": task.progress,  # 0-100
        "result": task.result,
        "error": task.error
    }
```

**前端轮询：**
```javascript
// 上传文件
const { task_id } = await uploadDocument(file)

// 轮询任务状态
const pollTaskStatus = setInterval(async () => {
  const { status, progress } = await getTaskStatus(task_id)

  if (status === 'completed') {
    clearInterval(pollTaskStatus)
    showToast('success', '文件处理完成')
  } else if (status === 'failed') {
    clearInterval(pollTaskStatus)
    showToast('error', '文件处理失败')
  } else {
    updateProgress(progress)
  }
}, 2000) // 每 2 秒轮询一次
```

**限制：**
- ❌ 服务重启后任务丢失
- ❌ 无法分布式部署
- ❌ 无任务优先级、重试机制
- ✅ 适合 MVP 快速验证

#### 阶段二：Celery + Redis（生产环境）

**架构图：**
```
前端上传文件
  ↓
FastAPI 接收文件，创建任务
  ↓
Celery 任务队列（Redis）
  ↓
Celery Worker 异步处理
  ↓
更新任务状态到数据库
  ↓
前端轮询/WebSocket 获取进度
```

**后端实现：**

```python
# celery_app.py
from celery import Celery

celery_app = Celery(
    "rag",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1"
)

celery_app.conf.update(
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='Asia/Shanghai',
    enable_utc=True,
)

# tasks/document_tasks.py
from celery_app import celery_app
from services.document_processor import DocumentProcessor

@celery_app.task(bind=True)
def process_document_task(self, task_id: str, file_path: str):
    """
    异步处理文档
    self: Celery task 实例，用于更新进度
    """
    processor = DocumentProcessor()

    try:
        # 更新状态：处理中
        self.update_state(
            state='PROCESSING',
            meta={'progress': 0, 'step': '解析文档'}
        )

        # 1. 解析文档
        text = processor.parse_document(file_path)
        self.update_state(
            state='PROCESSING',
            meta={'progress': 25, 'step': '文本分块'}
        )

        # 2. 文本分块
        chunks = processor.split_text(text)
        self.update_state(
            state='PROCESSING',
            meta={'progress': 50, 'step': '向量化'}
        )

        # 3. 向量化
        embeddings = processor.get_embeddings(chunks)
        self.update_state(
            state='PROCESSING',
            meta={'progress': 75, 'step': '存储向量'}
        )

        # 4. 存储向量
        document_id = processor.store_vectors(
            task_id, chunks, embeddings
        )

        # 5. 清理临时文件
        os.remove(file_path)

        return {
            'status': 'completed',
            'document_id': document_id,
            'chunks_count': len(chunks)
        }

    except Exception as e:
        # 失败处理
        self.update_state(
            state='FAILURE',
            meta={'error': str(e)}
        )
        raise

# api/documents.py
from tasks.document_tasks import process_document_task

@app.post("/documents")
async def upload_document(file: UploadFile):
    # 1. 保存文件
    file_path = await save_upload_file(file)

    # 2. 创建任务记录
    task = await db.tasks.create({
        "filename": file.filename,
        "status": "pending"
    })

    # 3. 提交 Celery 任务
    celery_task = process_document_task.apply_async(
        args=[task.id, file_path],
        task_id=task.id
    )

    return {
        "task_id": celery_task.id,
        "status": "pending"
    }

@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    # 从 Celery 获取任务状态
    task_result = celery_app.AsyncResult(task_id)

    if task_result.state == 'PENDING':
        response = {
            'status': 'pending',
            'progress': 0
        }
    elif task_result.state == 'PROCESSING':
        response = {
            'status': 'processing',
            'progress': task_result.info.get('progress', 0),
            'step': task_result.info.get('step', '')
        }
    elif task_result.state == 'SUCCESS':
        response = {
            'status': 'completed',
            'progress': 100,
            'result': task_result.result
        }
    elif task_result.state == 'FAILURE':
        response = {
            'status': 'failed',
            'error': str(task_result.info)
        }

    return response
```

**启动 Worker：**
```bash
# 启动 Celery Worker
celery -A celery_app worker --loglevel=info

# 启动 Flower（Web 监控界面）
celery -A celery_app flower
```

**优点：**
- ✅ 任务持久化（Redis 做 broker 和 backend）
- ✅ 支持分布式部署（多个 Worker）
- ✅ 自动重试、优先级队列
- ✅ 任务监控（Flower）
- ✅ 生产环境稳定可靠

#### 阶段三：WebSocket 实时推送（可选）

替代轮询，使用 WebSocket 实时推送进度：

```python
# api/websocket.py
from fastapi import WebSocket

@app.websocket("/ws/tasks/{task_id}")
async def task_progress_websocket(
    websocket: WebSocket,
    task_id: str
):
    await websocket.accept()

    try:
        while True:
            # 获取任务最新状态
            task_result = celery_app.AsyncResult(task_id)

            # 推送状态
            await websocket.send_json({
                'status': task_result.state,
                'progress': task_result.info.get('progress', 0),
                'step': task_result.info.get('step', '')
            })

            # 任务完成或失败，关闭连接
            if task_result.state in ['SUCCESS', 'FAILURE']:
                break

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
```

**前端 WebSocket：**
```javascript
const ws = new WebSocket(`ws://localhost:8000/ws/tasks/${taskId}`)

ws.onmessage = (event) => {
  const { status, progress, step } = JSON.parse(event.data)

  if (status === 'SUCCESS') {
    showToast('success', '处理完成')
    ws.close()
  } else {
    updateProgress(progress, step)
  }
}
```

### 数据库表结构

```sql
-- 任务表
CREATE TABLE tasks (
    id VARCHAR(36) PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    file_size BIGINT,
    status VARCHAR(20) NOT NULL,  -- pending/processing/completed/failed
    progress INT DEFAULT 0,
    step VARCHAR(100),
    document_id VARCHAR(36),
    chunks_count INT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 文档表
CREATE TABLE documents (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36),
    filename VARCHAR(255),
    file_type VARCHAR(20),
    chunks_count INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);
```

### 前端改造

**API 调用：**
```javascript
// api/tasks.js
export async function getTaskStatus(taskId) {
  return get(`/tasks/${taskId}`)
}

// composables/useUploadManager.js
async function startUpload(task) {
  task.status = UPLOAD_STATUS.UPLOADING

  try {
    // 1. 上传文件，获取 task_id
    const { task_id } = await uploadDocument(task.file, config.operator)
    task.taskId = task_id

    // 2. 轮询任务状态
    const pollInterval = setInterval(async () => {
      const result = await getTaskStatus(task_id)

      task.progress = result.progress
      task.step = result.step

      if (result.status === 'completed') {
        clearInterval(pollInterval)
        task.status = UPLOAD_STATUS.SUCCESS
        task.documentId = result.result.document_id
      } else if (result.status === 'failed') {
        clearInterval(pollInterval)
        task.status = UPLOAD_STATUS.ERROR
        task.error = result.error
      }
    }, 2000)

  } catch (error) {
    task.status = UPLOAD_STATUS.ERROR
    task.error = error.message
  }
}
```

### 部署架构

```
┌─────────────┐
│   Nginx     │  反向代理
└──────┬──────┘
       │
┌──────▼──────┐
│   FastAPI   │  Web 服务（多实例）
└──────┬──────┘
       │
┌──────▼──────┐
│    Redis    │  消息队列 + 任务状态存储
└──────┬──────┘
       │
┌──────▼──────┐
│   Celery    │  异步 Worker（多实例）
│   Worker    │
└──────┬──────┘
       │
┌──────▼──────┐
│   Chroma    │  向量数据库
└─────────────┘
```

### 监控与运维

**Celery Flower 监控：**
- 访问 `http://localhost:5555`
- 查看任务队列、执行状态、Worker 状态
- 手动重试失败任务

**日志记录：**
```python
import logging

logger = logging.getLogger(__name__)

@celery_app.task(bind=True)
def process_document_task(self, task_id: str, file_path: str):
    logger.info(f"开始处理文档: {task_id}")

    try:
        # 处理逻辑
        logger.info(f"文档处理完成: {task_id}")
    except Exception as e:
        logger.error(f"文档处理失败: {task_id}, 错误: {str(e)}")
        raise
```

### 性能优化

1. **批量处理** - 多个文档合并成一个批次向量化
2. **Worker 并发** - 根据服务器资源调整 Worker 数量
3. **任务优先级** - 小文件优先处理
4. **缓存** - 相同文件检测（文件 hash），避免重复处理
5. **限流** - 控制上传频率，防止队列堵塞
