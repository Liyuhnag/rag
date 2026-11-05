/**
 * 大文件上传管理器。
 *
 * 一次只上传一个文件，每个文件 2 个分片并发。
 * 会话和分片状态以服务端 upload_sessions + S3 ListParts 为准。
 */

import { computed, ref } from 'vue'
import {
  completeUpload,
  deleteUpload,
  getTask,
  getUpload,
  initUpload,
  uploadPart,
  UploadAPIError
} from '../api/uploads.js'
import { hashFile } from '../utils/hashFile.js'

export const UPLOAD_STATUS = {
  PENDING: 'pending',
  HASHING: 'hashing',
  UPLOADING: 'uploading',
  PAUSED: 'paused',
  COMPLETING: 'completing',
  PROCESSING: 'processing',
  SUCCESS: 'success',
  ERROR: 'error',
  CANCELLED: 'cancelled'
}

export const TASK_STAGE = {
  UPLOAD: 'upload',
  PROCESS: 'process'
}

const STORAGE_KEY = 'rag.upload.v2'
const STAGE_TEXT = {
  PENDING: '排队中',
  PARSING: '正在解析文档',
  MERGING: '正在合并解析结果',
  CHUNKING: '正在切分文本',
  INDEXING: '正在建立索引',
  DONE: '处理完成'
}
const ERROR_TEXT = {
  UPLOAD_BUSY: '当前上传人数较多，正在重试…',
  UPLOAD_EXPIRED: '上传已过期，请重新选择文件',
  UPLOAD_ABORTED: '上传已取消',
  UPLOAD_ALREADY_COMPLETED: '文件已完成上传',
  UPLOAD_INCOMPLETE: '还有分片未上传',
  FINGERPRINT_CONFLICT: '同名文件已有未完成上传',
  NETWORK_ERROR: '网络连接失败，请检查网络',
  TIMEOUT: '请求超时，请稍后重试'
}

const uploadTasks = ref([])
const storedUploads = ref(readStored())
const documentsVersion = ref(0)

const config = {
  partConcurrency: 2
}

const activeUploads = computed(() => {
  return uploadTasks.value.filter((task) =>
    [UPLOAD_STATUS.HASHING, UPLOAD_STATUS.UPLOADING, UPLOAD_STATUS.COMPLETING].includes(task.status)
  ).length
})

const hasActiveUploads = computed(() => activeUploads.value > 0)

const pendingCount = computed(() => {
  return uploadTasks.value.filter((task) => task.status === UPLOAD_STATUS.PENDING).length
})

function generateTaskId() {
  return `upload_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`
}

function formatFileSize(bytes) {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const index = Math.floor(Math.log(bytes) / Math.log(k))
  return `${Math.round((bytes / Math.pow(k, index)) * 100) / 100} ${sizes[index]}`
}

function getFileType(filename) {
  return filename.split('.').pop().toLowerCase()
}

function createTask(file) {
  return {
    id: generateTaskId(),
    file,
    name: file.name,
    type: getFileType(file.name),
    size: file.size,
    sizeText: formatFileSize(file.size),
    status: UPLOAD_STATUS.PENDING,
    progress: 0,
    hashProgress: 0,
    error: null,
    errorCode: null,
    uploadedAt: null,

    uploadId: null,
    documentId: null,
    taskId: null,
    taskStatus: null,
    fingerprint: null,
    chunkSize: null,
    totalParts: null,
    uploadedParts: new Set(),
    partProgress: {},
    uploadedBytes: 0,
    repairAttempted: false,

    stage: '',
    step: '',
    expiresAt: null,
    abortController: null,
    pollTimer: null,
    createdAt: new Date().toISOString()
  }
}

function addUploadTask(file) {
  if (!file) return null
  const task = createTask(file)
  uploadTasks.value.push(task)
  processQueue()
  return task.id
}

function processQueue() {
  if (activeUploads.value > 0) {
    return
  }
  const pending = uploadTasks.value.find((task) => task.status === UPLOAD_STATUS.PENDING)
  if (pending) {
    void startUpload(pending)
  }
}

async function startUpload(task) {
  task.status = UPLOAD_STATUS.HASHING
  task.error = null
  task.errorCode = null
  task.progress = 0
  task.hashProgress = 0
  task.step = '正在校验文件…'
  task.abortController = new AbortController()

  try {
    const fingerprint = await hashFile(task.file, {
      signal: task.abortController.signal,
      onProgress: (done, total) => {
        task.hashProgress = total ? Math.round((done / total) * 100) : 0
        task.step = `正在校验文件 ${task.hashProgress}%`
      }
    })
    if (task.status !== UPLOAD_STATUS.HASHING) return
    task.fingerprint = fingerprint
    task.step = '正在初始化上传…'

    const init = await initUpload({
      filename: task.name,
      sizeBytes: task.size,
      fingerprint,
      signal: task.abortController.signal
    })
    if (task.status !== UPLOAD_STATUS.HASHING) return

    applyInit(task, init)

    if (init.status === 'COMPLETED') {
      handleCompletedUpload(task, init.taskId, init.taskStatus)
      return
    }
    if (init.status === 'COMPLETING') {
      task.status = UPLOAD_STATUS.COMPLETING
      task.step = '正在合并文件…'
      startUploadPolling(task)
      return
    }
    if (init.status === 'ABORTING') {
      throw new UploadAPIError('上传正在取消', 'UPLOAD_ABORTING', 202)
    }

    task.status = UPLOAD_STATUS.UPLOADING
    task.step = '上传中…'
    await uploadMissingParts(task)
    if (task.status === UPLOAD_STATUS.UPLOADING) {
      await finishUpload(task)
    }
  } catch (error) {
    if (isAbortError(error) || task.status === UPLOAD_STATUS.PAUSED || task.status === UPLOAD_STATUS.CANCELLED) {
      return
    }
    failTask(task, error)
  }
}

function applyInit(task, init) {
  task.uploadId = init.uploadId
  task.documentId = init.documentId
  task.chunkSize = init.chunkSize
  task.totalParts = init.totalParts
  task.expiresAt = init.expiresAt
  task.uploadedParts = new Set((init.uploadedParts || []).map((part) => part.partNumber))
  task.uploadedBytes = (init.uploadedParts || []).reduce((sum, part) => sum + part.sizeBytes, 0)
  task.progress = task.size
    ? Math.min(99, Math.round((task.uploadedBytes / task.size) * 100))
    : 0
  saveStored(task)
}

async function uploadMissingParts(task) {
  const missing = []
  for (let part = 1; part <= task.totalParts; part += 1) {
    if (!task.uploadedParts.has(part)) {
      missing.push(part)
    }
  }

  let cursor = 0
  const run = async () => {
    while (true) {
      if (task.status !== UPLOAD_STATUS.UPLOADING) return
      const index = cursor
      cursor += 1
      if (index >= missing.length) return
      await uploadOnePart(task, missing[index])
    }
  }

  const workers = Array.from(
    { length: Math.min(config.partConcurrency, missing.length) },
    () => run()
  )
  await Promise.all(workers)
}

async function uploadOnePart(task, partNumber) {
  const start = (partNumber - 1) * task.chunkSize
  const end = Math.min(start + task.chunkSize, task.size)
  const blob = task.file.slice(start, end)

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    if (task.status !== UPLOAD_STATUS.UPLOADING) return
    try {
      task.partProgress[partNumber] = 0
      await uploadPart({
        uploadId: task.uploadId,
        partNumber,
        blob,
        signal: task.abortController.signal,
        onProgress: (loaded) => {
          task.partProgress[partNumber] = loaded
          updateProgress(task)
        }
      })
      if (!task.uploadedParts.has(partNumber)) {
        task.uploadedParts.add(partNumber)
        task.uploadedBytes += blob.size
      }
      delete task.partProgress[partNumber]
      updateProgress(task)
      return
    } catch (error) {
      delete task.partProgress[partNumber]
      if (task.status !== UPLOAD_STATUS.UPLOADING || isAbortError(error)) {
        return
      }
      if (!isRetryable(error) || attempt === 3) {
        throw error
      }
      await sleep(retryDelay(attempt), task.abortController.signal)
    }
  }
}

function updateProgress(task) {
  const inflight = Object.values(task.partProgress).reduce((sum, loaded) => sum + loaded, 0)
  const total = task.uploadedBytes + inflight
  task.progress = task.size
    ? Math.min(99, Math.round((total / task.size) * 100))
    : 0
}

async function finishUpload(task) {
  task.status = UPLOAD_STATUS.COMPLETING
  task.step = '正在合并文件…'
  task.progress = 99

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const result = await completeUpload(task.uploadId, {
        signal: task.abortController.signal
      })
      if (result.status === 'COMPLETED') {
        handleCompletedUpload(task, result.taskId, result.taskStatus)
        return
      }
      if (result.status === 'COMPLETING') {
        startUploadPolling(task)
        return
      }
      throw new UploadAPIError('完成上传返回了未知状态', 'UNKNOWN_ERROR', 500)
    } catch (error) {
      if (task.status === UPLOAD_STATUS.CANCELLED || isAbortError(error)) {
        return
      }
      if (
        error.code === 'UPLOAD_INCOMPLETE' &&
        Array.isArray(error.details?.missingParts) &&
        !task.repairAttempted
      ) {
        task.repairAttempted = true
        task.status = UPLOAD_STATUS.UPLOADING
        await uploadMissingParts(task)
        if (task.status === UPLOAD_STATUS.UPLOADING) {
          await finishUpload(task)
        }
        return
      }
      if (error.code === 'UPLOAD_COMPLETING' || error.status === 202) {
        task.status = UPLOAD_STATUS.COMPLETING
        startUploadPolling(task)
        return
      }
      if (!isRetryable(error) || attempt === 3) {
        failTask(task, error)
        return
      }
      await sleep(retryDelay(attempt), task.abortController.signal)
    }
  }
}

function handleCompletedUpload(task, taskId, taskStatus) {
  clearStored(task)
  task.taskId = taskId || null
  task.taskStatus = taskStatus || 'PENDING'
  task.progress = 100
  notifyDocumentsChanged()

  if (!task.taskId) {
    task.status = UPLOAD_STATUS.SUCCESS
    task.step = '上传完成'
    task.uploadedAt = new Date().toISOString()
    processQueue()
    return
  }

  task.status = UPLOAD_STATUS.PROCESSING
  task.step = '等待处理'
  task.stage = 'PENDING'
  startTaskPolling(task)
  processQueue()
}

function startUploadPolling(task) {
  stopPolling(task)
  task.pollTimer = setInterval(async () => {
    if (document.hidden) return
    try {
      const data = await getUpload(task.uploadId)
      applyInit(task, data)
      if (data.status === 'COMPLETED') {
        stopPolling(task)
        handleCompletedUpload(task, data.taskId, data.taskStatus)
        return
      }
      if (data.status === 'UPLOADING') {
        stopPolling(task)
        task.status = UPLOAD_STATUS.UPLOADING
        task.step = '上传中…'
        if (!task.abortController || task.abortController.signal.aborted) {
          task.abortController = new AbortController()
        }
        await uploadMissingParts(task)
        if (task.status === UPLOAD_STATUS.UPLOADING) {
          await finishUpload(task)
        }
        return
      }
      if (['ABORTED', 'EXPIRED', 'FAILED'].includes(data.status)) {
        stopPolling(task)
        failTask(task, new UploadAPIError('上传已结束', 'UPLOAD_EXPIRED', 410))
      }
    } catch (error) {
      if (error.status === 404 || error.status === 410) {
        stopPolling(task)
        failTask(task, error)
      }
    }
  }, 2000)
}

function startTaskPolling(task) {
  if (!task.taskId) return
  stopPolling(task)
  const poll = async () => {
    if (document.hidden) return
    try {
      const data = await getTask(task.taskId)
      task.taskStatus = data.status
      task.stage = data.stage || ''
      task.step = data.step || STAGE_TEXT[data.stage] || task.step
      task.progress = data.progress ?? task.progress

      if (data.status === 'COMPLETED') {
        task.status = UPLOAD_STATUS.SUCCESS
        task.progress = 100
        task.uploadedAt = new Date().toISOString()
        stopPolling(task)
        clearStored(task)
        notifyDocumentsChanged()
        processQueue()
        return
      }
      if (['FAILED', 'SUPERSEDED'].includes(data.status)) {
        task.status = UPLOAD_STATUS.ERROR
        task.error = data.status === 'SUPERSEDED'
          ? '已被新版本取代'
          : data.errorMessage || '处理失败'
        stopPolling(task)
        clearStored(task)
        processQueue()
      }
    } catch (error) {
      if (error.status === 404) {
        stopPolling(task)
        failTask(task, error)
      }
    }
  }
  task.pollTimer = setInterval(poll, 2000)
  void poll()
}

function stopPolling(task) {
  if (task.pollTimer) {
    clearInterval(task.pollTimer)
    task.pollTimer = null
  }
}

function pauseTask(taskId) {
  const task = uploadTasks.value.find((item) => item.id === taskId)
  if (!task || ![UPLOAD_STATUS.HASHING, UPLOAD_STATUS.UPLOADING].includes(task.status)) {
    return
  }
  task.status = UPLOAD_STATUS.PAUSED
  task.step = '已暂停'
  task.abortController?.abort()
  task.abortController = null
  stopPolling(task)
  processQueue()
}

function resumeTask(taskId) {
  const task = uploadTasks.value.find((item) => item.id === taskId)
  if (!task || task.status !== UPLOAD_STATUS.PAUSED) {
    return
  }

  if (!task.uploadId || !task.chunkSize) {
    task.status = UPLOAD_STATUS.PENDING
    task.progress = 0
    task.hashProgress = 0
    task.step = '等待上传'
    processQueue()
    return
  }

  task.abortController = new AbortController()
  task.status = UPLOAD_STATUS.UPLOADING
  task.step = '继续上传…'
  void resumeUpload(task)
}

async function resumeUpload(task) {
  try {
    const data = await getUpload(task.uploadId, {
      signal: task.abortController.signal
    })
    applyInit(task, data)
    if (data.status === 'COMPLETED') {
      handleCompletedUpload(task, data.taskId, data.taskStatus)
      return
    }
    if (data.status === 'COMPLETING') {
      task.status = UPLOAD_STATUS.COMPLETING
      startUploadPolling(task)
      return
    }
    if (data.status !== 'UPLOADING') {
      throw new UploadAPIError('上传已结束', 'UPLOAD_EXPIRED', 410)
    }
    task.status = UPLOAD_STATUS.UPLOADING
    task.step = '上传中…'
    await uploadMissingParts(task)
    if (task.status === UPLOAD_STATUS.UPLOADING) {
      await finishUpload(task)
    }
  } catch (error) {
    if (isAbortError(error) || task.status === UPLOAD_STATUS.PAUSED || task.status === UPLOAD_STATUS.CANCELLED) {
      return
    }
    failTask(task, error)
  }
}

function retryTask(taskId) {
  const task = uploadTasks.value.find((item) => item.id === taskId)
  if (!task || ![UPLOAD_STATUS.ERROR, UPLOAD_STATUS.CANCELLED].includes(task.status)) {
    return
  }

  const oldUploadId = task.uploadId
  clearStored(task)
  task.status = UPLOAD_STATUS.PENDING
  task.progress = 0
  task.hashProgress = 0
  task.error = null
  task.errorCode = null
  task.uploadId = null
  task.documentId = null
  task.taskId = null
  task.taskStatus = null
  task.fingerprint = null
  task.chunkSize = null
  task.totalParts = null
  task.uploadedParts = new Set()
  task.partProgress = {}
  task.uploadedBytes = 0
  task.repairAttempted = false
  task.stage = ''
  task.step = '等待上传'
  if (oldUploadId) {
    void deleteUpload(oldUploadId).catch(() => {})
  }
  processQueue()
}

function cancelTask(taskId) {
  const task = uploadTasks.value.find((item) => item.id === taskId)
  if (!task) return

  const shouldDelete = task.uploadId && ![UPLOAD_STATUS.SUCCESS, UPLOAD_STATUS.PROCESSING].includes(task.status)
  task.status = UPLOAD_STATUS.CANCELLED
  task.abortController?.abort()
  task.abortController = null
  stopPolling(task)
  clearStored(task)
  if (shouldDelete) {
    void deleteUpload(task.uploadId).catch(() => {})
  }

  const index = uploadTasks.value.findIndex((item) => item.id === taskId)
  if (index !== -1) {
    uploadTasks.value.splice(index, 1)
  }
  processQueue()
}

function clearCompleted() {
  uploadTasks.value = uploadTasks.value.filter(
    (task) => task.status !== UPLOAD_STATUS.SUCCESS
  )
}

function clearFailed() {
  uploadTasks.value = uploadTasks.value.filter(
    (task) => ![UPLOAD_STATUS.ERROR, UPLOAD_STATUS.CANCELLED].includes(task.status)
  )
}

function pauseAll() {
  uploadTasks.value.forEach((task) => {
    if ([UPLOAD_STATUS.HASHING, UPLOAD_STATUS.UPLOADING].includes(task.status)) {
      pauseTask(task.id)
    }
  })
}

function resumeAll() {
  uploadTasks.value.forEach((task) => {
    if (task.status === UPLOAD_STATUS.PAUSED) {
      resumeTask(task.id)
    }
  })
}

function setMaxConcurrent(count) {
  config.partConcurrency = Math.max(1, Math.min(4, count))
}

function setOperator() {
  // 保留旧调用签名；当前接口不需要操作人。
}

function failTask(task, error) {
  task.status = UPLOAD_STATUS.ERROR
  task.error = ERROR_TEXT[error.code] || error.message || '上传失败'
  task.errorCode = error.code || 'UNKNOWN_ERROR'
  task.step = '上传失败'
  stopPolling(task)
  clearStored(task)
  processQueue()
}

function isRetryable(error) {
  if (!error || isAbortError(error)) return false
  if (!error.status) return true
  return error.status === 408 || error.status === 429 || error.status >= 500
}

function retryDelay(attempt) {
  return 1000 * Math.pow(2, attempt - 1)
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      reject(abortError())
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function isAbortError(error) {
  return error?.name === 'AbortError' || error?.code === 'ABORTED'
}

function abortError() {
  const error = new Error('上传已取消')
  error.name = 'AbortError'
  return error
}

function readStored() {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function notifyDocumentsChanged() {
  documentsVersion.value += 1
}

function saveStored(task) {
  if (typeof localStorage === 'undefined' || !task.uploadId || !task.fingerprint) return
  try {
    const entries = readStored().filter((entry) => entry.uploadId !== task.uploadId)
    entries.push({
      fingerprint: task.fingerprint,
      uploadId: task.uploadId,
      filename: task.name,
      sizeBytes: task.size,
      lastModified: task.file.lastModified,
      createdAt: task.createdAt,
      updatedAt: new Date().toISOString()
    })
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
    storedUploads.value = readStored()
  } catch {
    // 本地存储不可用时不影响上传。
  }
}

function clearStoredUpload(uploadId) {
  if (typeof localStorage === 'undefined' || !uploadId) return
  try {
    const entries = readStored().filter((entry) => entry.uploadId !== uploadId)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
    storedUploads.value = readStored()
  } catch {
    // 忽略存储失败。
  }
}

function clearStored(task) {
  clearStoredUpload(task.uploadId)
}

function restoreStoredUpload(file, entry) {
  if (!file) return null
  if (entry?.uploadId) {
    clearStoredUpload(entry.uploadId)
  }
  return addUploadTask(file)
}

export function useUploadManager() {
  return {
    uploadTasks,
    storedUploads,
    documentsVersion,
    activeUploads,
    hasActiveUploads,
    pendingCount,
    config,

    addUploadTask,
    restoreStoredUpload,
    discardStoredUpload: clearStoredUpload,
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
