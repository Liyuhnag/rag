<script setup>
import { ref, computed } from 'vue'
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
  pdf: '#EF4444',      // 红色 - PDF 经典色
  md: '#A855F7',       // 紫色 - Markdown/代码
  markdown: '#A855F7',
  doc: '#3B82F6',      // 蓝色 - Word
  docx: '#3B82F6',
  txt: '#64748B',      // 灰色 - 纯文本
  xls: '#10B981',      // 绿色 - Excel
  xlsx: '#10B981',
  csv: '#14B8A6'       // 青绿色 - CSV 数据
}

// 真实文件数据（来自 SeaweedFS + kkFileView）
const realFiles = [
  {
    id: 1,
    name: 'ollama-readme.md',
    type: 'md',
    size: '12.3 KB',
    chunks: 15,
    status: 'completed',
    uploadedAt: '2026-10-17 14:20',
    url: 'http://ahanali.devcloud.woa.com:8888/buckets/rag-documents/md/ollama-readme.md',
    previewUrl: 'http://ahanali.devcloud.woa.com:8012/onlinePreview?url=aHR0cDovL3NlYXdlZWRmczo4ODg4L2J1Y2tldHMvcmFnLWRvY3VtZW50cy9tZC9vbGxhbWEtcmVhZG1lLm1k'
  },
  {
    id: 2,
    name: 'seaweedfs-readme.md',
    type: 'md',
    size: '18.7 KB',
    chunks: 20,
    status: 'completed',
    uploadedAt: '2026-10-17 14:18',
    url: 'http://ahanali.devcloud.woa.com:8888/buckets/rag-documents/md/seaweedfs-readme.md',
    previewUrl: 'http://ahanali.devcloud.woa.com:8012/onlinePreview?url=aHR0cDovL3NlYXdlZWRmczo4ODg4L2J1Y2tldHMvcmFnLWRvY3VtZW50cy9tZC9zZWF3ZWVkZnMtcmVhZG1lLm1k'
  },
  {
    id: 3,
    name: 'retrieval-augmented-generation.pdf',
    type: 'pdf',
    size: '2.1 MB',
    chunks: 45,
    status: 'completed',
    uploadedAt: '2026-10-17 14:15',
    url: 'http://ahanali.devcloud.woa.com:8888/buckets/rag-documents/pdf/retrieval-augmented-generation.pdf',
    previewUrl: 'http://ahanali.devcloud.woa.com:8012/onlinePreview?url=aHR0cDovL3NlYXdlZWRmczo4ODg4L2J1Y2tldHMvcmFnLWRvY3VtZW50cy9wZGYvcmV0cmlldmFsLWF1Z21lbnRlZC1nZW5lcmF0aW9uLnBkZg%3D%3D'
  },
  {
    id: 4,
    name: 'attention-is-all-you-need.pdf',
    type: 'pdf',
    size: '1.8 MB',
    chunks: 38,
    status: 'completed',
    uploadedAt: '2026-10-17 14:12',
    url: 'http://ahanali.devcloud.woa.com:8888/buckets/rag-documents/pdf/attention-is-all-you-need.pdf',
    previewUrl: 'http://ahanali.devcloud.woa.com:8012/onlinePreview?url=aHR0cDovL3NlYXdlZWRmczo4ODg4L2J1Y2tldHMvcmFnLWRvY3VtZW50cy9wZGYvYXR0ZW50aW9uLWlzLWFsbC15b3UtbmVlZC5wZGY%3D'
  },
  {
    id: 5,
    name: 'having-images.docx',
    type: 'docx',
    size: '850 KB',
    chunks: 25,
    status: 'completed',
    uploadedAt: '2026-10-17 14:10',
    url: 'http://ahanali.devcloud.woa.com:8888/buckets/rag-documents/docx/having-images.docx',
    previewUrl: 'http://ahanali.devcloud.woa.com:8012/onlinePreview?url=aHR0cDovL3NlYXdlZWRmczo4ODg4L2J1Y2tldHMvcmFnLWRvY3VtZW50cy9kb2N4L2hhdmluZy1pbWFnZXMuZG9jeA%3D%3D'
  },
  {
    id: 6,
    name: 'sample-document.docx',
    type: 'docx',
    size: '625 KB',
    chunks: 18,
    status: 'completed',
    uploadedAt: '2026-10-17 14:08',
    url: 'http://ahanali.devcloud.woa.com:8888/buckets/rag-documents/docx/sample-document.docx',
    previewUrl: 'http://ahanali.devcloud.woa.com:8012/onlinePreview?url=aHR0cDovL3NlYXdlZWRmczo4ODg4L2J1Y2tldHMvcmFnLWRvY3VtZW50cy9kb2N4L3NhbXBsZS1kb2N1bWVudC5kb2N4'
  }
]

// 生成模拟数据
const generateMockFiles = () => {
  const fileTypes = ['pdf', 'md', 'docx', 'txt', 'xlsx', 'csv']
  const fileNames = [
    'RAG技术文档', '向量数据库介绍', '项目需求文档', 'API接口设计',
    '用户手册', '系统架构说明', '测试报告', '产品规格书',
    '开发指南', '部署文档', '性能测试报告', '安全审计报告',
    '数据字典', '业务流程图', '技术选型方案', '代码规范'
  ]

  const files = []
  for (let i = 7; i <= 67; i++) {
    const type = fileTypes[Math.floor(Math.random() * fileTypes.length)]
    const baseName = fileNames[Math.floor(Math.random() * fileNames.length)]
    files.push({
      id: i,
      name: `${baseName}_v${i}.${type}`,
      type: type,
      size: `${(Math.random() * 5 + 0.1).toFixed(1)} MB`,
      chunks: Math.floor(Math.random() * 50) + 5,
      status: 'completed',
      uploadedAt: `2026-10-${String(Math.floor(Math.random() * 17) + 1).padStart(2, '0')} ${String(Math.floor(Math.random() * 24)).padStart(2, '0')}:${String(Math.floor(Math.random() * 60)).padStart(2, '0')}`,
      url: null,
      previewUrl: null
    })
  }
  return files
}

// 所有文件列表（真实数据 + 模拟数据）
const allFiles = ref([...realFiles, ...generateMockFiles()].sort((a, b) => b.id - a.id))

// 分页状态
const currentPage = ref(1)
const pageSize = ref(20)

// 列宽状态（使用 fr 弹性单位比例）
const columnWidths = ref({
  name: 3,      // 文件名占 3 份
  type: 0.8,    // 类型占 0.8 份
  size: 0.8,    // 大小占 0.8 份
  chunks: 0.8,  // 分块数占 0.8 份
  time: 1.5,    // 上传时间占 1.5 份
  actions: 0.8  // 操作占 0.8 份
})

// 拖拽状态
const resizing = ref(null)
const startX = ref(0)
const startWidth = ref(0)
const tableRef = ref(null)
const nextColumn = ref(null)
const startNextWidth = ref(0)

// 获取列的顺序
const columnOrder = ['name', 'type', 'size', 'chunks', 'time', 'actions']

// 开始调整列宽
const startResize = (column, event) => {
  if (!tableRef.value) return

  resizing.value = column
  startX.value = event.clientX
  startWidth.value = columnWidths.value[column]

  // 找到下一列
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

// 调整列宽中
const onResize = (event) => {
  if (!resizing.value || !tableRef.value) return

  // 计算鼠标移动的距离（像素）
  const diff = event.clientX - startX.value

  // 获取容器总宽度
  const containerWidth = tableRef.value.offsetWidth
  const totalFr = Object.values(columnWidths.value).reduce((sum, fr) => sum + fr, 0)
  const pixelPerFr = containerWidth / totalFr

  // 将像素差异转换为 fr 单位差异
  const frDiff = diff / pixelPerFr

  // 更新当前列和下一列的宽度
  const newWidth = Math.max(0.5, startWidth.value + frDiff) // 最小 0.5fr

  if (nextColumn.value) {
    const newNextWidth = Math.max(0.5, startNextWidth.value - frDiff) // 最小 0.5fr

    // 只有当两列都满足最小宽度时才更新
    if (newWidth >= 0.5 && newNextWidth >= 0.5) {
      columnWidths.value[resizing.value] = newWidth
      columnWidths.value[nextColumn.value] = newNextWidth
    }
  } else {
    // 最后一列，直接更新
    columnWidths.value[resizing.value] = newWidth
  }
}

// 停止调整列宽
const stopResize = () => {
  resizing.value = null
  nextColumn.value = null
  document.removeEventListener('mousemove', onResize)
  document.removeEventListener('mouseup', stopResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

// 计算 grid 模板列
const gridTemplateColumns = computed(() => {
  return `${columnWidths.value.name}fr ${columnWidths.value.type}fr ${columnWidths.value.size}fr ${columnWidths.value.chunks}fr ${columnWidths.value.time}fr ${columnWidths.value.actions}fr`
})

// 计算总页数
const totalPages = computed(() => {
  return Math.ceil(allFiles.value.length / pageSize.value)
})

// 当前页的文件列表
const files = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  const end = start + pageSize.value
  return allFiles.value.slice(start, end)
})

// 页面变化处理
const handlePageChange = (page) => {
  currentPage.value = page
}

// 每页数量变化处理
const handlePageSizeChange = (size) => {
  pageSize.value = size
  currentPage.value = 1 // 重置到第一页
}

// 获取文件图标组件
const getFileIcon = (type) => {
  const lowerType = type.toLowerCase()
  return fileIconMap[lowerType] || IconFileText
}

// 获取文件类型颜色
const getFileColor = (type) => {
  const lowerType = type.toLowerCase()
  return fileColorMap[lowerType] || '#64748B'
}

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
            allFiles.value.unshift({
              id: Date.now() + Math.random(),
              name: fileItem.name,
              type: getFileType(fileItem.name),
              size: fileItem.size,
              chunks: Math.floor(Math.random() * 50) + 5,
              status: 'completed',
              operator: '当前用户', // TODO: 从用户信息获取
              uploadedAt: new Date().toLocaleString('zh-CN', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
              }),
              url: null
            })

            // 重置到第一页
            currentPage.value = 1

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
      allFiles.value = allFiles.value.filter(f => f.id !== file.id)
      showToast('success', '文件已删除')

      // 如果当前页没有数据且不是第一页，回到上一页
      if (files.value.length === 0 && currentPage.value > 1) {
        currentPage.value--
      }
    }
  )
}

// 预览状态
const previewState = ref({
  show: false,
  file: null
})

const previewFile = (file) => {
  if (file.previewUrl) {
    // 有预览 URL，打开预览弹窗
    previewState.value = {
      show: true,
      file: file
    }
  } else if (file.url) {
    // 有原始文件 URL 但没有预览 URL，提示不支持预览
    showToast('warning', '该文件暂不支持在线预览，请下载查看')
  } else {
    showToast('warning', '该文件暂不支持预览')
  }
}

const closePreview = () => {
  previewState.value = {
    show: false,
    file: null
  }
}

const downloadFile = (file) => {
  if (file.url) {
    const link = document.createElement('a')
    link.href = file.url
    link.download = file.name
    link.click()
    showToast('success', '开始下载文件')
  } else {
    showToast('warning', '该文件没有下载链接')
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
        <p class="subtitle">共 {{ allFiles.length }} 个文件</p>
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
    <div class="file-list" ref="tableRef">
      <div class="list-header" :style="{ gridTemplateColumns }">
        <div class="col-name header-cell">
          文件名
          <div class="resize-handle" @mousedown="startResize('name', $event)"></div>
        </div>
        <div class="col-type header-cell">
          类型
          <div class="resize-handle" @mousedown="startResize('type', $event)"></div>
        </div>
        <div class="col-size header-cell">
          大小
          <div class="resize-handle" @mousedown="startResize('size', $event)"></div>
        </div>
        <div class="col-chunks header-cell">
          分块数
          <div class="resize-handle" @mousedown="startResize('chunks', $event)"></div>
        </div>
        <div class="col-time header-cell">
          上传时间
          <div class="resize-handle" @mousedown="startResize('time', $event)"></div>
        </div>
        <div class="col-actions header-cell">操作</div>
      </div>

      <div class="list-body">
        <div v-for="file in files" :key="file.id" class="file-row" :style="{ gridTemplateColumns }">
          <div class="col-name">
            <div class="file-icon" :style="{ color: getFileColor(file.type) }">
              <component :is="getFileIcon(file.type)" />
            </div>
            <span class="file-name">{{ file.name }}</span>
          </div>

          <div class="col-type">
            <span class="file-type-badge">
              {{ file.type.toUpperCase() }}
            </span>
          </div>

          <div class="col-size">{{ file.size }}</div>

          <div class="col-chunks">{{ file.chunks }} 个</div>

          <div class="col-time">{{ file.uploadedAt }}</div>

          <div class="col-actions">
            <button class="action-btn" @click="previewFile(file)" title="预览">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </button>

            <button class="action-btn delete" @click="deleteFile(file)" title="删除">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 分页 -->
    <Pagination
      :current-page="currentPage"
      :total-pages="totalPages"
      :total-items="allFiles.length"
      :page-size="pageSize"
      @page-change="handlePageChange"
      @page-size-change="handlePageSizeChange"
    />

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

    <!-- 文件预览弹窗 -->
    <div v-if="previewState.show" class="preview-overlay" @click="closePreview">
      <div class="preview-dialog" @click.stop>
        <div class="preview-header">
          <div class="preview-title">
            <span>{{ previewState.file?.name }}</span>
            <span class="preview-badge">{{ previewState.file?.type.toUpperCase() }}</span>
          </div>
          <div class="preview-actions">
            <button class="preview-download-btn" @click="downloadFile(previewState.file)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              下载原文件
            </button>
            <button class="preview-close-btn" @click="closePreview">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>
        <div class="preview-body">
          <iframe
            :src="previewState.file?.previewUrl"
            class="preview-iframe"
          ></iframe>
        </div>
      </div>
    </div>
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

.header-cell {
  position: relative;
  display: flex;
  align-items: center;
}

.resize-handle {
  position: absolute;
  right: -8px;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 100%;
  cursor: col-resize;
  display: flex;
  align-items: center;
  justify-content: center;
}

.resize-handle::before {
  content: '';
  width: 2px;
  height: 60%;
  background: var(--border);
  transition: background 0.2s;
}

.resize-handle:hover::before {
  background: var(--primary);
}

.resize-handle:active::before {
  background: var(--primary-hover);
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
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  position: relative;
}

.file-icon::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.1;
}

.file-icon svg {
  width: 20px;
  height: 20px;
  position: relative;
  z-index: 1;
}

.file-name {
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-type-badge {
  display: inline-block;
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary);
  background: var(--surface-3);
  text-transform: uppercase;
  letter-spacing: 0.5px;
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
  transition: all 0.2s;
}

.action-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: rgba(56, 189, 248, 0.1);
}

.action-btn.delete:hover {
  border-color: var(--error);
  color: var(--error);
  background: rgba(239, 68, 68, 0.1);
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

/* 预览弹窗 */
.preview-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}

.preview-dialog {
  background: var(--surface-1);
  border-radius: 12px;
  width: 95vw;
  height: 95vh;
  max-width: 1600px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}

.preview-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 16px;
  font-weight: 500;
  color: var(--text-primary);
}

.preview-badge {
  padding: 4px 8px;
  background: var(--primary);
  color: white;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.preview-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.preview-download-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: var(--primary);
  color: white;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

.preview-download-btn svg {
  width: 16px;
  height: 16px;
}

.preview-close-btn {
  width: 36px;
  height: 36px;
  border: 1px solid var(--border);
  background: var(--surface-1);
  border-radius: 6px;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.preview-close-btn svg {
  width: 18px;
  height: 18px;
}

.preview-body {
  flex: 1;
  overflow: hidden;
  background: var(--surface-3);
}

.preview-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
</style>
