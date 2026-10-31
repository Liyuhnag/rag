# -*- coding: utf-8 -*-
"""任务接口自己的失败。"""

from api.common.errors import AppError


def not_found() -> AppError:
    return AppError(404, "TASK_NOT_FOUND", "任务不存在")


def cannot_retry() -> AppError:
    return AppError(409, "TASK_NOT_RETRYABLE", "只有当前版本的失败任务且文档为失败状态时可以重试")
