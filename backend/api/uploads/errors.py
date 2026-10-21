# -*- coding: utf-8 -*-
"""上传接口的错误。"""

from api.common.errors import AppError
from config import ALLOWED_FILE_TYPES


def invalid_filename() -> AppError:
    return AppError(400, "INVALID_FILENAME", "文件名不合法")


def invalid_fingerprint() -> AppError:
    return AppError(400, "INVALID_FINGERPRINT", "文件指纹不合法")


def invalid_size() -> AppError:
    return AppError(422, "INVALID_SIZE", "文件大小不合法")


def file_too_large() -> AppError:
    return AppError(413, "FILE_TOO_LARGE", "文件大小超出限制")


def unsupported_file_type() -> AppError:
    return AppError(
        415,
        "UNSUPPORTED_FILE_TYPE",
        "不支持的文件类型",
        {"allowed": sorted(ALLOWED_FILE_TYPES)},
    )


def invalid_total_parts() -> AppError:
    return AppError(422, "INVALID_TOTAL_PARTS", "分片数量超出限制")


def upload_not_found() -> AppError:
    return AppError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")


def fingerprint_conflict(upload_id: str) -> AppError:
    return AppError(
        409,
        "FINGERPRINT_CONFLICT",
        "同一指纹存在其他未完成上传",
        {"uploadId": upload_id},
    )


def upload_already_completed() -> AppError:
    return AppError(409, "UPLOAD_ALREADY_COMPLETED", "上传已完成")


def upload_completing() -> AppError:
    return AppError(409, "UPLOAD_COMPLETING", "上传正在完成")


def upload_aborting() -> AppError:
    return AppError(409, "UPLOAD_ABORTING", "上传正在取消")


def upload_failed() -> AppError:
    return AppError(409, "UPLOAD_FAILED", "上传已失败，请重新上传")


def upload_aborted() -> AppError:
    return AppError(410, "UPLOAD_ABORTED", "上传已取消")


def upload_expired() -> AppError:
    return AppError(410, "UPLOAD_EXPIRED", "上传已过期")


def length_required() -> AppError:
    return AppError(411, "LENGTH_REQUIRED", "缺少 Content-Length")


def invalid_part_number(max_part: int) -> AppError:
    return AppError(
        422,
        "INVALID_PART_NUMBER",
        "分片编号不合法",
        {"min": 1, "max": max_part},
    )


def invalid_part_size(expected: int, actual: int | None) -> AppError:
    return AppError(
        422,
        "INVALID_PART_SIZE",
        "分片大小与预期不符",
        {"expected": expected, "actual": actual},
    )


def part_too_large(expected: int) -> AppError:
    return AppError(
        413,
        "PART_TOO_LARGE",
        "分片超过预期大小",
        {"expected": expected},
    )


def upload_incomplete(missing_parts: list[int]) -> AppError:
    return AppError(
        409,
        "UPLOAD_INCOMPLETE",
        "还有分片未上传",
        {"missingParts": missing_parts},
    )
