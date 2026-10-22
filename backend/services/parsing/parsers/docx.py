# -*- coding: utf-8 -*-
"""DOCX 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.adapters.mineru import MinerUClient
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser


class DocxParser(BaseParser):
    """把 DOCX 交给 MinerU 解析。"""

    file_types = ("docx",)

    def __init__(self, mineru: MinerUClient | None = None) -> None:
        self._mineru = mineru or MinerUClient()

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("DocxParser 尚未实现")
