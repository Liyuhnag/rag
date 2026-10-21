/**
 * 大文件上传 API。
 *
 * 分片使用 XHR 上传，以便获得真实 upload progress；
 * 其余 JSON 接口使用 fetch。
 */

import { API_BASE_URL } from './config.js'

const DEFAULT_TIMEOUT = 30000
const PART_TIMEOUT = 120000
const COMPLETE_TIMEOUT = 180000

export class UploadAPIError extends Error {
  constructor(message, code = 'UNKNOWN_ERROR', status = 0, details = null) {
    super(message)
    this.name = 'UploadAPIError'
    this.code = code
    this.status = status
    this.details = details
  }
}

function abortedError() {
  return new UploadAPIError('上传已取消', 'ABORTED', 0)
}

function errorFromPayload(status, payload) {
  const error = payload?.error || {}
  return new UploadAPIError(
    error.message || `请求失败 (${status})`,
    error.code || 'UNKNOWN_ERROR',
    status,
    error.details ?? null
  )
}

async function requestJSON(path, { method = 'GET', body, signal, timeout = DEFAULT_TIMEOUT } = {}) {
  const controller = new AbortController()
  const handleAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) {
      controller.abort()
    } else {
      signal.addEventListener('abort', handleAbort, { once: true })
    }
  }
  const timer = setTimeout(() => controller.abort(), timeout)

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal
    })

    if (response.status === 204) {
      return null
    }

    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw errorFromPayload(response.status, payload)
    }
    return payload
  } catch (error) {
    if (error instanceof UploadAPIError) {
      throw error
    }
    if (error.name === 'AbortError') {
      if (signal?.aborted) {
        throw abortedError()
      }
      throw new UploadAPIError('请求超时', 'TIMEOUT', 408)
    }
    if (error instanceof TypeError) {
      throw new UploadAPIError('网络连接失败', 'NETWORK_ERROR', 0)
    }
    throw new UploadAPIError(error.message || '请求失败', 'UNKNOWN_ERROR', 500)
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', handleAbort)
  }
}

function errorFromXHR(xhr) {
  let payload = null
  try {
    payload = JSON.parse(xhr.responseText)
  } catch {
    payload = null
  }
  return errorFromPayload(xhr.status, payload)
}

export function initUpload({ filename, sizeBytes, fingerprint, signal }) {
  return requestJSON('/uploads/init', {
    method: 'POST',
    body: { filename, sizeBytes, fingerprint },
    signal
  })
}

export function getUpload(uploadId, { signal } = {}) {
  return requestJSON(`/uploads/${encodeURIComponent(uploadId)}`, { signal })
}

export function completeUpload(uploadId, { signal } = {}) {
  return requestJSON(`/uploads/${encodeURIComponent(uploadId)}/complete`, {
    method: 'POST',
    signal,
    timeout: COMPLETE_TIMEOUT
  })
}

export function deleteUpload(uploadId, { signal } = {}) {
  return requestJSON(`/uploads/${encodeURIComponent(uploadId)}`, {
    method: 'DELETE',
    signal
  })
}

export function getTask(taskId, { signal } = {}) {
  return requestJSON(`/tasks/${encodeURIComponent(taskId)}`, { signal })
}

export function uploadPart({
  uploadId,
  partNumber,
  blob,
  onProgress,
  signal,
  timeout = PART_TIMEOUT
}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    let settled = false

    const cleanup = () => {
      signal?.removeEventListener('abort', handleAbort)
    }
    const succeed = (payload) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(payload)
    }
    const fail = (error) => {
      if (settled) return
      settled = true
      cleanup()
      reject(error)
    }
    const handleAbort = () => {
      xhr.abort()
    }

    xhr.open(
      'PUT',
      `${API_BASE_URL}/uploads/${encodeURIComponent(uploadId)}/parts/${partNumber}`
    )
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.timeout = timeout

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress?.(event.loaded, event.total)
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          succeed(xhr.responseText ? JSON.parse(xhr.responseText) : null)
        } catch {
          succeed(null)
        }
        return
      }
      fail(errorFromXHR(xhr))
    }
    xhr.onerror = () => {
      fail(new UploadAPIError('网络连接失败', 'NETWORK_ERROR', 0))
    }
    xhr.ontimeout = () => {
      fail(new UploadAPIError('分片上传超时', 'TIMEOUT', 408))
    }
    xhr.onabort = () => {
      fail(abortedError())
    }

    if (signal) {
      if (signal.aborted) {
        fail(abortedError())
        return
      }
      signal.addEventListener('abort', handleAbort, { once: true })
    }

    xhr.send(blob)
  })
}
