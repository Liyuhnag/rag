# -*- coding: utf-8 -*-
"""CSV 解析器。"""

from services.common.exceptions import FeatureNotReady
from services.parsing.models import ParseInput
from services.parsing.models import ParsedDocument
from services.parsing.parsers.base import BaseParser


class CsvParser(BaseParser):
    """把 CSV 转成表头和行记录。"""

    file_types = ("csv",)

    def _parse(self, source: ParseInput) -> ParsedDocument:
        raise FeatureNotReady("CsvParser 尚未实现")
