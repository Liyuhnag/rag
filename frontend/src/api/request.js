/**
 * 通用 HTTP 请求封装
 */

import { API_BASE_URL, REQUEST_TIMEOUT } from './config.js'

/**
 * API 错误类
 */
export class APIError extends Error {
  constructor(message, code, status) {
    super(message)
    this.name = 'APIError'
    this.code = code
    this.status = status
  }
}

/**
 * 通用请求函数
 * @param {string} url - 请求路径（相对于 API_BASE_URL）
 * @param {Object} options - fetch 选项
 * @param {number} timeout - 超时时间（毫秒）
 * @returns {Promise<any>} 响应数据
 */
export async function request(url, options = {}, timeout = REQUEST_TIMEOUT) {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  try {
    const response = await fetch(`${API_BASE_URL}${url}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers
      }
    })

    clearTimeout(timeoutId)

    // 处理 204 No Content
    if (response.status === 204) {
      return null
    }

    const data = await response.json()

    // 处理错误响应
    if (!response.ok) {
      throw new APIError(
        data.message || `请求失败 (${response.status})`,
        data.code || 'UNKNOWN_ERROR',
        response.status
      )
    }

    return data
  } catch (error) {
    clearTimeout(timeoutId)

    // 处理中断错误
    if (error.name === 'AbortError') {
      throw new APIError('请求超时', 'TIMEOUT', 408)
    }

    // 处理网络错误
    if (error instanceof TypeError) {
      throw new APIError('网络连接失败', 'NETWORK_ERROR', 0)
    }

    // 重新抛出 APIError
    if (error instanceof APIError) {
      throw error
    }

    // 其他未知错误
    throw new APIError(error.message || '未知错误', 'UNKNOWN_ERROR', 500)
  }
}

/**
 * GET 请求
 */
export function get(url, params = {}, options = {}) {
  const queryString = new URLSearchParams(params).toString()
  const fullUrl = queryString ? `${url}?${queryString}` : url

  return request(fullUrl, {
    method: 'GET',
    ...options
  })
}

/**
 * POST 请求
 */
export function post(url, data = {}, options = {}) {
  return request(url, {
    method: 'POST',
    body: JSON.stringify(data),
    ...options
  })
}

/**
 * DELETE 请求
 */
export function del(url, options = {}) {
  return request(url, {
    method: 'DELETE',
    ...options
  })
}

/**
 * 上传文件（multipart/form-data）
 */
export function upload(url, formData, options = {}, timeout) {
  return request(url, {
    method: 'POST',
    body: formData,
    headers: {
      // 不设置 Content-Type，让浏览器自动设置 multipart boundary
      ...(options.headers || {})
    },
    ...options
  }, timeout)
}
