/**
 * 文档管理 API
 */

import { get, del } from './request.js'

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
