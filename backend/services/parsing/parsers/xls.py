# -*- coding: utf-8 -*-
"""XLS 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.adapters.libreoffice import LibreOfficeConverter
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser
from services.parsing.parsers.xlsx import XlsxParser


class XlsParser(BaseParser):
    """旧 Excel 文档先转 XLSX，再走表格解析。"""

    file_types = ("xls",)

    def __init__(
        self,
        converter: LibreOfficeConverter | None = None,
        xlsx_parser: XlsxParser | None = None,
    ) -> None:
        self._converter = converter or LibreOfficeConverter()
        self._xlsx_parser = xlsx_parser or XlsxParser()

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("XlsParser 尚未实现")
