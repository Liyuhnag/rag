# -*- coding: utf-8 -*-
"""上传服务的业务错误。"""


class UploadError(Exception):
    """携带 HTTP 语义的上传业务错误。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def session_not_found() -> UploadError:
    return UploadError(404, "UPLOAD_NOT_FOUND", "上传会话不存在")


def upload_expired() -> UploadError:
    return UploadError(410, "UPLOAD_EXPIRED", "上传已过期")


def upload_completed() -> UploadError:
    return UploadError(409, "UPLOAD_ALREADY_COMPLETED", "上传已完成")


def upload_completing(message: str = "上传正在完成") -> UploadError:
    return UploadError(409, "UPLOAD_COMPLETING", message)


def upload_aborted() -> UploadError:
    return UploadError(410, "UPLOAD_ABORTED", "上传已取消")


def upload_failed() -> UploadError:
    return UploadError(409, "UPLOAD_FAILED", "上传已失败，请重新上传")


def upload_aborting() -> UploadError:
    return UploadError(409, "UPLOAD_ABORTING", "上传正在取消")


def upload_incomplete(
    message: str,
    details: dict[str, list[int]],
) -> UploadError:
    return UploadError(409, "UPLOAD_INCOMPLETE", message, details)
