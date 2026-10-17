/**
 * API 配置
 */

// API 基础地址
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api'

// 请求超时时间（毫秒）
export const REQUEST_TIMEOUT = 30000

// 文件上传超时时间（毫秒）
export const UPLOAD_TIMEOUT = 300000
