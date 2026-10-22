# -*- coding: utf-8 -*-
"""解析器注册表。

使用注册表模式把文件扩展名映射到具体解析器。
"""

from collections.abc import Iterable

from services.parsing.errors import UnsupportedFileType
from services.parsing.parsers.base import BaseParser
from services.parsing.parsers.csv import CsvParser
from services.parsing.parsers.doc import DocParser
from services.parsing.parsers.docx import DocxParser
from services.parsing.parsers.markdown import MarkdownParser
from services.parsing.parsers.pdf import PdfParser
from services.parsing.parsers.txt import TxtParser
from services.parsing.parsers.xlsx import XlsxParser


class ParserRegistry:
    """按扩展名查找解析器。"""

    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {}

    def register(self, parser: BaseParser) -> None:
        """注册一个解析器；同一扩展名不能重复注册。"""
        for file_type in parser.file_types:
            if file_type in self._parsers:
                raise ValueError(f"解析器重复注册: {file_type}")
            self._parsers[file_type] = parser

    def register_all(self, parsers: Iterable[BaseParser]) -> None:
        """批量注册解析器。"""
        for parser in parsers:
            self.register(parser)

    def get(self, file_type: str) -> BaseParser:
        """按扩展名返回解析器；未知类型抛出 UnsupportedFileType。"""
        parser = self._parsers.get(file_type)
        if parser is None:
            raise UnsupportedFileType(f"没有注册文件类型: {file_type}")
        return parser

    def supports(self, file_type: str) -> bool:
        """判断是否存在对应解析器。"""
        return file_type in self._parsers


def build_default_registry() -> ParserRegistry:
    """构造默认解析器集合。"""
    registry = ParserRegistry()
    registry.register_all(
        [
            TxtParser(),
            MarkdownParser(),
            CsvParser(),
            XlsxParser(),
            PdfParser(),
            DocxParser(),
            DocParser(),
        ]
    )
    return registry
