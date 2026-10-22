# -*- coding: utf-8 -*-
"""文件解析器。"""

from services.parsing.parsers.base import BaseParser
from services.parsing.parsers.csv import CsvParser
from services.parsing.parsers.doc import DocParser
from services.parsing.parsers.docx import DocxParser
from services.parsing.parsers.markdown import MarkdownParser
from services.parsing.parsers.pdf import PdfParser
from services.parsing.parsers.registry import ParserRegistry
from services.parsing.parsers.registry import build_default_registry
from services.parsing.parsers.txt import TxtParser
from services.parsing.parsers.xls import XlsParser
from services.parsing.parsers.xlsx import XlsxParser

__all__ = [
    "BaseParser",
    "CsvParser",
    "DocParser",
    "DocxParser",
    "MarkdownParser",
    "ParserRegistry",
    "PdfParser",
    "TxtParser",
    "XlsParser",
    "XlsxParser",
    "build_default_registry",
]
