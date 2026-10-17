# 前端异步上传测试文档

## 改动内容

### 1. API 层
- ✅ 添加 `api/tasks.js` - 任务状态查询 API
- ✅ 添加 `api/documents.js` - `getTaskStatus()` 函数

### 2. 上传管理器 (`useUploadManager.js`)
- ✅ 添加 `TASK_STAGE` 常量（upload / process）
- ✅ 任务对象新增字段：
  - `taskId` - 后端任务 ID
  - `stage` - 当前处理阶段
  - `step` - 当前步骤描述
  - `objectKey` - 对象存储 key
  - `fileUrl` - 文件访问 URL
  - `pollInterval` - 轮询定时器
- ✅ 移除字段：
  - `abortController` - 不再需要前端取消请求
- ✅ `startUpload()` 改为轮询模式
- ✅ `pauseTask()` / `cancelTask()` / `pauseAll()` 适配轮询

### 3. UI 组件 (`UploadPanel.vue`)
- ✅ 显示 `task.step` - 当前处理步骤
- ✅ 样式：`.task-step` - 灰色小字显示步骤

## 工作流程

```
用户选择文件
  ↓
前端调用 uploadDocument(file)
  ↓
后端返回 { task_id, status: 'pending', message: '...' }
  ↓
前端开始轮询 getTaskStatus(task_id)
  ↓
后端返回 { status, progress, step, stage, result }
  ↓
前端更新 UI（进度条、步骤描述）
  ↓
status = 'completed' → 显示成功，保存 object_key 和 file_url
status = 'failed' → 显示错误信息
```

## 测试步骤

### 前端测试（不需要后端）
1. 启动前端：`npm run dev`
2. 访问：http://localhost:5176
3. 选择文件上传
4. 查看上传面板：
   - ✅ 文件名、大小
   - ✅ 状态（等待中、上传中、已完成、失败）
   - ✅ 进度条
   - ✅ 步骤描述（如果有）
   - ✅ 操作按钮（暂停、恢复、重试、取消）

### 联调测试（需要后端）
1. 后端启动：
   - Redis: `redis-server`
   - Celery Worker: `celery -A celery_app worker --loglevel=info`
   - FastAPI: `uvicorn main:app --reload`
2. 前端上传文件
3. 观察轮询日志（浏览器控制台）
4. 查看后端日志（Celery Worker 输出）
5. 验证上传成功后的 `object_key` 和 `file_url`

## Mock 数据（用于测试）

如果后端未就绪，可以临时 mock `getTaskStatus` API：

```javascript
// api/tasks.js (临时 mock)
export function getTaskStatus(taskId) {
  // 模拟不同阶段的返回
  return new Promise((resolve) => {
    setTimeout(() => {
      const progress = Math.min((Date.now() % 100000) / 1000, 100)
      resolve({
        task_id: taskId,
        status: progress < 100 ? 'uploading' : 'completed',
        stage: 'upload',
        progress: Math.floor(progress),
        step: progress < 30 ? '正在上传...' :
              progress < 60 ? '正在校验文件...' :
              progress < 90 ? '正在存储到 OSS...' : '上传完成',
        result: progress >= 100 ? {
          object_key: 'uploads/2024/10/test-file.pdf',
          file_url: 'https://oss.example.com/uploads/2024/10/test-file.pdf'
        } : null
      })
    }, 500)
  })
}
```

## 预期效果

### 上传中
```
┌─────────────────────────────────────┐
│ 📄 report.pdf                       │
│ 2.5 MB · 上传中 · 45%                │
│ 正在存储到 OSS...                    │
│ ████████████░░░░░░░░░░░░░           │
│                      [⏸] [✕]        │
└─────────────────────────────────────┘
```

### 上传成功
```
┌─────────────────────────────────────┐
│ 📄 report.pdf                       │
│ 2.5 MB · 已完成                      │
│                               [✕]   │
└─────────────────────────────────────┘
```

### 上传失败
```
┌─────────────────────────────────────┐
│ 📄 report.pdf                       │
│ 2.5 MB · 失败                        │
│ 文件格式不支持                       │
│                          [↻] [✕]    │
└─────────────────────────────────────┘
```

## 下一步

- [ ] 后端实现 Celery + Redis 上传任务
- [ ] 联调测试上传流程
- [ ] 添加 WebSocket 实时推送（可选，替代轮询）
- [ ] 文件管理列表显示 `file_url`（下载功能）
