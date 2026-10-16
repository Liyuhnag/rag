<script setup>
import { ref } from 'vue'

const files = ref([])
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
    if (!files.value.find(f => f.name === file.name)) {
      files.value.push({
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

const removeFile = (id) => {
  files.value = files.value.filter(f => f.id !== id)
}

const uploadFiles = async () => {
  for (const fileItem of files.value) {
    if (fileItem.status === 'pending') {
      fileItem.status = 'uploading'
      uploadProgress.value[fileItem.id] = 0

      // 模拟上传进度
      const interval = setInterval(() => {
        uploadProgress.value[fileItem.id] += 10
        if (uploadProgress.value[fileItem.id] >= 100) {
          clearInterval(interval)
          fileItem.status = 'completed'
        }
      }, 200)
    }
  }
}
</script>

<template>
  <div class="upload-container">
    <div class="upload-header">
      <h1 class="title">文件向量化</h1>
      <p class="subtitle">上传文档以构建知识库，支持 PDF、TXT、MD 等格式</p>
    </div>

    <div
      class="drop-zone"
      :class="{ dragging: isDragging }"
      @drop.prevent="handleDrop"
      @dragover.prevent="isDragging = true"
      @dragleave.prevent="isDragging = false"
    >
      <div class="drop-zone-content">
        <svg class="upload-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
        <p class="drop-text">拖拽文件到此处上传</p>
        <p class="drop-hint">或</p>
        <label class="select-button">
          选择文件
          <input
            type="file"
            multiple
            @change="handleFileInput"
            style="display: none"
          />
        </label>
      </div>
    </div>

    <div v-if="files.length > 0" class="file-list">
      <div class="file-list-header">
        <h2 class="file-list-title">待处理文件 ({{ files.length }})</h2>
        <button
          class="upload-all-button"
          @click="uploadFiles"
          :disabled="files.every(f => f.status !== 'pending')"
        >
          开始向量化
        </button>
      </div>

      <div class="files">
        <div
          v-for="fileItem in files"
          :key="fileItem.id"
          class="file-item"
        >
          <div class="file-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
              <polyline points="13 2 13 9 20 9" />
            </svg>
          </div>

          <div class="file-info">
            <div class="file-name">{{ fileItem.name }}</div>
            <div class="file-meta">
              <span class="file-size">{{ fileItem.size }}</span>
              <span
                class="file-status"
                :class="fileItem.status"
              >
                {{ fileItem.status === 'pending' ? '待处理' : fileItem.status === 'uploading' ? '处理中...' : '已完成' }}
              </span>
            </div>
            <div
              v-if="fileItem.status === 'uploading'"
              class="progress-bar"
            >
              <div
                class="progress-fill"
                :style="{ width: uploadProgress[fileItem.id] + '%' }"
              ></div>
            </div>
          </div>

          <button
            class="remove-button"
            @click="removeFile(fileItem.id)"
            :disabled="fileItem.status === 'uploading'"
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
.upload-container {
  max-width: 900px;
  margin: 0 auto;
  padding: var(--spacing-xl);
}

.upload-header {
  margin-bottom: var(--spacing-xl);
}

.title {
  font-size: clamp(24px, 4vw, 32px);
  font-weight: 600;
  margin-bottom: var(--spacing-sm);
  letter-spacing: -0.02em;
}

.subtitle {
  color: var(--text-secondary);
  font-size: 15px;
}

.drop-zone {
  border: 2px dashed var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface-1);
  padding: var(--spacing-xl);
  margin-bottom: var(--spacing-xl);
  transition: all 0.2s;
}

.drop-zone.dragging {
  border-color: var(--accent);
  background: var(--surface-2);
}

.drop-zone-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-xl) 0;
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

.drop-hint {
  font-size: 14px;
  color: var(--text-dim);
}

.select-button {
  padding: var(--spacing-sm) var(--spacing-lg);
  background: var(--accent);
  color: #FFFFFF;
  border: none;
  border-radius: var(--radius-full);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.select-button:hover {
  background: var(--accent-dim);
}

.file-list {
  background: var(--surface-1);
  border-radius: var(--radius-lg);
  padding: var(--spacing-lg);
}

.file-list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-md);
}

.file-list-title {
  font-size: 18px;
  font-weight: 600;
}

.upload-all-button {
  padding: var(--spacing-sm) var(--spacing-lg);
  background: var(--accent);
  color: #FFFFFF;
  border: none;
  border-radius: var(--radius-full);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
}

.upload-all-button:hover:not(:disabled) {
  background: var(--accent-dim);
}

.upload-all-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.files {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.file-item {
  display: grid;
  grid-template-columns: 40px 1fr auto;
  gap: var(--spacing-md);
  align-items: center;
  padding: var(--spacing-md);
  background: var(--surface-2);
  border-radius: var(--radius-md);
}

.file-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
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

.file-info {
  min-width: 0;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.file-meta {
  display: flex;
  gap: var(--spacing-sm);
  font-size: 13px;
}

.file-size {
  color: var(--text-dim);
}

.file-status {
  color: var(--text-secondary);
}

.file-status.uploading {
  color: var(--accent);
}

.file-status.completed {
  color: #6EE7B7;
}

.progress-bar {
  margin-top: 8px;
  height: 4px;
  background: var(--surface-4);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--accent);
  transition: width 0.3s;
}

.remove-button {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.remove-button:hover:not(:disabled) {
  background: var(--surface-3);
  color: var(--text-primary);
}

.remove-button:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.remove-button svg {
  width: 16px;
  height: 16px;
}
</style>
