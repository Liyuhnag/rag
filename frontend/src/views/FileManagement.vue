<script setup>
import { ref, computed, onMounted } from 'vue'
import { getDocuments, uploadDocument, deleteDocument } from '../api'
import Toast from '../components/Toast.vue'
import Confirm from '../components/Confirm.vue'
import Pagination from '../components/Pagination.vue'
import IconFilePdf from '../icons/IconFilePdf.vue'
import IconFileMarkdown from '../icons/IconFileMarkdown.vue'
import IconFileWord from '../icons/IconFileWord.vue'
import IconFileText from '../icons/IconFileText.vue'
import IconFileExcel from '../icons/IconFileExcel.vue'
import IconFileCsv from '../icons/IconFileCsv.vue'

// 文件图标映射
const fileIconMap = {
  pdf: IconFilePdf,
  md: IconFileMarkdown,
  markdown: IconFileMarkdown,
  doc: IconFileWord,
  docx: IconFileWord,
  txt: IconFileText,
  xls: IconFileExcel,
  xlsx: IconFileExcel,
  csv: IconFileCsv
}

// 文件类型颜色映射
const fileColorMap = {
  pdf: '#EF4444',
  md: '#A855F7',
  markdown: '#A855F7',
  doc: '#3B82F6',
  docx: '#3B82F6',
  txt: '#64748B',
  xls: '#10B981',
  xlsx: '#10B981',
  csv: '#14B8A6'
}

// === 数据状态 ===
const allFiles = ref([])
const loading = ref(false)
const totalPages = ref(0)
const totalItems = ref(0)

// === 分页状态 ===
const currentPage = ref(1)
const pageSize = ref(20)

// === 表格列宽（使用 fr 弹性单位） ===
const columnWidths = ref({
  name: 3,
  type: 0.8,
  size: 0.8,
  chunks: 0.8,
  time: 1.5,
  actions: 0.8
})

// === 拖拽调整列宽 ===
const resizing = ref(null)
const startX = ref(0)
const startWidth = ref(0)
const tableRef = ref(null)
const nextColumn = ref(null)
const startNextWidth = ref(0)
const columnOrder = ['name', 'type', 'size', 'chunks', 'time', 'actions']

const startResize = (column, event) => {
  if (!tableRef.value) return
  resizing.value = column
  startX.value = event.clientX
  startWidth.value = columnWidths.value[column]
  const currentIndex = columnOrder.indexOf(column)
  if (currentIndex < columnOrder.length - 1) {
    nextColumn.value = columnOrder[currentIndex + 1]
    startNextWidth.value = columnWidths.value[nextColumn.value]
  } else {
    nextColumn.value = null
  }
  document.addEventListener('mousemove', onResize)
  document.addEventListener('mouseup', stopResize)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  event.preventDefault()
}

const onResize = (event) => {
  if (!resizing.value || !tableRef.value) return
  const diff = event.clientX - startX.value
  const containerWidth = tableRef.value.offsetWidth
  const totalFr = Object.values(columnWidths.value).reduce((sum, fr) => sum + fr, 0)
  const pixelPerFr = containerWidth / totalFr
  const frDiff = diff / pixelPerFr
  const newWidth = Math.max(0.5, startWidth.value + frDiff)
  if (nextColumn.value) {
    const newNextWidth = Math.max(0.5, startNextWidth.value - frDiff)
    if (newWidth >= 0.5 && newNextWidth >= 0.5) {
      columnWidths.value[resizing.value] = newWidth
      columnWidths.value[nextColumn.value] = newNextWidth
    }
  } else {
    columnWidths.value[resizing.value] = newWidth
  }
}

const stopResize = () => {
  resizing.value = null
  nextColumn.value = null
  document.removeEventListener('mousemove', onResize)
  document.removeEventListener('mouseup', stopResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

const gridTemplateColumns = computed(() => {
  return `${columnWidths.value.name}fr ${columnWidths.value.type}fr ${columnWidths.value.size}fr ${columnWidths.value.chunks}fr ${columnWidths.value.time}fr ${columnWidths.value.actions}fr`
})

// === 当前页的文件列表 ===
const files = computed(() => allFiles.value)

// === Toast 状态 ===
const toastState = ref({
  show: false,
  type: 'info',
  message: ''
})

const showToast = (type, message) => {
  toastState.value = { show: true, type, message }
}

const closeToast = () => {
  toastState.value.show = false
}

// === Confirm 状态 ===
const confirmState = ref({
  show: false,
  title: '确认操作',
  message: '',
  type: 'warning',
  onConfirm: null
})

const showConfirm = (title, message, type, onConfirm) => {
  confirmState.value = { show: true, title, message, type, onConfirm }
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

// === 上传对话框 ===
const showUploadDialog = ref(false)
const uploadingFiles = ref([])
const uploadProgress = ref({})

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
        file,
        name: file.name,
        size: formatFileSize(file.size),
        status: 'pending',
        progress: 0,
        error: null
      })
    }
  })
}

const removeUploadingFile = (id) => {
  const index = uploadingFiles.value.findIndex(f => f.id === id)
  if (index !== -1) {
    uploadingFiles.value.splice(index, 1)
  }
}

const startUpload = async () => {
  const pendingFiles = uploadingFiles.value.filter(f => f.status === 'pending')
  if (pendingFiles.length === 0) {
    showToast('warning', '没有待上传的文件')
    return
  }

  for (const fileItem of pendingFiles) {
    fileItem.status = 'uploading'
    fileItem.progress = 0

    try {
      await uploadDocument(
        fileItem.file,
        'User', // TODO: 从登录状态获取真实用户名
        (percent) => {
          fileItem.progress = percent
        }
      )

      fileItem.status = 'completed'
      fileItem.progress = 100
    } catch (error) {
      fileItem.status = 'failed'
      fileItem.error = error.message || '上传失败'
    }
  }

  // 上传完成后刷新列表
  const successCount = uploadingFiles.value.filter(f => f.status === 'completed').length
  const failCount = uploadingFiles.value.filter(f => f.status === 'failed').length

  if (successCount > 0) {
    showToast('success', `成功上传 ${successCount} 个文件`)
    await loadFiles()
  }

  if (failCount > 0) {
    showToast('error', `${failCount} 个文件上传失败`)
  }

  // 3秒后关闭对话框
  setTimeout(() => {
    closeUploadDialog()
  }, 3000)
}

// === 文件预览 ===
const showPreview = ref(false)
const previewFile = ref(null)

const handlePreview = (file) => {
  if (file.previewUrl) {
    previewFile.value = file
    showPreview.value = true
  } else if (file.url) {
    showToast('info', '该文件不支持预览，请下载查看')
  } else {
    showToast('warning', '文件链接不可用')
  }
}

const closePreview = () => {
  showPreview.value = false
  previewFile.value = null
}

const handleDownload = (file) => {
  if (file.url) {
    window.open(file.url, '_blank')
  } else {
    showToast('warning', '文件链接不可用')
  }
}

// === 删除文件 ===
const handleDelete = (file) => {
  showConfirm(
    '确认删除',
    `确定要删除文件"${file.name}"吗？此操作不可恢复。`,
    'danger',
    async () => {
      try {
        await deleteDocument(file.id)
        showToast('success', '文件删除成功')
        await loadFiles()
      } catch (error) {
        showToast('error', error.message || '删除失败')
      }
    }
  )
}

// === 分页 ===
const handlePageChange = (page) => {
  currentPage.value = page
  loadFiles()
}

const handlePageSizeChange = (size) => {
  pageSize.value = size
  currentPage.value = 1
  loadFiles()
}

// === 工具函数 ===
const getFileIcon = (type) => {
  const lowerType = type.toLowerCase()
  return fileIconMap[lowerType] || IconFileText
}

const getFileColor = (type) => {
  const lowerType = type.toLowerCase()
  return fileColorMap[lowerType] || '#64748B'
}

const formatFileSize = (bytes) => {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

const formatDateTime = (dateTimeStr) => {
  if (!dateTimeStr) return ''
  // ISO 8601 格式转换为本地时间
  const date = new Date(dateTimeStr)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:${minute}`
}

// === 加载文件列表 ===
const loadFiles = async () => {
  loading.value = true
  try {
    const response = await getDocuments({
      page: currentPage.value,
      pageSize: pageSize.value,
      sortBy: 'uploadedAt',
      sortOrder: 'desc'
    })

    allFiles.value = response.data.map(doc => ({
      id: doc.id,
      name: doc.name,
      type: doc.type,
      size: formatFileSize(doc.size_bytes),
      chunks: doc.chunk_count,
      status: doc.status.toLowerCase(),
      uploadedAt: formatDateTime(doc.uploaded_at),
      operator: doc.operator,
      url: null, // TODO: 后端需要添加原始文件 URL
      previewUrl: null // TODO: 后端需要添加预览 URL
    }))

    totalItems.value = response.pagination.total_items
    totalPages.value = response.pagination.total_pages
  } catch (error) {
    showToast('error', error.message || '加载文件列表失败')
    allFiles.value = []
    totalItems.value = 0
    totalPages.value = 0
  } finally {
    loading.value = false
  }
}

// === 生命周期 ===
onMounted(() => {
  loadFiles()
})
</script>

<template>
  <div class="file-management">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <h2 class="title">文件管理</h2>
      <button class="btn-upload" @click="openUploadDialog">上传文件</button>
    </div>

    <!-- 文件列表 -->
    <div class="file-list-container">
      <div class="file-table" ref="tableRef">
        <!-- 表头 -->
        <div class="table-header" :style="{ gridTemplateColumns }">
          <div class="header-cell">
            <span>文件名</span>
            <div class="resize-handle" @mousedown="startResize('name', $event)"></div>
          </div>
          <div class="header-cell">
            <span>类型</span>
            <div class="resize-handle" @mousedown="startResize('type', $event)"></div>
          </div>
          <div class="header-cell">
            <span>大小</span>
            <div class="resize-handle" @mousedown="startResize('size', $event)"></div>
          </div>
          <div class="header-cell">
            <span>分块数</span>
            <div class="resize-handle" @mousedown="startResize('chunks', $event)"></div>
          </div>
          <div class="header-cell">
            <span>上传时间</span>
            <div class="resize-handle" @mousedown="startResize('time', $event)"></div>
          </div>
          <div class="header-cell">
            <span>操作</span>
          </div>
        </div>

        <!-- 加载状态 -->
        <div v-if="loading" class="loading-state">
          <div class="loading-spinner"></div>
          <div class="loading-text">加载中...</div>
        </div>

        <!-- 空状态 -->
        <div v-else-if="files.length === 0" class="empty-state">
          <div class="empty-icon">📄</div>
          <div class="empty-text">暂无文件</div>
          <button class="btn-upload-empty" @click="openUploadDialog">上传文件</button>
        </div>

        <!-- 文件行 -->
        <div v-else class="table-body">
          <div
            v-for="file in files"
            :key="file.id"
            class="file-row"
            :style="{ gridTemplateColumns }"
          >
            <div class="cell cell-name">
              <component :is="getFileIcon(file.type)" class="file-icon" />
              <span class="file-name" :title="file.name">{{ file.name }}</span>
            </div>
            <div class="cell cell-type">
              <span class="type-badge" :style="{ color: getFileColor(file.type), borderColor: getFileColor(file.type) }">
                {{ file.type.toUpperCase() }}
              </span>
            </div>
            <div class="cell cell-size">{{ file.size }}</div>
            <div class="cell cell-chunks">{{ file.chunks }}</div>
            <div class="cell cell-time">{{ file.uploadedAt }}</div>
            <div class="cell cell-actions">
              <button class="btn-action" @click="handlePreview(file)" title="预览">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M8 3C4.5 3 1.7 5.6 1 8c.7 2.4 3.5 5 7 5s6.3-2.6 7-5c-.7-2.4-3.5-5-7-5z" stroke="currentColor" stroke-width="1.5"/>
                  <circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.5"/>
                </svg>
              </button>
              <button class="btn-action" @click="handleDownload(file)" title="下载">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M8 2v8M5 7l3 3 3-3M3 14h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </button>
              <button class="btn-action btn-delete" @click="handleDelete(file)" title="删除">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M3 4h10M6 4V3h4v1M6 7v4M10 7v4M4 4l.5 8h7l.5-8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- 分页 -->
      <Pagination
        v-if="!loading && files.length > 0"
        :currentPage="currentPage"
        :totalPages="totalPages"
        :totalItems="totalItems"
        @page-change="handlePageChange"
        @page-size-change="handlePageSizeChange"
      />
    </div>

    <!-- 上传对话框 -->
    <div v-if="showUploadDialog" class="upload-dialog-overlay" @click.self="closeUploadDialog">
      <div class="upload-dialog">
        <div class="upload-header">
          <h3>上传文件</h3>
          <button class="btn-close" @click="closeUploadDialog">✕</button>
        </div>

        <div class="upload-body">
          <div class="upload-area">
            <input
              type="file"
              id="file-input"
              multiple
              @change="handleFileInput"
              accept=".pdf,.doc,.docx,.txt,.md,.markdown,.xls,.xlsx,.csv"
            />
            <label for="file-input" class="upload-label">
              <div class="upload-icon">📁</div>
              <div class="upload-text">点击选择文件或拖拽到此处</div>
              <div class="upload-hint">支持 PDF、Word、Excel、Markdown、TXT、CSV</div>
            </label>
          </div>

          <div v-if="uploadingFiles.length > 0" class="uploading-files">
            <div v-for="file in uploadingFiles" :key="file.id" class="uploading-file">
              <div class="file-info">
                <span class="file-name">{{ file.name }}</span>
                <span class="file-size">{{ file.size }}</span>
              </div>
              <div class="file-status">
                <div v-if="file.status === 'pending'" class="status-pending">待上传</div>
                <div v-else-if="file.status === 'uploading'" class="status-uploading">
                  <div class="progress-bar">
                    <div class="progress-fill" :style="{ width: file.progress + '%' }"></div>
                  </div>
                  <span class="progress-text">{{ file.progress }}%</span>
                </div>
                <div v-else-if="file.status === 'completed'" class="status-completed">✓ 完成</div>
                <div v-else-if="file.status === 'failed'" class="status-failed">✕ {{ file.error }}</div>
              </div>
              <button
                v-if="file.status === 'pending'"
                class="btn-remove"
                @click="removeUploadingFile(file.id)"
              >
                ✕
              </button>
            </div>
          </div>
        </div>

        <div class="upload-footer">
          <button class="btn-cancel" @click="closeUploadDialog">取消</button>
          <button
            class="btn-confirm"
            @click="startUpload"
            :disabled="uploadingFiles.filter(f => f.status === 'pending').length === 0"
          >
            开始上传
          </button>
        </div>
      </div>
    </div>

    <!-- 文件预览 -->
    <div v-if="showPreview" class="preview-overlay" @click.self="closePreview">
      <div class="preview-container">
        <div class="preview-header">
          <div class="preview-title">
            <component :is="getFileIcon(previewFile.type)" class="preview-icon" />
            <span class="preview-filename">{{ previewFile.name }}</span>
            <span class="preview-badge" :style="{ color: getFileColor(previewFile.type), borderColor: getFileColor(previewFile.type) }">
              {{ previewFile.type.toUpperCase() }}
            </span>
          </div>
          <div class="preview-actions">
            <button class="btn-download" @click="handleDownload(previewFile)">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M8 2v8M5 7l3 3 3-3M3 14h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
              下载
            </button>
            <button class="btn-close-preview" @click="closePreview">✕</button>
          </div>
        </div>
        <div class="preview-content">
          <iframe
            :src="previewFile.previewUrl"
            frameborder="0"
            allowfullscreen
          ></iframe>
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
.file-management {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--surface-1);
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-lg) var(--space-xl);
  background: var(--surface-0);
  border-bottom: 1px solid var(--border);
}

.title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.btn-upload {
  padding: 10px 20px;
  background: var(--primary);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.file-list-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.file-table {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: var(--space-lg) var(--space-xl);
}

.table-header {
  display: grid;
  gap: var(--space-md);
  padding: var(--space-md) 0;
  border-bottom: 2px solid var(--border);
  margin-bottom: var(--space-md);
}

.header-cell {
  position: relative;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  padding-right: var(--space-md);
}

.resize-handle {
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 8px;
  cursor: col-resize;
  user-select: none;
}

.resize-handle:hover {
  background: var(--primary);
  opacity: 0.3;
}

.table-body {
  flex: 1;
  overflow-y: auto;
}

.file-row {
  display: grid;
  gap: var(--space-md);
  padding: var(--space-md) 0;
  border-bottom: 1px solid var(--border);
  align-items: center;
}

.file-row:hover {
  background: var(--surface-2);
}

.cell {
  font-size: 14px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-name {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.file-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
}

.file-name {
  overflow: hidden;
  text-overflow: ellipsis;
}

.type-badge {
  display: inline-block;
  padding: 2px 8px;
  border: 1px solid;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
}

.cell-actions {
  display: flex;
  gap: var(--space-sm);
}

.btn-action {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-0);
  color: var(--text-secondary);
  cursor: pointer;
}

.btn-delete:hover {
  border-color: var(--error);
  background: var(--error);
  color: white;
}

/* 加载状态 */
.loading-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-md);
}

.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-text {
  font-size: 14px;
  color: var(--text-secondary);
}

/* 空状态 */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-md);
}

.empty-icon {
  font-size: 48px;
  opacity: 0.3;
}

.empty-text {
  font-size: 14px;
  color: var(--text-secondary);
}

.btn-upload-empty {
  padding: 10px 20px;
  background: var(--primary);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
}

/* 上传对话框 */
.upload-dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.upload-dialog {
  width: 600px;
  max-height: 80vh;
  background: var(--surface-0);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.upload-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-lg) var(--space-xl);
  border-bottom: 1px solid var(--border);
}

.upload-header h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.btn-close {
  width: 32px;
  height: 32px;
  border: none;
  background: none;
  font-size: 20px;
  color: var(--text-secondary);
  cursor: pointer;
}

.upload-body {
  flex: 1;
  padding: var(--space-xl);
  overflow-y: auto;
}

.upload-area {
  position: relative;
  margin-bottom: var(--space-lg);
}

#file-input {
  display: none;
}

.upload-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-xl);
  border: 2px dashed var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-1);
  cursor: pointer;
}

.upload-label:hover {
  border-color: var(--primary);
  background: var(--surface-2);
}

.upload-icon {
  font-size: 48px;
  margin-bottom: var(--space-md);
}

.upload-text {
  font-size: 14px;
  color: var(--text-primary);
  margin-bottom: var(--space-sm);
}

.upload-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}

.uploading-files {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.uploading-file {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-md);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-1);
}

.file-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.file-info .file-name {
  font-size: 14px;
  color: var(--text-primary);
}

.file-info .file-size {
  font-size: 12px;
  color: var(--text-tertiary);
}

.file-status {
  flex-shrink: 0;
  width: 120px;
}

.status-pending {
  font-size: 12px;
  color: var(--text-tertiary);
}

.status-uploading {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.progress-bar {
  flex: 1;
  height: 4px;
  background: var(--surface-2);
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--primary);
  transition: width 0.3s;
}

.progress-text {
  font-size: 12px;
  color: var(--text-secondary);
  width: 40px;
  text-align: right;
}

.status-completed {
  font-size: 12px;
  color: var(--success);
}

.status-failed {
  font-size: 12px;
  color: var(--error);
}

.btn-remove {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  border: none;
  background: none;
  font-size: 16px;
  color: var(--text-tertiary);
  cursor: pointer;
}

.btn-remove:hover {
  color: var(--error);
}

.upload-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-md);
  padding: var(--space-lg) var(--space-xl);
  border-top: 1px solid var(--border);
}

.btn-cancel {
  padding: 10px 20px;
  border: 1px solid var(--border);
  background: var(--surface-0);
  color: var(--text-primary);
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
}

.btn-confirm {
  padding: 10px 20px;
  border: none;
  background: var(--primary);
  color: white;
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
}

.btn-confirm:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 文件预览 */
.preview-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.8);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}

.preview-container {
  width: 95vw;
  height: 95vh;
  background: var(--surface-0);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-lg) var(--space-xl);
  border-bottom: 1px solid var(--border);
  background: var(--surface-0);
}

.preview-title {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.preview-icon {
  width: 24px;
  height: 24px;
}

.preview-filename {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.preview-badge {
  padding: 2px 8px;
  border: 1px solid;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 600;
}

.preview-actions {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.btn-download {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: 8px 16px;
  border: 1px solid var(--border);
  background: var(--surface-0);
  color: var(--text-primary);
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
}

.btn-download:hover {
  background: var(--surface-2);
}

.btn-close-preview {
  width: 32px;
  height: 32px;
  border: none;
  background: none;
  font-size: 20px;
  color: var(--text-secondary);
  cursor: pointer;
}

.preview-content {
  flex: 1;
  overflow: hidden;
}

.preview-content iframe {
  width: 100%;
  height: 100%;
}
</style>
