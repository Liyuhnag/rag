# 前端适配异步上传架构 - 改动说明

## 概述

前端从**同步上传+前端进度条**模式改为**异步上传+轮询状态**模式，适配后端 Celery + Redis 异步任务架构。

## 核心改动

### 1. 新增 API 接口

#### `src/api/tasks.js` （新建）
```javascript
/**
 * 查询任务状态
 * @param {string} taskId - 任务 ID
 * @returns {Promise<Object>}
 */
export function getTaskStatus(taskId) {
  return get(`/tasks/${taskId}`)
}
```

**返回数据格式：**
```javascript
{
  task_id: "uuid",
  status: "pending" | "uploading" | "completed" | "failed",
  stage: "upload" | "process",
  progress: 0-100,
  step: "当前步骤描述",
  result: {
    object_key: "uploads/2024/10/file.pdf",
    file_url: "https://oss.example.com/..."
  },
  error: "错误信息"
}
```

#### `src/api/documents.js` （新增导出）
```javascript
export function getTaskStatus(taskId) {
  return get(`/tasks/${taskId}`)
}
```

### 2. 上传管理器改造

#### `src/composables/useUploadManager.js`

**新增常量：**
```javascript
// 任务处理阶段
export const TASK_STAGE = {
  UPLOAD: 'upload',       // 第一阶段：上传到对象存储
  PROCESS: 'process'      // 第二阶段：文档处理（预留）
}
```

**任务对象结构变化：**
```javascript
const task = {
  // 原有字段
  id: 'task-xxx',
  file: File,
  name: 'report.pdf',
  type: 'pdf',
  size: 2621440,
  sizeText: '2.5 MB',
  status: UPLOAD_STATUS.PENDING,
  progress: 0,
  error: null,
  uploadedAt: null,

  // 新增字段
  taskId: null,           // ✅ 后端任务 ID
  stage: TASK_STAGE.UPLOAD, // ✅ 当前处理阶段
  step: '',               // ✅ 当前步骤描述
  objectKey: null,        // ✅ 对象存储 key
  fileUrl: null,          // ✅ 文件访问 URL
  pollInterval: null,     // ✅ 轮询定时器

  // 移除字段
  // documentId: null,    // ❌ 删除（改用 taskId）
  // abortController: null // ❌ 删除（不再需要前端取消）
}
```

**核心方法改造：**

##### `startUpload(task)` - 从同步改为异步轮询

**之前（同步模式）：**
```javascript
async function startUpload(task) {
  task.status = UPLOAD_STATUS.UPLOADING
  task.abortController = new AbortController()

  try {
    const result = await uploadDocument(
      task.file,
      config.operator,
      (progress) => {
        task.progress = progress  // 前端进度回调
      }
    )
    task.status = UPLOAD_STATUS.SUCCESS
    task.progress = 100
  } catch (error) {
    task.status = UPLOAD_STATUS.ERROR
    task.error = error.message
  }
}
```

**现在（异步轮询模式）：**
```javascript
async function startUpload(task) {
  task.status = UPLOAD_STATUS.UPLOADING
  task.stage = TASK_STAGE.UPLOAD
  task.step = '准备上传...'

  try {
    // 1. 上传文件，立即返回 task_id
    const result = await uploadDocument(
      task.file,
      config.operator,
      null  // 不使用前端进度回调
    )

    task.taskId = result.task_id
    task.step = result.message || '等待处理...'

    // 2. 开始轮询任务状态（每 2 秒一次）
    task.pollInterval = setInterval(async () => {
      try {
        const status = await getTaskStatus(result.task_id)

        // 更新进度信息
        task.progress = status.progress || 0
        task.step = status.step || ''
        task.stage = status.stage || TASK_STAGE.UPLOAD

        // 判断任务状态
        if (status.status === 'completed') {
          // 上传成功
          clearInterval(task.pollInterval)
          task.pollInterval = null
          task.status = UPLOAD_STATUS.SUCCESS
          task.progress = 100

          // 保存对象存储信息
          if (status.result) {
            task.objectKey = status.result.object_key
            task.fileUrl = status.result.file_url
          }

          processQueue()  // 继续下一个任务

        } else if (status.status === 'failed') {
          // 上传失败
          clearInterval(task.pollInterval)
          task.pollInterval = null
          task.status = UPLOAD_STATUS.ERROR
          task.error = status.error || '处理失败'

          processQueue()
        }

      } catch (error) {
        // 查询失败，继续轮询（可能是网络波动）
        console.error('查询任务状态失败:', error)
      }
    }, 2000)

  } catch (error) {
    task.status = UPLOAD_STATUS.ERROR
    task.error = error.message || '上传失败'
    processQueue()
  }
}
```

##### `pauseTask(taskId)` - 适配轮询模式

**之前：**
```javascript
function pauseTask(taskId) {
  const task = uploadTasks.value.find(t => t.id === taskId)
  if (task && task.abortController) {
    task.abortController.abort()  // 取消 HTTP 请求
  }
  task.status = UPLOAD_STATUS.PAUSED
}
```

**现在：**
```javascript
function pauseTask(taskId) {
  const task = uploadTasks.value.find(t => t.id === taskId)
  if (task && task.pollInterval) {
    clearInterval(task.pollInterval)  // 停止轮询
    task.pollInterval = null
  }
  task.status = UPLOAD_STATUS.PAUSED
  processQueue()
}
```

##### `cancelTask(taskId)` - 适配轮询模式

**之前：**
```javascript
function cancelTask(taskId) {
  const task = uploadTasks.value.find(t => t.id === taskId)
  if (task && task.abortController) {
    task.abortController.abort()
  }
  // 从列表移除
  uploadTasks.value = uploadTasks.value.filter(t => t.id !== taskId)
}
```

**现在：**
```javascript
function cancelTask(taskId) {
  const task = uploadTasks.value.find(t => t.id === taskId)
  if (task && task.pollInterval) {
    clearInterval(task.pollInterval)  // 停止轮询
    task.pollInterval = null
  }
  // 从列表移除
  uploadTasks.value = uploadTasks.value.filter(t => t.id !== taskId)
  processQueue()
}
```

### 3. UI 组件改造

#### `src/components/UploadPanel.vue`

**新增步骤显示：**
```vue
<div class="task-info">
  <div class="task-name">{{ task.name }}</div>
  <div class="task-meta">
    <span class="task-size">{{ task.sizeText }}</span>
    <span class="task-status">{{ getStatusText(task.status) }}</span>
    <span v-if="task.status === UPLOAD_STATUS.UPLOADING">
      {{ task.progress }}%
    </span>
  </div>

  <!-- ✅ 新增：处理步骤 -->
  <div v-if="task.step && task.status === UPLOAD_STATUS.UPLOADING" class="task-step">
    {{ task.step }}
  </div>

  <!-- 错误信息 -->
  <div v-if="task.error" class="task-error">
    {{ task.error }}
  </div>
</div>
```

**新增样式：**
```css
.task-step {
  font-size: 12px;
  color: var(--text-tertiary);
}
```

## 工作流程对比

### 之前（同步模式）

```
选择文件 → 调用 uploadDocument() →
  ↓
前端 XMLHttpRequest.upload.onprogress
  ↓
实时更新进度条 (0% → 100%)
  ↓
返回 { id, filename, ... } → 显示成功
```

**问题：**
- HTTP 请求阻塞等待后端处理完成
- 大文件处理时间长，容易超时
- 后端需要同步完成所有操作才能返回

### 现在（异步模式）

```
选择文件 → 调用 uploadDocument() →
  ↓
后端立即返回 { task_id, status: 'pending', message: '...' }
  ↓
前端开始轮询 getTaskStatus(task_id)（每 2 秒）
  ↓
后端返回 { status, progress, step, stage, result }
  ↓
前端更新 UI（进度条、步骤描述）
  ↓
status = 'completed' → 保存结果，显示成功
status = 'failed' → 显示错误
```

**优势：**
- HTTP 请求立即返回，不阻塞
- 后端异步处理，不受超时限制
- 前端可显示详细处理步骤
- 用户可随时暂停、取消

## 后端 API 契约

### 上传接口

**请求：** `POST /api/documents`
```
Content-Type: multipart/form-data

file: <binary>
operator: "张三"
```

**响应：**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "filename": "report.pdf",
  "status": "pending",
  "message": "文件上传成功，正在处理中"
}
```

### 任务状态查询接口

**请求：** `GET /api/tasks/{task_id}`

**响应（处理中）：**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "uploading",
  "stage": "upload",
  "progress": 45,
  "step": "正在存储到 OSS..."
}
```

**响应（成功）：**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "stage": "upload",
  "progress": 100,
  "step": "上传完成",
  "result": {
    "object_key": "uploads/2024/10/17/report.pdf",
    "file_url": "https://oss.example.com/uploads/2024/10/17/report.pdf",
    "file_size": 2621440,
    "file_type": "pdf"
  }
}
```

**响应（失败）：**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "stage": "upload",
  "progress": 0,
  "error": "文件格式不支持"
}
```

## 测试验证

### 1. 前端单独测试

```bash
cd frontend
npm run dev
```

访问 http://localhost:5173，选择文件上传：
- ✅ 文件添加到上传队列
- ✅ 显示文件名、大小
- ✅ 状态切换正确
- ✅ 进度条显示
- ✅ 操作按钮可用（暂停、恢复、重试、取消）

### 2. Mock 测试（不需要后端）

临时 mock `api/tasks.js`：
```javascript
export function getTaskStatus(taskId) {
  const progress = Math.min((Date.now() % 50000) / 500, 100)
  return Promise.resolve({
    task_id: taskId,
    status: progress < 100 ? 'uploading' : 'completed',
    stage: 'upload',
    progress: Math.floor(progress),
    step: progress < 30 ? '正在上传...' :
          progress < 60 ? '正在校验文件...' :
          progress < 90 ? '正在存储到 OSS...' : '上传完成',
    result: progress >= 100 ? {
      object_key: 'uploads/test.pdf',
      file_url: 'https://oss.example.com/test.pdf'
    } : null
  })
}
```

### 3. 联调测试（需要后端）

**启动后端：**
```bash
# 1. Redis
redis-server

# 2. Celery Worker
cd backend
celery -A celery_app worker --loglevel=info

# 3. FastAPI
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**测试步骤：**
1. 前端上传文件
2. 浏览器控制台查看轮询日志
3. Celery Worker 查看任务执行日志
4. 验证上传成功后的 `object_key` 和 `file_url`

## 预期效果

### 上传中
```
┌──────────────────────────────────────┐
│ 📄 report.pdf                        │
│ 2.5 MB · 上传中 · 45%                 │
│ 正在存储到 OSS...                     │
│ ████████████░░░░░░░░░░░░░            │
│                       [⏸] [✕]        │
└──────────────────────────────────────┘
```

### 上传成功
```
┌──────────────────────────────────────┐
│ 📄 report.pdf                        │
│ 2.5 MB · 已完成                       │
│                                [✕]   │
└──────────────────────────────────────┘
```

### 上传失败
```
┌──────────────────────────────────────┐
│ 📄 report.pdf                        │
│ 2.5 MB · 失败                         │
│ 文件格式不支持                        │
│                           [↻] [✕]    │
└──────────────────────────────────────┘
```

## 注意事项

1. **轮询间隔**：当前设置为 2 秒，可根据实际情况调整
2. **轮询清理**：组件卸载时需清理所有 `pollInterval`
3. **错误重试**：轮询失败时不中断，继续等待下一次轮询
4. **断线重连**：前端刷新后无法恢复轮询状态，需重新上传
5. **并发限制**：保持最多 3 个并发上传任务

## 后续优化

- [ ] 添加 WebSocket 实时推送（替代轮询）
- [ ] 断点续传支持
- [ ] 大文件分片上传
- [ ] 上传状态持久化（localStorage）
- [ ] 上传速度显示
- [ ] 剩余时间预估
