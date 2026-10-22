# -*- coding: utf-8 -*-
"""DOC 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.adapters.libreoffice import LibreOfficeConverter
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser
from services.parsing.parsers.pdf import PdfParser


class DocParser(BaseParser):
    """旧 Word 文档先转 PDF，再走 PDF 解析。"""

    file_types = ("doc",)

    def __init__(
        self,
        converter: LibreOfficeConverter | None = None,
        pdf_parser: PdfParser | None = None,
    ) -> None:
        self._converter = converter or LibreOfficeConverter()
        self._pdf_parser = pdf_parser or PdfParser()

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("DocParser 尚未实现")
