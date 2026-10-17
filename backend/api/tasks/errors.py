# -*- coding: utf-8 -*-
"""任务接口自己的失败。"""

from api.common.errors import AppError


def not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "任务不存在")
