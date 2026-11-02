# -*- coding: utf-8 -*-
"""文件解析器。"""

from services.indexing.parsing.parsers.base import BaseParser
from services.indexing.parsing.parsers.csv import CsvParser
from services.indexing.parsing.parsers.doc import DocParser
from services.indexing.parsing.parsers.docx import DocxParser
from services.indexing.parsing.parsers.markdown import MarkdownParser
from services.indexing.parsing.parsers.pdf import PdfParser
from services.indexing.parsing.parsers.registry import ParserRegistry
from services.indexing.parsing.parsers.registry import build_default_registry
from services.indexing.parsing.parsers.txt import TxtParser
from services.indexing.parsing.parsers.xlsx import XlsxParser

__all__ = [
    "BaseParser",
    "CsvParser",
    "DocParser",
    "DocxParser",
    "MarkdownParser",
    "ParserRegistry",
    "PdfParser",
    "TxtParser",
    "XlsxParser",
    "build_default_registry",
]
