# -*- coding: utf-8 -*-
"""PDF 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.adapters.mineru import MinerUClient
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser


class PdfParser(BaseParser):
    """PDF 拆分、MinerU 解析和分段结果合并。"""

    file_types = ("pdf",)

    def __init__(self, mineru: MinerUClient | None = None) -> None:
        self._mineru = mineru or MinerUClient()

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("PdfParser 尚未实现")
