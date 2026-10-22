/**
 * 大文件 SHA-256 计算 Worker。
 *
 * 主线程按 4MiB 切片发送，Worker 使用 hash-wasm 流式计算，
 * 避免一次性占用整文件内存。
 */

import { createSHA256 } from 'hash-wasm'

let hasher = null
let queue = Promise.resolve()

const ready = createSHA256().then((instance) => {
  hasher = instance
  hasher.init()
})

async function handleMessage(data) {
  await ready
  const { type, buffer } = data || {}

  if (type === 'start') {
    hasher.init()
    return
  }

  if (type === 'chunk') {
    hasher.update(new Uint8Array(buffer))
    self.postMessage({ type: 'chunk-done' })
    return
  }

  if (type === 'finish') {
    const hash = hasher.digest('hex')
    self.postMessage({ type: 'done', hash })
  }
}

self.onmessage = (event) => {
  queue = queue
    .then(() => handleMessage(event.data))
    .catch((error) => {
      self.postMessage({
        type: 'error',
        message: error?.message || '哈希计算失败'
      })
    })
}
