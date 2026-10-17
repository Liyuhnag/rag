<script setup>
import { ref, computed } from 'vue'
import { useUploadManager, UPLOAD_STATUS } from '../composables/useUploadManager.js'

const {
  uploadTasks,
  activeUploads,
  hasActiveUploads,
  pauseTask,
  resumeTask,
  retryTask,
  cancelTask,
  clearCompleted,
  pauseAll,
  resumeAll
} = useUploadManager()

// 面板展开状态
const isExpanded = ref(true)

// 面板是否可见
const isVisible = computed(() => uploadTasks.value.length > 0)

// 统计信息
const stats = computed(() => {
  const pending = uploadTasks.value.filter(t => t.status === UPLOAD_STATUS.PENDING).length
  const uploading = uploadTasks.value.filter(t => t.status === UPLOAD_STATUS.UPLOADING).length
  const success = uploadTasks.value.filter(t => t.status === UPLOAD_STATUS.SUCCESS).length
  const error = uploadTasks.value.filter(t => t.status === UPLOAD_STATUS.ERROR).length
  const paused = uploadTasks.value.filter(t => t.status === UPLOAD_STATUS.PAUSED).length

  return { pending, uploading, success, error, paused }
})

// 任务标题
const taskTitle = computed(() => {
  const { uploading, pending, success, error } = stats.value
  if (uploading > 0 || pending > 0) {
    return `正在上传 ${uploading + pending} 个文件`
  }
  if (success > 0 && error === 0) {
    return `成功上传 ${success} 个文件`
  }
  if (error > 0) {
    return `${success} 个成功，${error} 个失败`
  }
  return '上传任务'
})

// 切换面板展开状态
const togglePanel = () => {
  isExpanded.value = !isExpanded.value
}

// 获取任务状态文本
const getStatusText = (status) => {
  const statusMap = {
    [UPLOAD_STATUS.PENDING]: '等待中',
    [UPLOAD_STATUS.UPLOADING]: '上传中',
    [UPLOAD_STATUS.PAUSED]: '已暂停',
    [UPLOAD_STATUS.SUCCESS]: '已完成',
    [UPLOAD_STATUS.ERROR]: '失败'
  }
  return statusMap[status] || '未知'
}

// 获取任务状态颜色
const getStatusColor = (status) => {
  const colorMap = {
    [UPLOAD_STATUS.PENDING]: '#94A3B8',
    [UPLOAD_STATUS.UPLOADING]: '#38BDF8',
    [UPLOAD_STATUS.PAUSED]: '#F59E0B',
    [UPLOAD_STATUS.SUCCESS]: '#10B981',
    [UPLOAD_STATUS.ERROR]: '#EF4444'
  }
  return colorMap[status] || '#94A3B8'
}

// 是否显示全部暂停按钮
const canPauseAll = computed(() => {
  return uploadTasks.value.some(t =>
    t.status === UPLOAD_STATUS.UPLOADING || t.status === UPLOAD_STATUS.PENDING
  )
})

// 是否显示全部恢复按钮
const canResumeAll = computed(() => {
  return uploadTasks.value.some(t => t.status === UPLOAD_STATUS.PAUSED)
})
</script>

<template>
  <div v-if="isVisible" class="upload-panel" :class="{ collapsed: !isExpanded }">
    <!-- 面板头部 -->
    <div class="panel-header" @click="togglePanel">
      <div class="header-left">
        <div
          class="status-indicator"
          :class="{ active: hasActiveUploads }"
        ></div>
        <span class="panel-title">{{ taskTitle }}</span>
      </div>

      <div class="header-right">
        <!-- 全部暂停 -->
        <button
          v-if="canPauseAll && isExpanded"
          class="header-btn"
          @click.stop="pauseAll"
          title="全部暂停"
        >
          <svg viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="4" width="4" height="16" rx="1" />
            <rect x="14" y="4" width="4" height="16" rx="1" />
          </svg>
        </button>

        <!-- 全部恢复 -->
        <button
          v-if="canResumeAll && isExpanded"
          class="header-btn"
          @click.stop="resumeAll"
          title="全部恢复"
        >
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>

        <!-- 清除已完成 -->
        <button
          v-if="stats.success > 0 && isExpanded"
          class="header-btn"
          @click.stop="clearCompleted"
          title="清除已完成"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          </svg>
        </button>

        <!-- 展开/收起 -->
        <button class="header-btn" title="展开/收起">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            :style="{ transform: isExpanded ? 'rotate(0deg)' : 'rotate(180deg)' }"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 任务列表 -->
    <div v-if="isExpanded" class="panel-body">
      <div
        v-for="task in uploadTasks"
        :key="task.id"
        class="task-item"
      >
        <div class="task-info">
          <div class="task-name">{{ task.name }}</div>
          <div class="task-meta">
            <span class="task-size">{{ task.sizeText }}</span>
            <span class="task-status" :style="{ color: getStatusColor(task.status) }">
              {{ getStatusText(task.status) }}
            </span>
            <span v-if="task.status === UPLOAD_STATUS.UPLOADING" class="task-progress-text">
              {{ task.progress }}%
            </span>
          </div>

          <!-- 处理步骤 -->
          <div v-if="task.step && task.status === UPLOAD_STATUS.UPLOADING" class="task-step">
            {{ task.step }}
          </div>

          <!-- 错误信息 -->
          <div v-if="task.error" class="task-error">
            {{ task.error }}
          </div>
        </div>

        <!-- 进度条 -->
        <div
          v-if="task.status === UPLOAD_STATUS.UPLOADING || task.status === UPLOAD_STATUS.PAUSED"
          class="task-progress"
        >
          <div
            class="progress-bar"
            :style="{
              width: task.progress + '%',
              background: getStatusColor(task.status)
            }"
          ></div>
        </div>

        <!-- 操作按钮 -->
        <div class="task-actions">
          <!-- 暂停 -->
          <button
            v-if="task.status === UPLOAD_STATUS.UPLOADING"
            class="task-btn"
            @click="pauseTask(task.id)"
            title="暂停"
          >
            <svg viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="4" width="4" height="16" rx="1" />
              <rect x="14" y="4" width="4" height="16" rx="1" />
            </svg>
          </button>

          <!-- 恢复 -->
          <button
            v-if="task.status === UPLOAD_STATUS.PAUSED"
            class="task-btn"
            @click="resumeTask(task.id)"
            title="恢复"
          >
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
          </button>

          <!-- 重试 -->
          <button
            v-if="task.status === UPLOAD_STATUS.ERROR"
            class="task-btn retry"
            @click="retryTask(task.id)"
            title="重试"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
          </button>

          <!-- 取消/删除 -->
          <button
            class="task-btn delete"
            @click="cancelTask(task.id)"
            title="取消"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.upload-panel {
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 420px;
  max-height: 500px;
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  z-index: 1000;
  transition: max-height 0.3s;
}

.upload-panel.collapsed {
  max-height: 60px;
}

/* 头部 */
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  user-select: none;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

.status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-tertiary);
  flex-shrink: 0;
}

.status-indicator.active {
  background: var(--primary);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.header-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-radius: 6px;
  transition: all 0.2s;
}

.header-btn:hover {
  background: var(--surface-2);
  color: var(--text-primary);
}

.header-btn svg {
  width: 16px;
  height: 16px;
  transition: transform 0.2s;
}

/* 任务列表 */
.panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.task-item {
  background: var(--surface-2);
  border-radius: 8px;
  padding: 12px;
  display: grid;
  gap: 8px;
}

.task-info {
  display: grid;
  gap: 4px;
}

.task-name {
  font-size: 14px;
  color: var(--text-primary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
}

.task-size {
  color: var(--text-secondary);
}

.task-status {
  font-weight: 500;
}

.task-progress-text {
  color: var(--text-secondary);
}

.task-step {
  font-size: 12px;
  color: var(--text-tertiary);
}

.task-error {
  font-size: 12px;
  color: var(--error);
  padding: 6px 8px;
  background: rgba(239, 68, 68, 0.1);
  border-radius: 4px;
}

/* 进度条 */
.task-progress {
  height: 4px;
  background: var(--surface-3);
  border-radius: 4px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  transition: width 0.3s;
}

/* 操作按钮 */
.task-actions {
  display: flex;
  gap: 4px;
  justify-content: flex-end;
}

.task-btn {
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-radius: 6px;
  transition: all 0.2s;
}

.task-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: rgba(56, 189, 248, 0.1);
}

.task-btn.retry:hover {
  border-color: var(--warning);
  color: var(--warning);
  background: rgba(245, 158, 11, 0.1);
}

.task-btn.delete:hover {
  border-color: var(--error);
  color: var(--error);
  background: rgba(239, 68, 68, 0.1);
}

.task-btn svg {
  width: 14px;
  height: 14px;
}
</style>
