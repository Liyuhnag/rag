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
