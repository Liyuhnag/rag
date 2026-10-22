/**
 * 在 Web Worker 中计算文件全量 SHA-256。
 *
 * 主线程按 4MiB 切片，Worker 每处理完一片回执，主线程再读下一片，
 * 避免一次性把大文件读进内存。
 */

const CHUNK_SIZE = 4 * 1024 * 1024

function abortedError() {
  const error = new Error('哈希计算已取消')
  error.name = 'AbortError'
  return error
}

export function hashFile(file, { onProgress, signal } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortedError())
      return
    }

    const worker = new Worker(
      new URL('../workers/sha256.worker.js', import.meta.url),
      { type: 'module' }
    )
    let offset = 0
    let settled = false

    const cleanup = () => {
      worker.terminate()
      signal?.removeEventListener('abort', handleAbort)
    }

    const fail = (error) => {
      if (settled) return
      settled = true
      cleanup()
      reject(error)
    }

    const succeed = (hash) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(hash)
    }

    const handleAbort = () => {
      fail(abortedError())
    }

    const sendNext = async () => {
      if (settled) return
      try {
        const end = Math.min(offset + CHUNK_SIZE, file.size)
        const buffer = await file.slice(offset, end).arrayBuffer()
        if (settled) return
        offset = end
        onProgress?.(offset, file.size)
        worker.postMessage({ type: 'chunk', buffer }, [buffer])
      } catch (error) {
        fail(error)
      }
    }

    worker.onerror = (event) => {
      fail(new Error(event.message || '哈希计算失败'))
    }

    worker.onmessage = async (event) => {
      const { type, hash, message } = event.data || {}
      if (type === 'chunk-done') {
        if (offset >= file.size) {
          worker.postMessage({ type: 'finish' })
        } else {
          await sendNext()
        }
        return
      }
      if (type === 'done') {
        succeed(hash)
        return
      }
      if (type === 'error') {
        fail(new Error(message || '哈希计算失败'))
      }
    }

    signal?.addEventListener('abort', handleAbort, { once: true })
    worker.postMessage({ type: 'start' })
    onProgress?.(0, file.size)

    if (file.size === 0) {
      worker.postMessage({ type: 'finish' })
    } else {
      sendNext()
    }
  })
}
