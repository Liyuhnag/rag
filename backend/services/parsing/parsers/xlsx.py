# -*- coding: utf-8 -*-
"""XLSX 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser


class XlsxParser(BaseParser):
    """读取工作簿并生成结构化表格。"""

    file_types = ("xlsx",)

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("XlsxParser 尚未实现")
