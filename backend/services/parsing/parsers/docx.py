# -*- coding: utf-8 -*-
"""DOCX 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.adapters.libreoffice import LibreOfficeConverter
from services.parsing.adapters.mineru import MinerUClient
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser


class DocxParser(BaseParser):
    """优先直接解析 DOCX，不支持时转 PDF。"""

    file_types = ("docx",)

    def __init__(
        self,
        converter: LibreOfficeConverter | None = None,
        mineru: MinerUClient | None = None,
    ) -> None:
        self._converter = converter or LibreOfficeConverter()
        self._mineru = mineru or MinerUClient()

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("DocxParser 尚未实现")
