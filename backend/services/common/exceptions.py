# -*- coding: utf-8 -*-
"""服务层可预期的失败。"""


class FeatureNotReady(Exception):
    """调用方请求了尚未接入的能力。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StorageError(Exception):
    """对象存储或数据库暂时不可用。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UploadNotFound(Exception):
    """Multipart Upload 已不存在。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DuplicateRecord(Exception):
    """唯一键冲突。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StateConflict(Exception):
    """上传会话状态已经被其他请求改变。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class FingerprintConflict(Exception):
    """同一指纹已有元数据不一致的上传会话。"""

    def __init__(self, upload_id: str) -> None:
        super().__init__("同一指纹存在其他未完成上传")
        self.upload_id = upload_id


class QueueFull(Exception):
    """等待中的任务已达到上限。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BrokerUnavailable(Exception):
    """任务队列暂时查不到。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
