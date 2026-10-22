# -*- coding: utf-8 -*-
"""文档解析服务的业务异常。"""


class ParsingError(Exception):
    """解析链路的可预期错误基类。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedFileType(ParsingError):
    """没有注册对应文件类型的解析器。"""


class ParserUnavailable(ParsingError):
    """解析器依赖或外部服务不可用。"""


class ParseFailed(ParsingError):
    """解析器执行失败。"""


class ChunkingFailed(ParsingError):
    """文本切分失败。"""
