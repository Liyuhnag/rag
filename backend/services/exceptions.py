# -*- coding: utf-8 -*-
"""服务层可预期的失败。"""


class FeatureNotReady(Exception):
    """调用方请求了尚未接入的能力。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
