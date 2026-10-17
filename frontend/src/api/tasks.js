/**
 * 任务状态查询 API
 */

import { get } from './request.js'

/**
 * 查询任务状态
 * @param {string} taskId - 任务 ID
 * @returns {Promise<Object>}
 */
export function getTaskStatus(taskId) {
  return get(`/tasks/${taskId}`)
}
