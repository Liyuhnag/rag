<script setup>
import { ref } from 'vue'
import Toast from './Toast.vue'
import Confirm from './Confirm.vue'

// 文件列表（模拟数据）
const files = ref([
  {
    id: 1,
    name: 'RAG技术文档.pdf',
    type: 'pdf',
    size: '2.5 MB',
    chunks: 45,
    status: 'completed',
    uploadedAt: '2026-10-15 14:30',
    url: null // 预留预览 URL
  },
  {
    id: 2,
    name: '向量数据库介绍.md',
    type: 'md',
    size: '156 KB',
    chunks: 12,
    status: 'completed',
    uploadedAt: '2026-10-14 09:15',
    url: null
  },
  {
    id: 3,
    name: '项目需求文档.docx',
    type: 'docx',
    size: '1.2 MB',
    chunks: 28,
    status: 'completed',
    uploadedAt: '2026-10-13 16:20',
    url: null
  }
])

// Toast 状态
const toastState = ref({
  show: false,
  type: 'info',
  message: ''
})

// Confirm 状态
const confirmState = ref({
  show: false,
  title: '确认操作',
  message: '',
  type: 'warning',
  onConfirm: null
})

// 显示上传对话框
const showUploadDialog = ref(false)
const uploadingFiles = ref([])
const uploadProgress = ref({})

const showToast = (type, message) => {
  toastState.value = {
    show: true,
    type,
    message
  }
}

const closeToast = () => {
  toastState.value.show = false
}

const showConfirm = (title, message, type, onConfirm) => {
  confirmState.value = {
    show: true,
    title,
    message,
    type,
    onConfirm
  }
}

const closeConfirm = () => {
  confirmState.value.show = false
  confirmState.value.onConfirm = null
}

const handleConfirm = () => {
  if (confirmState.value.onConfirm) {
    confirmState.value.onConfirm()
  }
  closeConfirm()
}

const openUploadDialog = () => {
  showUploadDialog.value = true
}

const closeUploadDialog = () => {
  if (uploadingFiles.value.some(f => f.status === 'uploading')) {
    showConfirm(
      '确认关闭',
      '还有文件正在上传中，确定要关闭吗？',
      'warning',
      () => {
        showUploadDialog.value = false
        uploadingFiles.value = []
      }
    )
  } else {
    showUploadDialog.value = false
    uploadingFiles.value = []
  }
}

const handleFileInput = (e) => {
  const selectedFiles = Array.from(e.target.files)
  addFiles(selectedFiles)
}

const addFiles = (newFiles) => {
  newFiles.forEach(file => {
    if (!uploadingFiles.value.find(f => f.name === file.name)) {
      uploadingFiles.value.push({
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

const getFileType = (filename) => {
  const ext = filename.split('.').pop().toLowerCase()
  return ext
}

const uploadFiles = async () => {
  if (uploadingFiles.value.length === 0) {
    showToast('warning', '请先选择要上传的文件')
    return
  }

  for (const fileItem of uploadingFiles.value) {
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

            // 添加到文件列表
            files.value.unshift({
              id: Date.now() + Math.random(),
              name: fileItem.name,
              type: getFileType(fileItem.name),
              size: fileItem.size,
              chunks: Math.floor(Math.random() * 50) + 5,
              status: 'completed',
              uploadedAt: new Date().toLocaleString('zh-CN', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
              }),
              url: null
            })

            resolve()
          }
        }, 200)
      })
    }
  }

  showToast('success', `成功上传 ${uploadingFiles.value.length} 个文件`)
  closeUploadDialog()
}

const deleteFile = (file) => {
  showConfirm(
    '确认删除',
    `确定要删除文件"${file.name}"吗？此操作不可恢复。`,
    'danger',
    () => {
      files.value = files.value.filter(f => f.id !== file.id)
      showToast('success', '文件已删除')
    }
  )
}

const previewFile = (file) => {
  if (file.url) {
    // TODO: 集成文件预览组件库
    showToast('info', '文件预览功能开发中...')
  } else {
    showToast('warning', '该文件暂不支持预览')
  }
}

const removeUploadingFile = (id) => {
  uploadingFiles.value = uploadingFiles.value.filter(f => f.id !== id)
}
</script>

<template>
  <div class="file-manager">
    <!-- 头部操作栏 -->
    <div class="header">
      <div class="header-info">
        <h2 class="title">文件管理</h2>
        <p class="subtitle">共 {{ files.length }} 个文件</p>
      </div>

      <button class="upload-btn" @click="openUploadDialog">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
        上传文件
      </button>
    </div>

    <!-- 文件列表 -->
    <div class="file-list">
      <div class="list-header">
        <div class="col-name">文件名</div>
        <div class="col-type">类型</div>
        <div class="col-size">大小</div>
        <div class="col-chunks">分块数</div>
        <div class="col-time">上传时间</div>
        <div class="col-actions">操作</div>
      </div>

      <div class="list-body">
        <div v-for="file in files" :key="file.id" class="file-row">
          <div class="col-name">
            <div class="file-icon" :class="`icon-${file.type}`">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                <polyline points="13 2 13 9 20 9" />
              </svg>
            </div>
            <span class="file-name">{{ file.name }}</span>
          </div>

          <div class="col-type">
            <span class="file-type-badge">{{ file.type.toUpperCase() }}</span>
          </div>

          <div class="col-size">{{ file.size }}</div>

          <div class="col-chunks">{{ file.chunks }} 个</div>

          <div class="col-time">{{ file.uploadedAt }}</div>

          <div class="col-actions">
            <button class="action-btn" @click="previewFile(file)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </button>

            <button class="action-btn" @click="deleteFile(file)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 上传对话框 -->
    <div v-if="showUploadDialog" class="dialog-overlay" @click="closeUploadDialog">
      <div class="dialog" @click.stop>
        <div class="dialog-header">
          <h3>上传文件</h3>
          <button class="close-btn" @click="closeUploadDialog">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div class="dialog-body">
          <label class="file-select-area">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <span>点击选择文件</span>
            <p>支持 PDF、TXT、MD、DOCX 格式</p>
            <input
              type="file"
              multiple
              accept=".pdf,.txt,.md,.docx"
              @change="handleFileInput"
              style="display: none"
            />
          </label>

          <!-- 待上传文件列表 -->
          <div v-if="uploadingFiles.length > 0" class="uploading-list">
            <div v-for="file in uploadingFiles" :key="file.id" class="uploading-item">
              <div class="uploading-info">
                <span class="uploading-name">{{ file.name }}</span>
                <span class="uploading-size">{{ file.size }}</span>
              </div>

              <div v-if="file.status === 'uploading'" class="uploading-progress">
                <div class="progress-bar" :style="{ width: uploadProgress[file.id] + '%' }"></div>
              </div>

              <button
                v-if="file.status === 'pending'"
                class="remove-btn"
                @click="removeUploadingFile(file.id)"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          </div>
        </div>

        <div class="dialog-footer">
          <button class="cancel-btn" @click="closeUploadDialog">取消</button>
          <button class="confirm-btn" @click="uploadFiles">开始上传</button>
        </div>
      </div>
    </div>

    <!-- Toast 提示 -->
    <Toast
      :type="toastState.type"
      :message="toastState.message"
      :show="toastState.show"
      @close="closeToast"
    />

    <!-- Confirm 确认框 -->
    <Confirm
      :title="confirmState.title"
      :message="confirmState.message"
      :type="confirmState.type"
      :show="confirmState.show"
      @confirm="handleConfirm"
      @cancel="closeConfirm"
    />
  </div>
</template>

<style scoped>
.file-manager {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 32px;
  gap: 24px;
}

/* 头部 */
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-info {
  display: grid;
  gap: 4px;
}

.title {
  font-size: 24px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.subtitle {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0;
}

.upload-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 24px;
  background: var(--primary);
  color: #FFFFFF;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.upload-btn svg {
  width: 18px;
  height: 18px;
}

/* 文件列表 */
.file-list {
  flex: 1;
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.list-header,
.file-row {
  display: grid;
  grid-template-columns: 2fr 100px 120px 120px 180px 120px;
  gap: 16px;
  padding: 16px 20px;
  align-items: center;
}

.list-header {
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.list-body {
  flex: 1;
  overflow-y: auto;
}

.file-row {
  border-bottom: 1px solid var(--border);
  font-size: 14px;
}

.file-row:last-child {
  border-bottom: none;
}

.col-name {
  display: flex;
  align-items: center;
  gap: 12px;
}

.file-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: var(--surface-2);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.file-icon svg {
  width: 18px;
  height: 18px;
  color: var(--primary);
}

.file-name {
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-type-badge {
  display: inline-block;
  padding: 4px 8px;
  background: var(--surface-2);
  color: var(--text-secondary);
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.col-size,
.col-chunks,
.col-time {
  color: var(--text-secondary);
}

.col-actions {
  display: flex;
  gap: 8px;
}

.action-btn {
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  background: transparent;
  border-radius: 6px;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.action-btn svg {
  width: 16px;
  height: 16px;
}

/* 对话框 */
.dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.dialog {
  width: 560px;
  background: var(--surface-1);
  border-radius: 12px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
  display: grid;
  grid-template-rows: auto 1fr auto;
}

.dialog-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid var(--border);
}

.dialog-header h3 {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.close-btn svg {
  width: 18px;
  height: 18px;
}

.dialog-body {
  padding: 24px;
  display: grid;
  gap: 16px;
  max-height: 400px;
  overflow-y: auto;
}

.file-select-area {
  border: 2px dashed var(--border);
  border-radius: 8px;
  padding: 40px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}

.file-select-area svg {
  width: 40px;
  height: 40px;
  color: var(--primary);
}

.file-select-area span {
  font-size: 16px;
  color: var(--text-primary);
}

.file-select-area p {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0;
}

.uploading-list {
  display: grid;
  gap: 12px;
}

.uploading-item {
  padding: 12px;
  background: var(--surface-2);
  border-radius: 8px;
  display: grid;
  gap: 8px;
  position: relative;
}

.uploading-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.uploading-name {
  font-size: 14px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.uploading-size {
  font-size: 13px;
  color: var(--text-secondary);
  flex-shrink: 0;
  margin-left: 12px;
}

.uploading-progress {
  height: 4px;
  background: var(--surface-3);
  border-radius: 4px;
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--primary);
}

.remove-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.remove-btn svg {
  width: 14px;
  height: 14px;
}

.dialog-footer {
  padding: 16px 24px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.cancel-btn,
.confirm-btn {
  padding: 10px 24px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.cancel-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-secondary);
}

.confirm-btn {
  background: var(--primary);
  border: none;
  color: #FFFFFF;
}
</style>
