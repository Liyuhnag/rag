<script setup>
import { ref } from 'vue'

// 已上传的文件列表（模拟数据）
const uploadedFiles = ref([
  {
    id: 1,
    name: 'RAG技术文档.pdf',
    size: '2.5 MB',
    chunks: 45,
    status: 'completed',
    uploadedAt: '2026-10-15 14:30'
  },
  {
    id: 2,
    name: '向量数据库介绍.md',
    size: '156 KB',
    chunks: 12,
    status: 'completed',
    uploadedAt: '2026-10-14 09:15'
  }
])

// 待上传的文件
const pendingFiles = ref([])
const isDragging = ref(false)
const uploadProgress = ref({})

const handleDrop = (e) => {
  isDragging.value = false
  const droppedFiles = Array.from(e.dataTransfer.files)
  addFiles(droppedFiles)
}

const handleFileInput = (e) => {
  const selectedFiles = Array.from(e.target.files)
  addFiles(selectedFiles)
}

const addFiles = (newFiles) => {
  newFiles.forEach(file => {
    if (!pendingFiles.value.find(f => f.name === file.name)) {
      pendingFiles.value.push({
        id: Date.now() + Math.random(),
        name: file.name,
        size: formatFileSize(file.size),
        status: 'pending',
        file: file
      })
    }
  })
}

const formatFileSize = (bytes) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

const removePendingFile = (id) => {
  pendingFiles.value = pendingFiles.value.filter(f => f.id !== id)
}

const deleteUploadedFile = (id) => {
  if (confirm('确定要删除这个文件吗？')) {
    uploadedFiles.value = uploadedFiles.value.filter(f => f.id !== id)
  }
}

const uploadFiles = async () => {
  for (const fileItem of pendingFiles.value) {
    if (fileItem.status === 'pending') {
      fileItem.status = 'uploading'
      uploadProgress.value[fileItem.id] = 0

      // 模拟上传进度
      await new Promise(resolve => {
        const interval = setInterval(() => {
          uploadProgress.value[fileItem.id] += 10
          if (uploadProgress.value[fileItem.id] >= 100) {
            clearInterval(interval)
            fileItem.status = 'completed'

            // 移动到已上传列表
            uploadedFiles.value.unshift({
              id: Date.now() + Math.random(),
              name: fileItem.name,
              size: fileItem.size,
              chunks: Math.floor(Math.random() * 50) + 5,
              status: 'completed',
              uploadedAt: new Date().toLocaleString('zh-CN', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
              })
            })

            resolve()
          }
        }, 200)
      })
    }
  }

  // 清空待上传列表
  pendingFiles.value = pendingFiles.value.filter(f => f.status !== 'completed')
}
</script>

<template>
  <div class="file-manager">
    <!-- 上传区域 -->
    <div class="upload-section">
      <div class="section-header">
        <h2 class="section-title">上传文件</h2>
        <p class="section-desc">支持 PDF、TXT、MD、DOCX 格式</p>
      </div>

      <div
        class="drop-zone"
        :class="{ dragging: isDragging }"
        @drop.prevent="handleDrop"
        @dragover.prevent="isDragging = true"
        @dragleave.prevent="isDragging = false"
      >
        <svg class="upload-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
        <p class="drop-text">拖拽文件到此处</p>
        <p class="drop-or">或</p>
        <label class="select-btn">
          选择文件
          <input
            type="file"
            multiple
            accept=".pdf,.txt,.md,.docx"
            @change="handleFileInput"
            style="display: none"
          />
        </label>
      </div>

      <!-- 待上传列表 -->
      <div v-if="pendingFiles.length > 0" class="pending-list">
        <div class="list-header">
          <span class="list-title">待上传 ({{ pendingFiles.length }})</span>
          <button
            class="upload-btn"
            @click="uploadFiles"
            :disabled="pendingFiles.every(f => f.status !== 'pending')"
          >
            开始上传
          </button>
        </div>

        <div class="file-items">
          <div
            v-for="file in pendingFiles"
            :key="file.id"
            class="file-item pending"
          >
            <div class="file-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                <polyline points="13 2 13 9 20 9" />
              </svg>
            </div>

            <div class="file-details">
              <div class="file-name">{{ file.name }}</div>
              <div class="file-meta">
                <span class="file-size">{{ file.size }}</span>
                <span class="file-status" :class="file.status">
                  {{ file.status === 'pending' ? '待上传' : file.status === 'uploading' ? '上传中...' : '已完成' }}
                </span>
              </div>
              <div v-if="file.status === 'uploading'" class="progress">
                <div class="progress-bar" :style="{ width: uploadProgress[file.id] + '%' }"></div>
              </div>
            </div>

            <button
              class="file-action"
              @click="removePendingFile(file.id)"
              :disabled="file.status === 'uploading'"
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

    <!-- 已上传文件列表 -->
    <div class="uploaded-section">
      <div class="section-header">
        <h2 class="section-title">已上传文件</h2>
        <p class="section-desc">共 {{ uploadedFiles.length }} 个文件</p>
      </div>

      <div class="file-items">
        <div
          v-for="file in uploadedFiles"
          :key="file.id"
          class="file-item uploaded"
        >
          <div class="file-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
              <polyline points="13 2 13 9 20 9" />
            </svg>
          </div>

          <div class="file-details">
            <div class="file-name">{{ file.name }}</div>
            <div class="file-meta">
              <span class="file-size">{{ file.size }}</span>
              <span class="file-chunks">{{ file.chunks }} 个分块</span>
              <span class="file-date">{{ file.uploadedAt }}</span>
            </div>
          </div>

          <button
            class="file-action"
            @click="deleteUploadedFile(file.id)"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.file-manager {
  max-width: 1000px;
  margin: 0 auto;
  padding: 32px;
  display: grid;
  gap: 32px;
}

.upload-section,
.uploaded-section {
  display: grid;
  gap: 24px;
}

.section-header {
  display: grid;
  gap: 4px;
}

.section-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.section-desc {
  font-size: 14px;
  color: var(--text-secondary);
}

.drop-zone {
  border: 2px dashed var(--border);
  border-radius: 8px;
  background: var(--surface-1);
  padding: 48px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.drop-zone.dragging {
  border-color: var(--accent);
  background: var(--accent-light);
}

.upload-icon {
  width: 48px;
  height: 48px;
  color: var(--accent);
}

.drop-text {
  font-size: 16px;
  color: var(--text-primary);
}

.drop-or {
  font-size: 14px;
  color: var(--text-dim);
}

.select-btn {
  padding: 10px 24px;
  background: var(--accent);
  color: #FFFFFF;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.pending-list {
  background: var(--surface-2);
  border-radius: 8px;
  padding: 16px;
  display: grid;
  gap: 16px;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.list-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.upload-btn {
  padding: 8px 20px;
  background: var(--accent);
  color: #FFFFFF;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.upload-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.file-items {
  display: grid;
  gap: 8px;
}

.file-item {
  display: grid;
  grid-template-columns: 40px 1fr auto;
  gap: 16px;
  align-items: center;
  padding: 12px;
  border-radius: 8px;
}

.file-item.pending {
  background: var(--surface-1);
}

.file-item.uploaded {
  background: var(--surface-1);
  border: 1px solid var(--border);
}

.file-icon {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: var(--surface-3);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
}

.file-icon svg {
  width: 20px;
  height: 20px;
}

.file-details {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  display: flex;
  gap: 12px;
  font-size: 13px;
  color: var(--text-secondary);
}

.file-status {
  color: var(--text-secondary);
}

.file-status.uploading {
  color: var(--accent);
}

.file-status.completed {
  color: #10B981;
}

.file-chunks,
.file-date {
  color: var(--text-dim);
}

.progress {
  margin-top: 8px;
  height: 4px;
  background: var(--surface-3);
  border-radius: 4px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--accent);
}

.file-action {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.file-action:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.file-action svg {
  width: 16px;
  height: 16px;
}
</style>
