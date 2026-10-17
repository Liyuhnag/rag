/**
 * 全局上传管理器
 * 管理所有文件上传任务，支持后台上传
 */

import { ref, computed } from 'vue'
import { uploadDocument } from '../api/documents.js'
import { getTaskStatus } from '../api/tasks.js'

// 上传任务状态
export const UPLOAD_STATUS = {
  PENDING: 'pending',     // 等待上传
  UPLOADING: 'uploading', // 上传中
  PAUSED: 'paused',       // 已暂停
  SUCCESS: 'success',     // 上传成功
  ERROR: 'error'          // 上传失败
}

// 任务处理阶段
export const TASK_STAGE = {
  UPLOAD: 'upload',       // 第一阶段：上传到对象存储
  PROCESS: 'process'      // 第二阶段：文档处理（预留）
}

// 全局上传任务列表
const uploadTasks = ref([])

// 上传配置
const config = {
  maxConcurrent: 3, // 最大并发上传数
  operator: '当前用户' // 默认操作人
}

// 当前正在上传的任务数
const activeUploads = computed(() => {
  return uploadTasks.value.filter(t => t.status === UPLOAD_STATUS.UPLOADING).length
})

// 是否有任务在上传
const hasActiveUploads = computed(() => activeUploads.value > 0)

// 待上传任务数
const pendingCount = computed(() => {
  return uploadTasks.value.filter(t => t.status === UPLOAD_STATUS.PENDING).length
})

// 生成唯一任务 ID
function generateTaskId() {
  return `upload_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`
}

// 格式化文件大小
function formatFileSize(bytes) {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

// 获取文件类型
function getFileType(filename) {
  const ext = filename.split('.').pop().toLowerCase()
  return ext
}

/**
 * 添加上传任务
 */
function addUploadTask(file) {
  const task = {
    id: generateTaskId(),
    file: file,
    name: file.name,
    type: getFileType(file.name),
    size: file.size,
    sizeText: formatFileSize(file.size),
    status: UPLOAD_STATUS.PENDING,
    progress: 0,
    error: null,
    uploadedAt: null,

    // 后端任务信息
    taskId: null,           // 后端任务 ID
    stage: TASK_STAGE.UPLOAD, // 当前处理阶段
    step: '',               // 当前步骤描述

    // 对象存储信息
    objectKey: null,
    fileUrl: null,

    // 轮询控制
    pollInterval: null
  }

  uploadTasks.value.push(task)

  // 尝试开始上传
  processQueue()

  return task.id
}

/**
 * 处理上传队列
 * 确保并发数不超过限制
 */
function processQueue() {
  // 如果已达到最大并发数，不启动新任务
  if (activeUploads.value >= config.maxConcurrent) {
    return
  }

  // 找到第一个待上传任务
  const pendingTask = uploadTasks.value.find(t => t.status === UPLOAD_STATUS.PENDING)

  if (pendingTask) {
    startUpload(pendingTask)
  }
}

/**
 * 开始上传单个任务
 */
async function startUpload(task) {
  task.status = UPLOAD_STATUS.UPLOADING
  task.progress = 0
  task.error = null
  task.stage = TASK_STAGE.UPLOAD
  task.step = '准备上传...'

  try {
    // 1. 上传文件，获取 task_id
    const result = await uploadDocument(
      task.file,
      config.operator,
      null  // 不使用前端进度回调，改用轮询获取后端进度
    )

    task.taskId = result.task_id
    task.step = result.message || '等待处理...'

    // 2. 开始轮询任务状态
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
          task.uploadedAt = new Date().toISOString()

          // 保存对象存储信息
          if (status.result) {
            task.objectKey = status.result.object_key
            task.fileUrl = status.result.file_url
          }

          // 继续处理队列
          processQueue()

        } else if (status.status === 'failed') {
          // 上传失败
          clearInterval(task.pollInterval)
          task.pollInterval = null
          task.status = UPLOAD_STATUS.ERROR
          task.error = status.error || '处理失败'

          // 继续处理队列
          processQueue()
        }
        // 其他状态继续轮询

      } catch (error) {
        // 查询失败，继续轮询（可能是网络波动）
        console.error('查询任务状态失败:', error)
      }

    }, 2000)  // 每 2 秒轮询一次

  } catch (error) {
    // 上传请求失败
    task.status = UPLOAD_STATUS.ERROR
    task.error = error.message || '上传失败'

    // 继续处理队列
    processQueue()
  }
}

/**
 * 暂停上传任务
 */
function pauseTask(taskId) {
  const task = uploadTasks.value.find(t => t.id === taskId)
  if (task && task.status === UPLOAD_STATUS.UPLOADING) {
    // 停止轮询
    if (task.pollInterval) {
      clearInterval(task.pollInterval)
      task.pollInterval = null
    }
    task.status = UPLOAD_STATUS.PAUSED

    // 处理队列中的下一个任务
    processQueue()
  }
}

/**
 * 恢复上传任务
 */
function resumeTask(taskId) {
  const task = uploadTasks.value.find(t => t.id === taskId)
  if (task && task.status === UPLOAD_STATUS.PAUSED) {
    task.status = UPLOAD_STATUS.PENDING
    processQueue()
  }
}

/**
 * 重试上传任务
 */
function retryTask(taskId) {
  const task = uploadTasks.value.find(t => t.id === taskId)
  if (task && task.status === UPLOAD_STATUS.ERROR) {
    task.status = UPLOAD_STATUS.PENDING
    task.error = null
    task.progress = 0
    processQueue()
  }
}

/**
 * 取消上传任务
 */
function cancelTask(taskId) {
  const task = uploadTasks.value.find(t => t.id === taskId)
  if (task) {
    // 停止轮询
    if (task.pollInterval) {
      clearInterval(task.pollInterval)
      task.pollInterval = null
    }

    // 从列表中移除
    const index = uploadTasks.value.findIndex(t => t.id === taskId)
    if (index !== -1) {
      uploadTasks.value.splice(index, 1)
    }

    // 处理队列中的下一个任务
    processQueue()
  }
}

/**
 * 清除已完成的任务
 */
function clearCompleted() {
  uploadTasks.value = uploadTasks.value.filter(
    t => t.status !== UPLOAD_STATUS.SUCCESS
  )
}

/**
 * 清除失败的任务
 */
function clearFailed() {
  uploadTasks.value = uploadTasks.value.filter(
    t => t.status !== UPLOAD_STATUS.ERROR
  )
}

/**
 * 全部暂停
 */
function pauseAll() {
  uploadTasks.value.forEach(task => {
    if (task.status === UPLOAD_STATUS.UPLOADING || task.status === UPLOAD_STATUS.PENDING) {
      if (task.status === UPLOAD_STATUS.UPLOADING && task.pollInterval) {
        clearInterval(task.pollInterval)
        task.pollInterval = null
      }
      task.status = UPLOAD_STATUS.PAUSED
    }
  })
}

/**
 * 全部恢复
 */
function resumeAll() {
  uploadTasks.value.forEach(task => {
    if (task.status === UPLOAD_STATUS.PAUSED) {
      task.status = UPLOAD_STATUS.PENDING
    }
  })
  processQueue()
}

/**
 * 设置并发数
 */
function setMaxConcurrent(count) {
  config.maxConcurrent = Math.max(1, Math.min(10, count))
  processQueue()
}

/**
 * 设置操作人
 */
function setOperator(name) {
  config.operator = name
}

/**
 * 导出 composable
 */
export function useUploadManager() {
  return {
    // 状态
    uploadTasks,
    activeUploads,
    hasActiveUploads,
    pendingCount,
    config,

    // 方法
    addUploadTask,
    pauseTask,
    resumeTask,
    retryTask,
    cancelTask,
    clearCompleted,
    clearFailed,
    pauseAll,
    resumeAll,
    setMaxConcurrent,
    setOperator
  }
}
