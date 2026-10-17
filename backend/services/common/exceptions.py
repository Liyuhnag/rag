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
