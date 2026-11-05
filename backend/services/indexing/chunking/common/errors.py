# -*- coding: utf-8 -*-
"""父子块切分的业务异常。"""


class ChunkingError(Exception):
    """切分链路的可预期错误基类。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ChunkingFailed(ChunkingError):
    """文本切分失败。"""
