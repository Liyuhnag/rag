/**
 * 文档管理 API
 */

import { get, post, del, upload } from './request.js'
import { UPLOAD_TIMEOUT } from './config.js'

/**
 * 获取文档列表
 * @param {Object} params - 查询参数
 * @param {number} params.page - 页码（从 1 开始）
 * @param {number} params.pageSize - 每页数量
 * @param {string} params.sortBy - 排序字段（uploadedAt | name | sizeBytes）
 * @param {string} params.sortOrder - 排序方向（asc | desc）
 * @returns {Promise<{data: Array, pagination: Object}>}
 */
export function getDocuments(params = {}) {
  return get('/documents', params)
}

/**
 * 获取文档详情
 * @param {string} documentId - 文档 ID
 * @returns {Promise<Object>}
 */
export function getDocument(documentId) {
  return get(`/documents/${documentId}`)
}

/**
 * 上传文档
 * @param {File} file - 文件对象
 * @param {string} operator - 操作人
 * @param {Function} onProgress - 上传进度回调
 * @returns {Promise<Object>}
 */
export function uploadDocument(file, operator = '', onProgress = null) {
  const formData = new FormData()
  formData.append('file', file)
  if (operator) {
    formData.append('operator', operator)
  }

  // 如果需要进度回调，使用 XMLHttpRequest
  if (onProgress) {
    return uploadWithProgress('/documents', formData, onProgress)
  }

  // 否则使用普通 fetch
  return upload('/documents', formData, {}, UPLOAD_TIMEOUT)
}

/**
 * 删除文档
 * @param {string} documentId - 文档 ID
 * @returns {Promise<null>}
 */
export function deleteDocument(documentId) {
  return del(`/documents/${documentId}`)
}

/**
 * 下载文档
 * @param {string} fileUrl - 文件 URL
 * @param {string} fileName - 文件名
 * @returns {Promise<void>}
 */
export async function downloadDocument(fileUrl, fileName) {
  try {
    // 使用 fetch 获取文件
    const response = await fetch(fileUrl, {
      method: 'GET',
      mode: 'cors'
    })

    if (!response.ok) {
      throw new Error(`下载失败 (${response.status})`)
    }

    // 获取 Blob 数据
    const blob = await response.blob()

    // 创建下载链接
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    link.style.display = 'none'

    // 触发下载
    document.body.appendChild(link)
    link.click()

    // 清理
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    throw new Error(error.message || '下载文件失败')
  }
}

/**
 * 使用 XMLHttpRequest 上传文件（支持进度）
 * @param {string} url - 上传路径
 * @param {FormData} formData - 表单数据
 * @param {Function} onProgress - 进度回调 (percent: number) => void
 * @returns {Promise<Object>}
 */
function uploadWithProgress(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api'

    // 上传进度
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) {
        const percent = Math.round((event.loaded / event.total) * 100)
        onProgress(percent)
      }
    })

    // 请求完成
    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText)
          resolve(data)
        } catch (error) {
          reject(new Error('响应解析失败'))
        }
      } else {
        try {
          const error = JSON.parse(xhr.responseText)
          reject(new Error(error.message || `上传失败 (${xhr.status})`))
        } catch {
          reject(new Error(`上传失败 (${xhr.status})`))
        }
      }
    })

    // 请求错误
    xhr.addEventListener('error', () => {
      reject(new Error('网络连接失败'))
    })

    // 请求超时
    xhr.addEventListener('timeout', () => {
      reject(new Error('上传超时'))
    })

    // 发送请求
    xhr.open('POST', `${API_BASE_URL}${url}`)
    xhr.timeout = UPLOAD_TIMEOUT
    xhr.send(formData)
  })
}

/**
 * 查询任务状态
 * @param {string} taskId - 任务 ID
 * @returns {Promise<Object>}
 */
export function getTaskStatus(taskId) {
  return get(`/tasks/${taskId}`)
}
