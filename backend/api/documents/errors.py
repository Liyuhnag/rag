# -*- coding: utf-8 -*-
"""文档接口自己的失败。"""

from api.common.errors import AppError
from config import ALLOWED_FILE_TYPES


def not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "文档不存在")


def empty_file() -> AppError:
    return AppError(422, "EMPTY_FILE", "文件内容为空")


def file_too_large() -> AppError:
    return AppError(413, "FILE_TOO_LARGE", "文件大小超出限制")


def invalid_filename() -> AppError:
    return AppError(422, "VALIDATION_ERROR", "文件名不合法")


def filename_too_long() -> AppError:
    return AppError(422, "VALIDATION_ERROR", "文件名过长")


def unsupported_file_type() -> AppError:
    return AppError(
        422,
        "UNSUPPORTED_FILE_TYPE",
        "不支持的文件类型",
        {"allowed": sorted(ALLOWED_FILE_TYPES)},
    )
